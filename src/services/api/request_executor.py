"""API Request Executor module.

Handles HTTP request execution with retry logic, rate limiting,
caching, and response processing for external API calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

import aiohttp

from services.cache.hash_service import UnifiedHashService

if TYPE_CHECKING:
    from core.models.protocols import CacheServiceProtocol
    from services.api.api_base import ApiRateLimiter


# Constants
WAIT_TIME_LOG_THRESHOLD = 0.1
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
API_RESPONSE_LOG_LIMIT = 500
SECURE_RANDOM = secrets.SystemRandom()
# Request headers safe to log; everything else (Authorization, cookies) is left out
LOGGABLE_REQUEST_HEADERS = ("User-Agent", "Accept", "Accept-Encoding", "Content-Type")
HTTP_NOT_FOUND = 404
MAX_RETRY_DELAY_SECONDS = 120.0


class ApiRequestError(Exception):
    """A provider request failed: no usable answer arrived within the retry budget.

    Distinct from a provider answering that nothing matches, which `execute_request` returns as None (HTTP 404) or
    as an answer without results. Derives from Exception so the clients' broad parsing handlers never swallow it.
    """

    def __init__(self, api_name: str, url: str, reason: str, *, status: int | None = None) -> None:
        super().__init__(f"[{api_name}] {reason}: {url}")
        self.api_name = api_name
        self.url = url
        self.status = status


class TransientRequestError(Exception):
    """A failure worth retrying: a timeout, a dropped connection, a rate limit or a server error."""

    def __init__(self, reason: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.status = status
        self.retry_after = retry_after


def _parse_retry_after(value: str | None) -> float | None:
    """Read the seconds of a Retry-After header in its delta-seconds form; an HTTP date or garbage gives None."""
    try:
        return max(float(value), 0.0) if value else None
    except ValueError:
        return None


class ApiRequestExecutor:
    """Executes HTTP requests with retry logic, rate limiting, and caching.

    Handles all low-level HTTP communication including:
    - Request preparation (headers, timeouts)
    - Rate limiting coordination
    - Retry with exponential backoff
    - Response parsing and validation
    - Cache integration

    Important:
        Session lifecycle is managed by ExternalApiOrchestrator, NOT here.
        This executor only holds a session reference set via set_session().
        It may clear this reference on errors (e.g., event loop closed), but
        it will NEVER close the session. Closing is the owner's responsibility.

    Args:
        cache_service: Cache service for storing/retrieving API responses
        rate_limiters: Dict mapping API names to rate limiters
        console_logger: Logger for info/debug messages
        error_logger: Logger for errors/warnings
        user_agent: User-Agent header for requests
        discogs_token: Discogs API authentication token
        cache_ttl_days: How long to cache API responses (days)
        default_max_retries: Default retry count for failed requests
        default_retry_delay: Base delay between retries (seconds)

    """

    def __init__(
        self,
        *,
        cache_service: CacheServiceProtocol,
        rate_limiters: dict[str, ApiRateLimiter],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        user_agent: str,
        discogs_token: str | None,
        cache_ttl_days: int,
        default_max_retries: int,
        default_retry_delay: float,
    ) -> None:
        self.cache_service = cache_service
        self.rate_limiters = rate_limiters
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.user_agent = user_agent
        self.discogs_token = discogs_token
        self.cache_ttl_days = cache_ttl_days
        self.default_max_retries = default_max_retries
        self.default_retry_delay = default_retry_delay

        # Session managed externally, set via set_session()
        self.session: aiohttp.ClientSession | None = None

        # Metrics - initialize with known API keys for backward compatibility
        self.request_counts: dict[str, int] = {
            "discogs": 0,
            "musicbrainz": 0,
            "itunes": 0,
        }
        self.api_call_durations: dict[str, list[float]] = {
            "discogs": [],
            "musicbrainz": [],
            "itunes": [],
        }

    def set_session(self, session: aiohttp.ClientSession | None) -> None:
        """Set the aiohttp session for making requests."""
        self.session = session

    async def execute_request(
        self,
        api_name: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers_override: dict[str, str] | None = None,
        max_retries: int | None = None,
        base_delay: float | None = None,
        timeout_override: float | None = None,
    ) -> dict[str, Any] | None:
        """Execute an API request with rate limiting, caching, and retry logic.

        Args:
            api_name: Name of the API (e.g., 'discogs', 'musicbrainz')
            url: Request URL
            params: Query parameters
            headers_override: Additional headers to merge
            max_retries: Override default retry count
            base_delay: Override default retry delay
            timeout_override: Override default timeout

        Returns:
            The parsed answer, or None when the provider says the resource does not exist (HTTP 404)

        Raises:
            ApiRequestError: The request failed after its retry budget, its answer was unusable, or it could not be
                prepared
        """
        # Debug logging for iTunes requests
        if api_name == "itunes":
            self.console_logger.debug(
                "[%s] Making API request to %s with params: %s",
                api_name,
                url,
                params,
            )

        # Build cache key and check cache first
        cache_key = self._build_cache_key(api_name, url, params)
        cached_result = await self._check_cache(cache_key, api_name, url)
        if cached_result is not None:
            if api_name == "itunes":
                self.console_logger.debug("[%s] Using cached result", api_name)
            return cached_result

        # Prepare request components
        prepared = self._prepare_request(api_name, url, headers_override, timeout_override)
        if prepared is None:
            raise ApiRequestError(api_name, self._build_log_url(url, params), "request could not be prepared")

        request_headers, limiter, request_timeout = prepared

        # Execute with retry
        retry_attempts = max_retries if isinstance(max_retries, int) and max_retries > 0 else self.default_max_retries
        retry_delay = base_delay if isinstance(base_delay, (int, float)) and base_delay >= 0 else self.default_retry_delay

        result = await self._execute_with_retry(
            api_name,
            url,
            params,
            request_headers=request_headers,
            request_timeout=request_timeout,
            limiter=limiter,
            max_retries=retry_attempts,
            base_delay=retry_delay,
        )

        # Debug logging for iTunes results
        if api_name == "itunes":
            self.console_logger.debug(
                "[%s] Request execution result: %s",
                api_name,
                "Success" if result is not None else "Not found",
            )

        # Cache the result
        await self._cache_result(cache_key, result)
        return result

    @staticmethod
    def _build_cache_key(
        api_name: str,
        url: str,
        params: dict[str, str] | None,
    ) -> str:
        """Build a deterministic cache key for the API request.

        Uses SHA-256 via UnifiedHashService instead of Python's built-in hash()
        to ensure cache keys remain stable across interpreter restarts.
        Python's hash() is randomized per-process (PYTHONHASHSEED) which would
        cause cache misses after every restart.
        """
        cache_key_data = {
            "type": "api_request",
            "api": api_name,
            "url": url,
            "params": sorted((params or {}).items()),
        }
        return f"api_request_{api_name}_{UnifiedHashService.hash_generic_key(cache_key_data)}"

    async def _check_cache(
        self,
        cache_key: str,
        api_name: str,
        url: str,
    ) -> dict[str, Any] | None:
        """Check cache for existing response.

        Args:
            cache_key: Cache key identifying the request
            api_name: Name of the API (e.g., 'discogs', 'musicbrainz')
            url: Request URL

        Returns:
            Cached response if valid, None if not cached or invalid
        """
        cached_response: Any = await self.cache_service.get_async(cache_key)
        if cached_response is None:
            return None

        if isinstance(cached_response, dict):
            if cached_response:
                self.console_logger.debug(
                    "Using cached response for %s request to %s",
                    api_name,
                    url,
                )
                return cached_response
            # Earlier versions stored a failed request as {}; ask the API again so those records heal
            return None

        self.console_logger.warning(
            "Unexpected cached response type for %s request to %s: %s",
            api_name,
            url,
            type(cached_response).__name__,
        )
        self.cache_service.invalidate(cache_key)
        return None

    async def _cache_result(
        self,
        cache_key: str,
        result: dict[str, Any] | None,
    ) -> None:
        """Cache the API response; a failed request (None) is not cached, so the next lookup asks the API again.

        An empty body is skipped as well, so a cached {} can only be a failure an earlier version stored.
        """
        if not result:
            return
        cache_ttl_seconds = self.cache_ttl_days * 86400
        await self.cache_service.set_async(cache_key, result, ttl=cache_ttl_seconds)

    def _prepare_request(
        self,
        api_name: str,
        url: str,
        headers_override: dict[str, str] | None,
        timeout_override: float | None,
    ) -> tuple[dict[str, str], ApiRateLimiter, aiohttp.ClientTimeout] | None:
        """Prepare request headers, rate limiter, and timeout.

        Args:
            api_name: Name of the API (e.g., 'discogs', 'musicbrainz')
            url: Request URL
            headers_override: Additional headers to merge
            timeout_override: Override default timeout

        Returns:
            Tuple of (headers, limiter, timeout) or None if preparation failed
        """
        # Ensure session is available
        if self.session is None or self.session.closed:
            self.error_logger.error(
                "[%s] Session not available for request to %s. Initialize method was not called or failed.",
                api_name,
                url,
            )
            return None

        # Setup request headers
        request_headers: dict[str, str] = {str(name): str(value) for name, value in self.session.headers.items()}
        if api_name == "discogs":
            if not self.discogs_token:
                self.error_logger.error("Discogs token is missing or could not be loaded")
                return None
            request_headers["Authorization"] = f"Discogs token={self.discogs_token}"
            if "User-Agent" not in request_headers:
                request_headers["User-Agent"] = self.user_agent

        if headers_override:
            request_headers |= headers_override

        # Get rate limiter
        limiter = self.rate_limiters.get(api_name)
        if not limiter:
            self.error_logger.error(
                "No rate limiter configured for API: %s",
                api_name,
            )
            return None

        # Setup request timeout
        request_timeout = aiohttp.ClientTimeout(total=timeout_override) if timeout_override else self.session.timeout

        return request_headers, limiter, request_timeout

    async def _execute_with_retry(
        self,
        api_name: str,
        url: str,
        params: dict[str, str] | None,
        *,
        request_headers: dict[str, str],
        request_timeout: aiohttp.ClientTimeout,
        limiter: ApiRateLimiter,
        max_retries: int,
        base_delay: float,
    ) -> dict[str, Any] | None:
        """Send the request, retrying transient failures with backoff until the budget is spent.

        Args:
            api_name: Provider key for limits, logs and errors
            url: Endpoint without the query
            params: Query parameters
            request_headers: Headers to send
            request_timeout: Timeout for one attempt
            limiter: Provider rate limiter
            max_retries: Retries allowed after the first attempt
            base_delay: Backoff base in seconds

        Returns:
            The parsed answer, or None for HTTP 404

        Raises:
            ApiRequestError: A permanent failure, or a transient one that outlasted the retry budget
        """
        log_url = self._build_log_url(url, params)
        attempt = 0
        while True:
            try:
                return await self._attempt_request(
                    api_name,
                    url,
                    params,
                    request_headers=request_headers,
                    request_timeout=request_timeout,
                    limiter=limiter,
                    attempt=attempt,
                    log_url=log_url,
                )
            except TransientRequestError as failure:
                if attempt >= max_retries:
                    self.error_logger.exception("[%s] Request failed after %d attempts: %s (%s)", api_name, attempt + 1, log_url, failure)
                    raise ApiRequestError(api_name, log_url, f"failed after {attempt + 1} attempts ({failure})", status=failure.status) from (
                        failure.__cause__ or failure
                    )
                delay = self._retry_delay(attempt, base_delay, failure.retry_after)
                self.console_logger.warning("[%s] %s, retrying %d/%d in %.2fs", api_name, failure, attempt + 1, max_retries, delay)
                await asyncio.sleep(delay)
                attempt += 1

    @staticmethod
    def _retry_delay(attempt: int, base_delay: float, retry_after: float | None) -> float:
        """Wait what the provider asked for, or back off exponentially with jitter, capped at two minutes."""
        if retry_after is not None:
            return min(retry_after, MAX_RETRY_DELAY_SECONDS)
        return min(base_delay * (2**attempt) * (0.8 + SECURE_RANDOM.random() * 0.4), MAX_RETRY_DELAY_SECONDS)

    @staticmethod
    def _build_log_url(url: str, params: dict[str, str] | None) -> str:
        """Build URL string for logging purposes."""
        return url + (f"?{urllib.parse.urlencode(params or {}, safe=':/')}" if params else "")

    async def _attempt_request(
        self,
        api_name: str,
        url: str,
        params: dict[str, str] | None,
        *,
        request_headers: dict[str, str],
        request_timeout: aiohttp.ClientTimeout,
        limiter: ApiRateLimiter,
        attempt: int,
        log_url: str,
    ) -> dict[str, Any] | None:
        """Send one attempt and sort its exceptions into transient failures and permanent ones.

        Args:
            api_name: Provider key for limits, logs and errors
            url: Endpoint without the query
            params: Query parameters
            request_headers: Headers to send
            request_timeout: Timeout for one attempt
            limiter: Provider rate limiter
            attempt: Zero-based attempt number
            log_url: URL with its query, for logs and errors

        Returns:
            The parsed answer, or None for HTTP 404

        Raises:
            TransientRequestError: A timeout, a dropped connection, a closed event loop, a rate limit or a server error
            ApiRequestError: Any other failure; retrying would not change it
        """
        try:
            return await self._execute_single_request(
                api_name,
                url,
                params,
                request_headers=request_headers,
                request_timeout=request_timeout,
                limiter=limiter,
                attempt=attempt,
                log_url=log_url,
            )
        except (TransientRequestError, ApiRequestError):
            raise
        except (TimeoutError, aiohttp.ClientConnectionError) as error:
            self.api_call_durations.setdefault(api_name, []).append(0.0)
            raise TransientRequestError(type(error).__name__) from error
        except RuntimeError as error:
            if "Event loop is closed" in str(error):
                # Clear the reference only; ExternalApiOrchestrator owns the session's lifecycle
                self.session = None
                reason = "event loop closed"
                raise TransientRequestError(reason) from error
            self.error_logger.exception("[%s] Request to %s failed", api_name, log_url)
            raise ApiRequestError(api_name, log_url, f"{type(error).__name__}: {error}") from error
        except (aiohttp.ClientError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.error_logger.exception("[%s] Unexpected error requesting %s", api_name, log_url)
            raise ApiRequestError(api_name, log_url, f"{type(error).__name__}: {error}") from error

    async def _execute_single_request(
        self,
        api_name: str,
        url: str,
        params: dict[str, str] | None,
        *,
        request_headers: dict[str, str],
        request_timeout: aiohttp.ClientTimeout,
        limiter: ApiRateLimiter,
        attempt: int,
        log_url: str,
    ) -> dict[str, Any] | None:
        """Perform a single request attempt.

        Args:
            api_name: Name of the API (e.g., 'discogs', 'musicbrainz')
            url: Request URL
            params: Query parameters
            request_headers: Prepared request headers
            request_timeout: Request timeout configuration
            limiter: Rate limiter for the API
            attempt: Current retry attempt number (0-indexed)
            log_url: URL string for logging purposes

        Returns:
            Response dict if successful, None if should retry,
            raises exception if failed

        Raises:
            RuntimeError: If the session is lost before making the request.
        """
        start_time = time.monotonic()
        acquired = False

        try:
            self._ensure_session()
            wait_time = await limiter.acquire()
            acquired = True
            if wait_time > WAIT_TIME_LOG_THRESHOLD:
                self.console_logger.debug(
                    "[%s] Waited %.3fs for rate limiting",
                    api_name,
                    wait_time,
                )

            self.request_counts[api_name] = self.request_counts.get(api_name, 0) + 1

            self._ensure_session()
            if self.session is None:  # type narrowing — _ensure_session() raises if unavailable
                msg = f"HTTP session lost before {api_name} request to {log_url}"
                raise RuntimeError(msg)

            # iTunes API requires %20 for spaces, not + (which aiohttp uses by default)
            if api_name == "itunes" and params:
                request_url = self._encode_url_for_itunes(url, params)
                request_params = None  # Already encoded in URL
            else:
                request_url = url
                request_params = params

            async with self.session.get(
                request_url,
                params=request_params,
                headers=request_headers,
                timeout=request_timeout,
            ) as response:
                elapsed = time.monotonic() - start_time
                self.api_call_durations[api_name].append(elapsed)

                return await self._process_response(
                    response,
                    api_name=api_name,
                    attempt=attempt,
                    log_url=log_url,
                    elapsed=elapsed,
                )

        finally:
            if acquired:
                limiter.release()

    def _ensure_session(self) -> None:
        """Ensure session is available, raise if not."""
        if self.session is None or self.session.closed:
            msg = "HTTP session not initialized or closed"
            raise RuntimeError(msg)

    @staticmethod
    def _encode_url_for_itunes(url: str, params: dict[str, str] | None) -> str:
        """Encode URL with %20 for spaces (required by iTunes API).

        iTunes API doesn't recognize '+' as space, requires '%20'.
        Standard aiohttp uses '+' which causes 0 results for queries with spaces.
        """
        if not params:
            return url
        # Use quote_via=urllib.parse.quote to get %20 instead of +
        encoded = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        return f"{url}?{encoded}"

    async def _process_response(
        self,
        response: aiohttp.ClientResponse,
        *,
        api_name: str,
        attempt: int,
        log_url: str,
        elapsed: float,
    ) -> dict[str, Any] | None:
        """Process HTTP response and determine the next action.

        Args:
            response: The HTTP response to process
            api_name: Name of the API (e.g., 'discogs', 'musicbrainz')
            attempt: Current retry attempt number (0-indexed)
            log_url: URL string for logging purposes
            elapsed: Time elapsed for the request, in seconds

        Returns:
            The parsed answer, or None for HTTP 404

        Raises:
            TransientRequestError: A rate limit or a server error
            ApiRequestError: Any other error status, or an answer that is not a JSON object
        """
        response_status = response.status

        if api_name == "discogs":
            # Allowlist rather than redact: any header not listed (Authorization, cookies) never reaches the log
            sent_headers = response.request_info.headers
            self.console_logger.debug(
                "[discogs] Sending Headers: %s",
                {name: sent_headers[name] for name in LOGGABLE_REQUEST_HEADERS if name in sent_headers},
            )

        # Read response text
        response_text_snippet = await self._read_response_text(response, api_name)

        self.console_logger.debug(
            "[%s] Request (Attempt %d): %s - Status: %d (%.3fs)",
            api_name,
            attempt + 1,
            log_url,
            response_status,
            elapsed,
        )

        # A 404 is the provider saying the resource does not exist: an answer, not a failure
        if response_status == HTTP_NOT_FOUND:
            self.console_logger.debug("[%s] Not found: %s", api_name, log_url)
            return None

        if response_status == HTTP_TOO_MANY_REQUESTS or response_status >= HTTP_SERVER_ERROR:
            reason = f"HTTP {response_status}"
            raise TransientRequestError(
                reason,
                status=response_status,
                retry_after=_parse_retry_after(response.headers.get("Retry-After")),
            )

        if not response.ok:
            self.error_logger.warning(
                "[%s] API request failed with status %d. URL: %s. Snippet: %s",
                api_name,
                response_status,
                log_url,
                response_text_snippet,
            )
            raise ApiRequestError(api_name, log_url, f"HTTP {response_status}", status=response_status)

        # Process successful response
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type or (api_name == "itunes" and "text/javascript" in content_type):
            return await self._parse_json_response(response, api_name, log_url, response_text_snippet)

        self.error_logger.warning(
            "[%s] Received non-JSON response from %s. Content-Type: %s",
            api_name,
            log_url,
            content_type,
        )
        raise ApiRequestError(api_name, log_url, f"non-JSON answer ({content_type})")

    async def _read_response_text(
        self,
        response: aiohttp.ClientResponse,
        api_name: str,
    ) -> str:
        """Read and log the response text snippet."""
        try:
            raw_text: str = await response.text(encoding="utf-8", errors="ignore")
            text_snippet: str = raw_text[:API_RESPONSE_LOG_LIMIT]

            if self.console_logger.isEnabledFor(logging.DEBUG):
                self._log_response_debug(api_name, response.status, text_snippet)

            return text_snippet
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            self.error_logger.warning(
                "[%s] Failed to read response body: %s",
                api_name,
                e,
            )
            return f"[Error Reading Response: {e}]"

    def _log_response_debug(
        self,
        api_name: str,
        status: int,
        text_snippet: str,
    ) -> None:
        """Log API response for debugging."""
        self.console_logger.debug(
            "%s raw response (status=%d): %s",
            api_name.upper(),
            status,
            text_snippet,
        )

    async def _parse_json_response(
        self,
        response: aiohttp.ClientResponse,
        api_name: str,
        url: str,
        snippet: str,
    ) -> dict[str, Any]:
        """Parse the answer as a JSON object.

        Args:
            response: The HTTP response
            api_name: Provider key for logs and errors
            url: URL with its query, for logs and errors
            snippet: Start of the body, for logs

        Returns:
            The JSON object

        Raises:
            ApiRequestError: The body is not JSON, or is JSON but not an object
        """
        try:
            data = await response.json()
        except aiohttp.ContentTypeError as cte:
            return await self._handle_content_type_error(response, api_name, url, snippet, cte)
        except json.JSONDecodeError as error:
            self.error_logger.exception(
                "[%s] Error parsing JSON response from %s. Snippet: %s",
                api_name,
                url,
                snippet[:200],
            )
            raise ApiRequestError(api_name, url, "malformed JSON") from error
        if isinstance(data, dict):
            return data
        self.error_logger.warning(
            "[%s] JSON response is not a dict (type: %s) from %s. Snippet: %s",
            api_name,
            type(data).__name__,
            url,
            str(data)[:200],
        )
        raise ApiRequestError(api_name, url, f"JSON {type(data).__name__} instead of an object")

    async def _handle_content_type_error(
        self,
        response: aiohttp.ClientResponse,
        api_name: str,
        url: str,
        snippet: str,
        error: aiohttp.ContentTypeError,
    ) -> dict[str, Any]:
        """Parse an iTunes answer served as text/javascript; for any other API a wrong content type is a failure.

        Args:
            response: The HTTP response
            api_name: Provider key for logs and errors
            url: URL with its query, for logs and errors
            snippet: Start of the body, for logs
            error: The content-type error aiohttp raised

        Returns:
            The JSON object

        Raises:
            ApiRequestError: The answer cannot be read as a JSON object
        """
        self.console_logger.debug(
            "[%s] ContentTypeError caught: %s from %s",
            api_name,
            error,
            url,
        )

        if api_name != "itunes":
            self.error_logger.exception(
                "[%s] Content type error parsing JSON response from %s. Snippet: %s",
                api_name,
                url,
                snippet[:200],
            )
            raise ApiRequestError(api_name, url, "unexpected content type") from error

        # iTunes API returns text/javascript but content is JSON
        self.console_logger.debug(
            "[%s] Attempting manual JSON parsing for iTunes",
            api_name,
        )
        try:
            text_content = await response.text()
            self.console_logger.debug(
                "[%s] Retrieved text content (%d chars) from iTunes API",
                api_name,
                len(text_content),
            )
            data = json.loads(text_content)
            if isinstance(data, dict):
                self.console_logger.debug(
                    "[%s] Successfully parsed iTunes JSON: %d results",
                    api_name,
                    data.get("resultCount", 0),
                )
                return data
            self.error_logger.warning(
                "[%s] Parsed JSON is not a dict (type: %s) from %s",
                api_name,
                type(data).__name__,
                url,
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as parse_error:
            self.error_logger.exception(
                "[%s] Error parsing iTunes JSON response from %s. Snippet: %s",
                api_name,
                url,
                snippet[:200],
            )
            raise ApiRequestError(api_name, url, "malformed JSON") from parse_error
        raise ApiRequestError(api_name, url, "JSON is not an object")
