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

if TYPE_CHECKING:
    from core.models.protocols import CacheServiceProtocol
    from services.api.api_base import EnhancedRateLimiter


# Constants
WAIT_TIME_LOG_THRESHOLD = 0.1
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
API_RESPONSE_LOG_LIMIT = 500
SECURE_RANDOM = secrets.SystemRandom()


class ApiRequestExecutor:
    """Executes HTTP requests with retry logic, rate limiting, and caching.

    Handles all low-level HTTP communication including:
    - Request preparation (headers, timeouts)
    - Rate limiting coordination
    - Retry with exponential backoff
    - Response parsing and validation
    - Cache integration
    """

    def __init__(
        self,
        *,
        cache_service: CacheServiceProtocol,
        rate_limiters: dict[str, EnhancedRateLimiter],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        user_agent: str,
        discogs_token: str | None,
        cache_ttl_days: int,
        default_max_retries: int,
        default_retry_delay: float,
    ) -> None:
        """Initialize the API request executor.

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
            "lastfm": 0,
            "itunes": 0,
        }
        self.api_call_durations: dict[str, list[float]] = {
            "discogs": [],
            "musicbrainz": [],
            "lastfm": [],
            "itunes": [],
        }

    def set_session(self, session: aiohttp.ClientSession | None) -> None:
        """Set the aiohttp session for making requests."""
        self.session = session

    async def execute_request(
        self,
        api_name: str,
        url: str,
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
            Parsed JSON response dict, or None if request failed
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
            return None

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
                "Success" if result is not None else "Failed/None",
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
        """Build a cache key for the API request."""
        cache_key_tuple = (
            "api_request",
            api_name,
            url,
            tuple(sorted((params or {}).items())),
        )
        return f"{cache_key_tuple[0]}_{cache_key_tuple[1]}_{hash(cache_key_tuple)}"

    async def _check_cache(
        self,
        cache_key: str,
        api_name: str,
        url: str,
    ) -> dict[str, Any] | None:
        """Check cache for existing response.

        Returns:
            Cached response if valid, None if not cached or invalid
        """
        cached_response: Any = await self.cache_service.get_async(cache_key)
        if cached_response is None:
            return None

        if isinstance(cached_response, dict):
            if cached_response != {}:
                self.console_logger.debug(
                    "Using cached response for %s request to %s",
                    api_name,
                    url,
                )
                return cached_response
            self.console_logger.debug(
                "Cached empty response for %s request to %s",
                api_name,
                url,
            )
            return {}  # Return empty dict to signal "no result but cached"

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
        """Cache the API response."""
        cache_ttl_seconds = self.cache_ttl_days * 86400
        await self.cache_service.set_async(
            cache_key,
            result if result is not None else {},
            ttl=cache_ttl_seconds,
        )

    def _prepare_request(
        self,
        api_name: str,
        url: str,
        headers_override: dict[str, str] | None,
        timeout_override: float | None,
    ) -> tuple[dict[str, str], EnhancedRateLimiter, aiohttp.ClientTimeout] | None:
        """Prepare request headers, rate limiter, and timeout.

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
        request_headers = dict(self.session.headers)
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
        limiter: EnhancedRateLimiter,
        max_retries: int,
        base_delay: float,
    ) -> dict[str, Any] | None:
        """Execute a request with retry logic."""
        log_url = self._build_log_url(url, params)

        for attempt in range(max_retries + 1):
            result = await self._attempt_request(
                api_name,
                url,
                params,
                request_headers=request_headers,
                request_timeout=request_timeout,
                limiter=limiter,
                attempt=attempt,
                log_url=log_url,
                max_retries=max_retries,
                base_delay=base_delay,
            )
            if result is not None:
                return result

        return None

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
        limiter: EnhancedRateLimiter,
        attempt: int,
        log_url: str,
        max_retries: int,
        base_delay: float,
    ) -> dict[str, Any] | None:
        """Attempt a single request with exception handling."""
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
        except RuntimeError as rt:
            return await self._handle_runtime_error(rt, api_name, attempt, max_retries, url)
        except (TimeoutError, aiohttp.ClientError) as e:
            return await self._handle_client_error(e, api_name, attempt, max_retries, base_delay, url)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            self._handle_unexpected_error(e, api_name, url)
            return None

    async def _execute_single_request(
        self,
        api_name: str,
        url: str,
        params: dict[str, str] | None,
        *,
        request_headers: dict[str, str],
        request_timeout: aiohttp.ClientTimeout,
        limiter: EnhancedRateLimiter,
        attempt: int,
        log_url: str,
    ) -> dict[str, Any] | None:
        """Perform a single request attempt.

        Returns:
            Response dict if successful, None if should retry,
            raises exception if failed
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
            assert self.session is not None  # _ensure_session() guarantees this
            async with self.session.get(
                url,
                params=params,
                headers=request_headers,
                timeout=request_timeout,
            ) as response:
                elapsed = time.monotonic() - start_time
                self.api_call_durations[api_name].append(elapsed)

                return await self._process_response(response, api_name, url, attempt, log_url, elapsed)

        finally:
            if acquired:
                limiter.release()

    def _ensure_session(self) -> None:
        """Ensure session is available, raise if not."""
        if self.session is None or self.session.closed:
            msg = "HTTP session not initialized or closed"
            raise RuntimeError(msg)

    async def _process_response(
        self,
        response: aiohttp.ClientResponse,
        api_name: str,
        url: str,
        attempt: int,
        log_url: str,
        elapsed: float,
    ) -> dict[str, Any] | None:
        """Process HTTP response and determine the next action.

        Returns:
            Response dict if successful, None if should retry,
            raises exception if failed
        """
        response_status = response.status

        if api_name == "discogs":
            self.console_logger.debug(
                "[discogs] Sending Headers: %s",
                response.request_info.headers,
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

        # Handle rate limiting and server errors
        if response_status == HTTP_TOO_MANY_REQUESTS or response_status >= HTTP_SERVER_ERROR:
            raise self._create_response_error(
                response=response,
                status=response_status,
                message=response_text_snippet,
            )

        if not response.ok:
            self.error_logger.warning(
                "[%s] API request failed with status %d. URL: %s. Snippet: %s",
                api_name,
                response_status,
                url,
                response_text_snippet,
            )
            raise self._create_response_error(
                response=response,
                status=response_status,
                message=response_text_snippet,
            )

        # Process successful response
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type or (api_name == "itunes" and "text/javascript" in content_type):
            return await self._parse_json_response(response, api_name, url, response_text_snippet)

        self.error_logger.warning(
            "[%s] Received non-JSON response from %s. Content-Type: %s",
            api_name,
            url,
            content_type,
        )
        return None

    @staticmethod
    def _create_response_error(
        response: aiohttp.ClientResponse,
        status: int,
        message: str,
    ) -> aiohttp.ClientResponseError:
        """Create ClientResponseError with proper type handling."""
        # noinspection PyTypeChecker
        return aiohttp.ClientResponseError(
            request_info=response.request_info,
            history=response.history,
            status=status,
            message=message,
        )

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
            "====== %s RAW RESPONSE (Status: %d) ======",
            api_name.upper(),
            status,
        )
        self.console_logger.debug(text_snippet)
        self.console_logger.debug("====== END %s RAW RESPONSE ======", api_name.upper())

    async def _handle_runtime_error(
        self,
        exception: RuntimeError,
        api_name: str,
        attempt: int,
        max_retries: int,
        url: str,
    ) -> dict[str, Any] | None:
        """Handle RuntimeError exceptions."""
        if "Event loop is closed" not in str(exception) or attempt >= max_retries:
            self._log_final_failure(api_name, url, exception)
            return None

        self.error_logger.exception(
            "[%s] Event loop is closed. Recreating ClientSession and retrying %d/%d",
            api_name,
            attempt + 1,
            max_retries,
        )
        try:
            if self.session is not None and not self.session.closed:
                await self.session.close()
        except (aiohttp.ClientError, TimeoutError, RuntimeError):
            self.error_logger.exception("Error closing existing session")

        self.session = None
        # Caller must handle session recreation
        return None

    async def _handle_client_error(
        self,
        exception: TimeoutError | aiohttp.ClientError,
        api_name: str,
        attempt: int,
        max_retries: int,
        base_delay: float,
        url: str,
    ) -> dict[str, Any] | None:
        """Handle client timeout and connection errors."""
        # Track elapsed time for failed requests
        self.api_call_durations[api_name].append(0.0)

        retryable_errors = (
            aiohttp.ClientConnectorError,
            aiohttp.ServerDisconnectedError,
            asyncio.TimeoutError,
        )

        if attempt >= max_retries or not isinstance(exception, retryable_errors):
            self.error_logger.exception(
                "[%s] Request failed after %d attempts",
                api_name,
                attempt + 1,
            )
            self._log_final_failure(api_name, url, exception)
            return None

        max_delay = 120.0  # Cap to prevent excessively long waits (2 minutes)
        delay = min(base_delay * (2**attempt) * (0.8 + SECURE_RANDOM.random() * 0.4), max_delay)

        if delay > 15.0:
            self.console_logger.info(
                "[%s] Long retry delay: waiting %.1fs before attempt %d/%d",
                api_name,
                delay,
                attempt + 2,
                max_retries + 1,
            )

        self.console_logger.warning(
            "[%s] %s, retrying %d/%d in %.2fs",
            api_name,
            type(exception).__name__,
            attempt + 1,
            max_retries,
            delay,
        )
        await asyncio.sleep(delay)
        return None

    def _handle_unexpected_error(
        self,
        exception: Exception,
        api_name: str,
        url: str,
    ) -> None:
        """Handle unexpected exceptions."""
        self.error_logger.exception(
            "[%s] Unexpected error making request to %s",
            api_name,
            url,
        )
        self._log_final_failure(api_name, url, exception)

    def _log_final_failure(
        self,
        api_name: str,
        url: str,
        exception: Exception,
    ) -> None:
        """Log the final failure after all retries exhausted."""
        self.error_logger.error(
            "[%s] Request failed for URL: %s. Last exception: %s",
            api_name,
            url,
            exception,
        )

    async def _parse_json_response(
        self,
        response: aiohttp.ClientResponse,
        api_name: str,
        url: str,
        snippet: str,
    ) -> dict[str, Any] | None:
        """Parse JSON response and ensure it is a dict."""
        try:
            data = await response.json()
            if isinstance(data, dict):
                return data
            self.error_logger.warning(
                "[%s] JSON response is not a dict (type: %s) from %s. Snippet: %s",
                api_name,
                type(data).__name__,
                url,
                str(data)[:200],
            )
        except aiohttp.ContentTypeError as cte:
            return await self._handle_content_type_error(response, api_name, url, snippet, cte)
        except json.JSONDecodeError:
            self.error_logger.exception(
                "[%s] Error parsing JSON response from %s. Snippet: %s",
                api_name,
                url,
                snippet[:200],
            )
        return None

    async def _handle_content_type_error(
        self,
        response: aiohttp.ClientResponse,
        api_name: str,
        url: str,
        snippet: str,
        error: aiohttp.ContentTypeError,
    ) -> dict[str, Any] | None:
        """Handle ContentTypeError, especially for iTunes API."""
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
            return None

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
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.error_logger.exception(
                "[%s] Error parsing iTunes JSON response from %s. Snippet: %s",
                api_name,
                url,
                snippet[:200],
            )
        return None
