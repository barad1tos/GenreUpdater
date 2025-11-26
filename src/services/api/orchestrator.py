"""External API Service Orchestrator.

This module provides the main coordination layer for fetching album release years
from multiple API providers (MusicBrainz, Discogs, Last.fm). It replaces the
legacy external API service with a modular architecture that maintains
backward compatibility while providing better separation of concerns.

The orchestrator handles:
- HTTP session management and connection pooling
- Rate limiting coordination across all API providers
- Request caching and response aggregation
- Dependency injection for cache and verification services
- Authentication token management with encryption support
- Release year determination using the sophisticated scoring algorithm
"""

import asyncio
import contextlib
import json
import logging
import os
import ssl
import time
import urllib.parse
from collections import defaultdict
from collections.abc import Coroutine
from datetime import UTC
from datetime import datetime as dt
from typing import Any, NoReturn, TypedDict

import aiohttp
import certifi

from src.services.api.applemusic import AppleMusicClient
from src.services.api.base import EnhancedRateLimiter, ScoredRelease
from src.services.api.discogs import DiscogsClient
from src.services.api.lastfm import LastFmClient
from src.services.api.musicbrainz import MusicBrainzClient
from src.services.api.scoring import ArtistPeriodContext, create_release_scorer
from src.services.cache.orchestrator import CacheOrchestrator
from src.services.pending import PendingVerificationService
from src.types.cryptography.secure_config import SecureConfig, SecurityConfigError
from src.core.debug import debug
from src.core.models.script_detection import ScriptType, detect_primary_script
from src.core.models.validators import is_valid_year
from src.metrics import Analytics


def normalize_name(name: str) -> str:
    """Normalize artist/album name for matching. Currently returns unchanged.

    TODO: Implement normalization when needed (lowercase, & → and, remove punctuation).
    """
    return name


# Constants from an original implementation
WAIT_TIME_LOG_THRESHOLD = 0.1
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500
MAX_LOGGED_YEARS = 5
YEAR_LENGTH = 4
API_RESPONSE_LOG_LIMIT = 500  # Unified limit for all API response logging
ACTIVITY_PERIOD_TUPLE_LENGTH = 2  # Expected length for activity period tuple
VERY_HIGH_SCORE_THRESHOLD = 75  # Score threshold for automatic acceptance
MIN_REISSUE_YEAR_DIFFERENCE = 2  # Minimum years between original and reissue to prefer original
MIN_YEAR_GAP_FOR_REISSUE_DETECTION = 4  # Minimum year gap to detect potential reissue scenarios
MAX_SUSPICIOUS_YEAR_DIFFERENCE = 3  # Maximum years difference before considering year suspicious
MIN_CONFIDENT_SCORE_THRESHOLD = 85  # Minimum score to consider result confident
SECURE_RANDOM = __import__("random").SystemRandom()


# Type definitions for structured data
class HTTPHeaders(TypedDict):
    """HTTP headers for API requests."""

    User_Agent: str  # Note: TypedDict uses underscore for hyphenated keys
    Accept: str
    Accept_Encoding: str


class JSONResponse(TypedDict, total=False):
    """Generic JSON API response structure."""

    resultCount: int
    results: list[dict[str, Any]]


class ScoreThresholds(TypedDict):
    """Score threshold indicators for year determination."""

    high_score_met: bool
    very_high_score: bool


class ExternalApiOrchestrator:
    """External API service orchestrator.

    Coordinates API calls across multiple providers (MusicBrainz, Discogs, Last.fm)
    to determine the original release year for music albums. Provides rate limiting,
    caching, authentication, and sophisticated scoring to identify the most likely
    original release.

    This class implements a modular architecture for external API services,
    providing unified access to MusicBrainz, Last.fm, and Discogs APIs.

    Attributes:
        config: Configuration dictionary
        console_logger: Logger for general output
        error_logger: Logger for errors and warnings
        cache_service: Service for caching API responses
        pending_verification_service: Service for managing verification queue
        session: HTTP session for API requests
        rate_limiters: Rate limiters for each API provider
        scoring_config: Configuration for release scoring algorithm
        release_scorer: Scorer for evaluating release candidates

    """

    # Class constants
    _SUSPICIOUS_CURRENT_YEAR_MSG = "Rejecting suspicious current_library_year=%s (matches system year) for '%s - %s'"

    @staticmethod
    def _coerce_non_negative_int(value: Any, default: int) -> int:
        """Convert value to a non-negative integer with fallback."""
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return default
        return candidate if candidate >= 0 else default

    @staticmethod
    def _coerce_positive_int(value: Any, default: int) -> int:
        """Convert value to a positive integer with fallback."""
        result = ExternalApiOrchestrator._coerce_non_negative_int(value, default)
        return result if result > 0 else default

    @staticmethod
    def _coerce_non_negative_float(value: Any, default: float) -> float:
        """Convert value to a non-negative float with fallback."""
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            return default
        return candidate if candidate >= 0 else default

    @staticmethod
    def _normalize_api_name(api_name: Any) -> str:
        """Normalize API name aliases to orchestrator-internal identifiers."""
        name = str(api_name).strip().lower()
        if name in {"applemusic", "itunes"}:
            return "itunes"
        if name not in {"musicbrainz", "discogs", "itunes", "lastfm"}:
            return "musicbrainz"
        return name

    def _apply_preferred_order(self, api_list: list[str]) -> list[str]:
        """Apply preferred API ordering to a list of API identifiers."""
        normalized: list[str] = [self._normalize_api_name(api) for api in api_list]
        if self.preferred_api in normalized:
            normalized.remove(self.preferred_api)
            normalized.insert(0, self.preferred_api)
        # Deduplicate while preserving order
        seen: set[str] = set()
        ordered: list[str] = []
        for api in normalized:
            if api not in seen:
                ordered.append(api)
                seen.add(api)
        return ordered

    def __init__(
        self,
        config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics: Analytics,
        cache_service: CacheOrchestrator,
        pending_verification_service: PendingVerificationService,
    ) -> None:
        """Initialize the API orchestrator with configuration, loggers, and dependencies."""
        self.config = config
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.analytics = analytics
        self.session: aiohttp.ClientSession | None = None

        # Store injected dependencies
        self.cache_service = cache_service
        self.pending_verification_service = pending_verification_service

        # Initialize pending tasks for fire-and-forget async operations
        self._pending_tasks: set[asyncio.Task[Any]] = set()

        # Initialize artist period context for release scoring
        self.artist_period_context: ArtistPeriodContext | None = None

        # Initialize API client references (will be set in _initialize_api_clients)
        self.discogs_client: DiscogsClient
        self.musicbrainz_client: MusicBrainzClient
        self.lastfm_client: LastFmClient
        self.applemusic_client: AppleMusicClient

        # Initialize SecureConfig for encrypted token storage
        self.secure_config: SecureConfig | None = None
        try:
            self.secure_config = SecureConfig(logger=self.error_logger)
            self.console_logger.debug("SecureConfig initialized for encrypted token storage")
        except SecurityConfigError as e:
            self.error_logger.warning("Failed to initialize SecureConfig: %s", e)
            self.secure_config = None

        # Extract and validate configuration
        self._extract_configuration()

        # Initialize rate limiters
        self._initialize_rate_limiters()

        # Initialize the scoring system first (needed for API client injection)
        self._initialize_scoring_system()

        # Initialize statistics tracking
        self.request_counts = {"discogs": 0, "musicbrainz": 0, "lastfm": 0, "itunes": 0}
        self.api_call_durations: dict[str, list[float]] = {
            "discogs": [],
            "musicbrainz": [],
            "lastfm": [],
            "itunes": [],
        }

        # Initialize state flag
        self._initialized = False

    def _validate_config_section(self, config: dict[str, Any] | None, error_message: str) -> dict[str, Any]:
        """Validate that a configuration section is a dictionary.

        Args:
            config: Configuration value to validate
            error_message: Error message to use if validation fails

        Returns:
            The validated configuration dictionary

        Raises:
            TypeError: If config is not a dictionary

        """
        if not isinstance(config, dict):
            self.error_logger.critical("Configuration error: %s", error_message)
            msg = f"Configuration error: {error_message}"
            raise TypeError(msg)
        return config

    def _extract_configuration(self) -> None:
        """Extract and validate configuration settings."""
        # Validate year_retrieval configuration section
        year_config_raw = self.config.get("year_retrieval")
        year_config = self._validate_config_section(year_config_raw, "'year_retrieval' section missing or not a dictionary.")

        # Extract API authentication configuration
        api_auth_config_raw = year_config.get("api_auth", {})
        api_auth_config = self._validate_config_section(
            api_auth_config_raw,
            "'year_retrieval.api_auth' subsection missing or invalid.",
        )

        # Load API tokens with SecureConfig
        self.discogs_token = self._load_secure_token(
            api_auth_config,
            "discogs_token",
            "DISCOGS_TOKEN",
        )
        self.lastfm_api_key = self._load_secure_token(
            api_auth_config,
            "lastfm_api_key",
            "LASTFM_API_KEY",
        )

        # Load MusicBrainz identification
        self.musicbrainz_app_name = api_auth_config.get(
            "musicbrainz_app_name",
            "MusicGenreUpdater/UnknownVersion",
        )
        self.contact_email = api_auth_config.get("contact_email", "")

        # Fallback to env var if placeholder unresolved or empty
        if not self.contact_email or str(self.contact_email).startswith("${"):
            self.contact_email = os.getenv("CONTACT_EMAIL", "")

        if not self.contact_email:
            self.error_logger.error(
                "Contact email is missing or not properly loaded from environment variables. "
                "Set 'contact_email' in config or CONTACT_EMAIL environment variable. "
                "MusicBrainz API requires valid contact information for compliance.",
            )
            self.console_logger.warning(
                "⚠️  Missing contact email - using placeholder. MusicBrainz API requests may be rate-limited or rejected.",
            )
            self.contact_email = "no-email-provided@example.com"

        # Setup User-Agent
        self.user_agent = (
            f"{self.musicbrainz_app_name} ({self.contact_email})"
            if self.contact_email and not self.contact_email.startswith("no-email")
            else self.musicbrainz_app_name
        )

        # Extract other configuration sections
        self.rate_limits_config = year_config.get("rate_limits", {})
        processing_config = year_config.get("processing", {})
        logic_config = year_config.get("logic", {})
        self.scoring_config = year_config.get("scoring", {})

        # Extract processing parameters
        preferred_api_raw = year_config.get("preferred_api", "musicbrainz")
        self.preferred_api = self._normalize_api_name(preferred_api_raw)

        self.use_lastfm = bool(self.lastfm_api_key) and bool(api_auth_config.get("use_lastfm", year_config.get("use_lastfm", True)))
        self.cache_ttl_days = processing_config.get("cache_ttl_days", 30)
        self.skip_prerelease = bool(processing_config.get("skip_prerelease", True))
        self.future_year_threshold = self._coerce_non_negative_int(processing_config.get("future_year_threshold"), default=1)
        self.prerelease_recheck_days = self._coerce_positive_int(processing_config.get("prerelease_recheck_days"), default=30)

        # Extract logic parameters
        self.min_valid_year = logic_config.get("min_valid_year", 1900)
        self.definitive_score_threshold = logic_config.get("definitive_score_threshold", 85)
        self.definitive_score_diff = logic_config.get("definitive_score_diff", 15)
        self.current_year = dt.now(UTC).year

        # Global retry configuration sourced from top-level settings
        self.default_api_max_retries = self._coerce_positive_int(self.config.get("max_retries"), default=3)
        self.default_api_retry_delay = self._coerce_non_negative_float(self.config.get("retry_delay_seconds"), default=1.0)

    def _load_secure_token(self, config: dict[str, Any], key: str, env_var: str) -> str:
        """Load API token using SecureConfig with fallback to environment variables."""
        try:
            raw_token = self._get_raw_token(config, key, env_var)
            return self._process_token_security(raw_token, key) if raw_token else ""
        except (KeyError, ValueError, SecurityConfigError):
            self.error_logger.exception("Error loading %s", key)
            return ""

    def _get_raw_token(self, config: dict[str, Any], key: str, env_var: str) -> str:
        """Get raw token from config or environment variables."""
        raw_token: str = str(config.get(key, ""))

        # Check if it's a placeholder that needs environment resolution
        if not raw_token or raw_token.startswith("${"):
            raw_token = os.getenv(env_var) or ""

        if not raw_token:
            self.error_logger.warning("%s is missing from config and %s environment variable", key, env_var)
            return ""

        return raw_token

    def _process_token_security(self, raw_token: str, key: str) -> str:
        """Process token encryption/decryption if SecureConfig is available."""
        if not self.secure_config:
            return raw_token

        if self.secure_config.is_token_encrypted(raw_token):
            return self._decrypt_token(raw_token, key)

        if raw_token:
            self._encrypt_token_for_future_storage(raw_token, key)

        return raw_token

    def _decrypt_token(self, encrypted_token: str, key: str) -> str:
        """Decrypt an encrypted token."""
        assert self.secure_config is not None  # Caller ensures this
        try:
            decrypted_token = self.secure_config.decrypt_token(encrypted_token, key)
            self.console_logger.debug("Successfully decrypted %s", key)
        except SecurityConfigError as e:
            self.error_logger.warning("Failed to decrypt %s, using as plaintext: %s", key, e)
            return encrypted_token

        return decrypted_token

    def _encrypt_token_for_future_storage(self, raw_token: str, key: str) -> None:
        """Encrypt a plaintext token and log the encrypted value for future use."""
        assert self.secure_config is not None  # Caller ensures this
        try:
            encrypted_token = self.secure_config.encrypt_token(raw_token, key)
            self.console_logger.info(
                "Encrypted %s token for secure storage. Consider updating config to use encrypted value: %s",
                key,
                encrypted_token,
            )
        except SecurityConfigError as e:
            self.error_logger.warning("Failed to encrypt %s: %s", key, e)

    def _initialize_rate_limiters(self) -> None:
        """Initialize rate limiters for each API provider."""
        try:
            self.rate_limiters = {
                "discogs": EnhancedRateLimiter(
                    requests_per_window=max(
                        1,
                        int(self.rate_limits_config.get("discogs_requests_per_minute", 25)),
                    ),
                    window_seconds=60.0,
                ),
                "musicbrainz": EnhancedRateLimiter(
                    requests_per_window=max(
                        1,
                        int(self.rate_limits_config.get("musicbrainz_requests_per_second", 1)),
                    ),
                    window_seconds=1.0,
                ),
                "lastfm": EnhancedRateLimiter(
                    requests_per_window=max(
                        1,
                        int(self.rate_limits_config.get("lastfm_requests_per_second", 5)),
                    ),
                    window_seconds=1.0,
                ),
                "itunes": EnhancedRateLimiter(
                    requests_per_window=max(
                        1,
                        int(self.rate_limits_config.get("itunes_requests_per_second", 10)),
                    ),
                    window_seconds=1.0,
                ),
            }
        except ValueError as e:
            self.error_logger.critical("Invalid rate limiter configuration: %s", e)
            msg = f"Invalid rate limiter configuration: {e}"
            raise ValueError(msg) from e

    def _initialize_api_clients(self) -> None:
        """Initialize API client instances with dependency injection."""

        # Create API request function for injection
        def make_api_request_func(
            api_name: str,
            url: str,
            params: dict[str, str] | None = None,
            headers_override: dict[str, str] | None = None,
            max_retries: int | None = None,
            base_delay: float | None = None,
            timeout_override: float | None = None,
        ) -> Coroutine[Any, Any, dict[str, Any] | None]:
            """Create an API request coroutine with injected parameters."""
            return self._make_api_request(
                api_name,
                url,
                params,
                headers_override,
                max_retries,
                base_delay,
                timeout_override,
            )

        # Create the scoring function for injection (using initialized scorer)
        def score_release_func(
            release: dict[str, Any],
            artist_norm: str,
            album_norm: str,
            artist_region: str | None,
            source: str = "unknown",
        ) -> int:
            """Create the release scoring function with an injected scorer."""
            return int(self.release_scorer.score_original_release(release, artist_norm, album_norm, artist_region=artist_region, source=source))

        # Initialize MusicBrainz client
        self.musicbrainz_client = MusicBrainzClient(
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            make_api_request_func=make_api_request_func,
            score_release_func=score_release_func,
        )

        # Initialize Discogs client
        self.discogs_client = DiscogsClient(
            token=self.discogs_token,
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            analytics=self.analytics,
            make_api_request_func=make_api_request_func,
            score_release_func=score_release_func,
            scoring_config=self.scoring_config,
            config={},  # Provide empty config dict
            cache_service=self.cache_service,
        )

        # Initialize Last.fm client
        self.lastfm_client = LastFmClient(
            api_key=self.lastfm_api_key,
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            make_api_request_func=make_api_request_func,
            score_release_func=score_release_func,
        )

        # Initialize Apple Music Search API client
        self.applemusic_client = AppleMusicClient(
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            make_api_request_func=make_api_request_func,
            score_release_func=score_release_func,
        )

    def _initialize_scoring_system(self) -> None:
        """Initialize the release scoring system."""
        self.release_scorer = create_release_scorer(
            scoring_config=self.scoring_config,
            min_valid_year=self.min_valid_year,
            definitive_score_threshold=self.definitive_score_threshold,
            console_logger=self.console_logger,
        )

        # Scoring function is now properly injected during API client initialization

    def _ensure_session_initialized(self) -> None:
        """Ensure the session is initialized, raise an error if not."""
        if self.session is None:
            self._raise_session_not_initialized()

    @staticmethod
    def _raise_session_not_initialized() -> NoReturn:
        """Raise runtime error for uninitialized session."""
        msg = "HTTP session is not initialized. Call initialize() method first."
        raise RuntimeError(msg)

    async def initialize(self, force: bool = False) -> None:
        """Initialize the aiohttp ClientSession and API clients."""
        if force and self.session and not self.session.closed:
            await self.session.close()
            self.session = None

        if self.session is None:
            self.session = self._create_client_session()
            forced_text = " (forced)" if force else ""
            self.console_logger.info(
                "External API session initialized with User-Agent: %s%s",
                self.user_agent,
                forced_text,
            )

            # Initialize API clients after the session is created
            self._initialize_api_clients()
            self.console_logger.debug("API clients initialized")

            # Mark as initialized
            self._initialized = True

    def _create_client_session(self) -> aiohttp.ClientSession:
        """Create a new aiohttp ClientSession with proper SSL configuration."""
        timeout = aiohttp.ClientTimeout(total=45, connect=15, sock_connect=15, sock_read=30)

        # Use certifi for portable SSL certificate management with TLS 1.3
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3

        connector = aiohttp.TCPConnector(
            limit_per_host=10,
            limit=50,
            force_close=True,
            ttl_dns_cache=300,
            ssl=ssl_context,
        )
        headers: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
        return aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers)

    async def _ensure_session(self) -> None:
        """Ensure that self.session is an open aiohttp.ClientSession."""
        if self.session is None or self.session.closed:
            # Close the existing session if it exists but is closed
            if self.session is not None:
                try:
                    if not self.session.closed:
                        await self.session.close()
                except (aiohttp.ClientError, TimeoutError, RuntimeError):
                    self.error_logger.exception("Error closing existing session")

            # Create a new session
            self.session = self._create_client_session()

    async def close(self) -> None:
        """Close the aiohttp ClientSession and log API usage statistics."""
        if self.session is None or self.session.closed:
            return

        # Log API statistics before closing
        self.console_logger.info("--- API Call Statistics ---")
        total_api_calls = 0
        total_api_time = 0.0
        for api_name, limiter in self.rate_limiters.items():
            stats = limiter.get_stats()
            durations = self.api_call_durations.get(api_name, [])
            avg_duration = sum(durations) / max(1, len(durations)) if durations else 0.0
            total_api_calls += stats["total_requests"]
            total_api_time += sum(durations)
            self.console_logger.info(
                "API: %-12s | Requests: %-5d | Avg Wait: %.3fs | Avg Duration: %.3fs",
                api_name.title(),
                stats["total_requests"],
                stats["avg_wait_time"],
                avg_duration,
            )

        if total_api_calls > 0:
            avg_total_duration = total_api_time / total_api_calls
            self.console_logger.info(
                "Total API Calls: %d, Average Call Duration: %.3fs",
                total_api_calls,
                avg_total_duration,
            )
        else:
            self.console_logger.info("No API calls were made during this session.")
        self.console_logger.info("---------------------------")

        await self.session.close()
        self.console_logger.info("External API session closed")

    # noinspection PyTypeChecker
    @staticmethod
    def _create_client_response_error(
        response: aiohttp.ClientResponse,
        status: int,
        message: str,
    ) -> aiohttp.ClientResponseError:
        """Create ClientResponseError with proper type handling for aiohttp type bug.

        Aiohttp has incorrect type hints in ClientResponseError constructor:
        - request_info expects None but needs RequestInfo
        - history expects tuple[None, ...]
        But needs tuple[ClientResponse, ...]

        This is a known issue: https://github.com/aio-libs/aiohttp/issues/...
        """
        return aiohttp.ClientResponseError(
            request_info=response.request_info,
            history=response.history,
            status=status,
            message=message,
        )

    async def _make_api_request(
        self,
        api_name: str,
        url: str,
        params: dict[str, str] | None = None,
        headers_override: dict[str, str] | None = None,
        max_retries: int | None = None,
        base_delay: float | None = None,
        timeout_override: float | None = None,
    ) -> dict[str, Any] | None:
        """Make an API request with rate limiting, error handling, and retry logic."""
        # Debug logging for iTunes requests
        if api_name == "itunes":
            self.console_logger.debug("[%s] Making API request to %s with params: %s", api_name, url, params)

        # Build the cache key and check the cache first
        cache_key_tuple = (
            "api_request",
            api_name,
            url,
            tuple(sorted((params or {}).items())),
        )
        cache_key = f"{cache_key_tuple[0]}_{cache_key_tuple[1]}_{hash(cache_key_tuple)}"
        cached_result = await self._check_and_validate_cache(cache_key, api_name, url)
        if cached_result is not None:
            if api_name == "itunes":
                self.console_logger.debug("[%s] Using cached result", api_name)
            return cached_result

        # Prepare request components
        prepared_request = self._prepare_request_components(api_name, url, headers_override, timeout_override)
        if prepared_request is None:
            return None

        request_headers, limiter, request_timeout = prepared_request

        # Execute request with retry logic
        retry_attempts = max_retries if isinstance(max_retries, int) and max_retries > 0 else self.default_api_max_retries
        retry_delay = base_delay if isinstance(base_delay, (int, float)) and base_delay >= 0 else self.default_api_retry_delay

        result = await self._execute_request_with_retry(
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
        cache_ttl_seconds = self.cache_ttl_days * 86400
        await self.cache_service.set_async(
            cache_key,
            result if result is not None else {},
            ttl=cache_ttl_seconds,
        )
        return result

    async def _check_and_validate_cache(self, cache_key: str, api_name: str, url: str) -> dict[str, Any] | None:
        """Check the cache and validate the cached response.

        Returns:
            The cached response dict if valid, None if not cached or invalid

        """
        cached_response = await self.cache_service.get_async(cache_key)
        if cached_response is None:
            return None

        # For API requests, cached_response should be dict[str, Any] based on how we store it
        if isinstance(cached_response, dict):
            if cached_response != {}:
                self.console_logger.debug("Using cached response for %s request to %s", api_name, url)
                return cached_response
            self.console_logger.debug("Cached empty response for %s request to %s", api_name, url)
            return {}  # Return an empty dict to signal "no result but cached"

        self.console_logger.warning(
            "Unexpected cached response type for %s request to %s: %s",
            api_name,
            url,
            type(cached_response).__name__,
        )
        self.cache_service.invalidate(cache_key)
        return None

    def _prepare_request_components(
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
            self.error_logger.error("No rate limiter configured for API: %s", api_name)
            return None

        # Setup request timeout
        request_timeout = aiohttp.ClientTimeout(total=timeout_override) if timeout_override else self.session.timeout

        return request_headers, limiter, request_timeout

    async def _execute_request_with_retry(
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
            result = await self._attempt_single_request(
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
        """Build URL for logging purposes."""
        return url + (f"?{urllib.parse.urlencode(params or {}, safe=':/')}" if params else "")

    async def _attempt_single_request(
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
            return await self._single_request_execution(
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
            return await self._handle_runtime_exception(rt, api_name, attempt, max_retries, url)
        except (TimeoutError, aiohttp.ClientError) as e:
            return await self._handle_client_exception(e, api_name, attempt, max_retries, base_delay, url)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            self._handle_unexpected_exception(e, api_name, url)
            return None

    async def _handle_runtime_exception(
        self,
        exception: RuntimeError,
        api_name: str,
        attempt: int,
        max_retries: int,
        url: str,
    ) -> dict[str, Any] | None:
        """Handle RuntimeError exceptions."""
        if await self._handle_runtime_error(exception, api_name, attempt, max_retries):
            return None  # Continue to next attempt

        self._log_final_failure(api_name, url, exception)
        return None  # Stop retrying

    async def _handle_client_exception(
        self,
        exception: TimeoutError | aiohttp.ClientError,
        api_name: str,
        attempt: int,
        max_retries: int,
        base_delay: float,
        url: str,
    ) -> dict[str, Any] | None:
        """Handle client timeout and connection errors."""
        if await self._handle_client_error(exception, api_name, attempt, max_retries, base_delay):
            return None  # Continue to next attempt

        self._log_final_failure(api_name, url, exception)
        return None  # Stop retrying

    def _handle_unexpected_exception(self, exception: Exception, api_name: str, url: str) -> None:
        """Handle unexpected exceptions."""
        self.error_logger.exception("[%s] Unexpected error making request to %s", api_name, url)
        self._log_final_failure(api_name, url, exception)

    def _log_final_failure(self, api_name: str, url: str, exception: Exception) -> None:
        """Log the final failure after all retries exhausted."""
        self.error_logger.error(
            "[%s] Request failed for URL: %s. Last exception: %s",
            api_name,
            url,
            exception,
        )

    async def _single_request_execution(
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
            The response dict if successful, None if it should retry, raises an exception if it failed

        """
        start_time = time.monotonic()

        try:
            await self._ensure_session()
            wait_time = await limiter.acquire()
            if wait_time > WAIT_TIME_LOG_THRESHOLD:
                self.console_logger.debug("[%s] Waited %.3fs for rate limiting", api_name, wait_time)

            self.request_counts[api_name] = self.request_counts.get(api_name, 0) + 1

            await self._ensure_session()
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
            limiter.release()

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
            Response dict if successful, None if it should retry, raises an exception if it failed

        """
        response_status = response.status

        if api_name == "discogs":
            self.console_logger.debug("[discogs] Sending Headers: %s", response.request_info.headers)

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

        # Handle different response statuses
        if response_status == HTTP_TOO_MANY_REQUESTS or response_status >= HTTP_SERVER_ERROR:
            raise ExternalApiOrchestrator._create_client_response_error(response=response, status=response_status, message=response_text_snippet)

        if not response.ok:
            self.error_logger.warning(
                "[%s] API request failed with status %d. URL: %s. Snippet: %s",
                api_name,
                response_status,
                url,
                response_text_snippet,
            )
            raise ExternalApiOrchestrator._create_client_response_error(response=response, status=response_status, message=response_text_snippet)

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

    async def _read_response_text(self, response: aiohttp.ClientResponse, api_name: str) -> str:
        """Read and log the response text snippet."""
        try:
            raw_text: str = await response.text(encoding="utf-8", errors="ignore")
            # Use unified limit for all APIs
            text_snippet: str = raw_text[:API_RESPONSE_LOG_LIMIT]

            # Unified debug logging instead of special case
            if self.console_logger.isEnabledFor(logging.DEBUG):
                self._log_api_response_debug(api_name, response.status, text_snippet)

            return text_snippet
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            self.error_logger.warning("[%s] Failed to read response body: %s", api_name, e)
            return f"[Error Reading Response: {e}]"

    def _log_api_response_debug(self, api_name: str, status: int, text_snippet: str) -> None:
        """Log API response for debugging."""
        self.console_logger.debug("====== %s RAW RESPONSE (Status: %d) ======", api_name.upper(), status)
        self.console_logger.debug(text_snippet)
        self.console_logger.debug("====== END %s RAW RESPONSE ======", api_name.upper())

    async def _handle_runtime_error(self, rt: RuntimeError, api_name: str, attempt: int, max_retries: int) -> bool:
        """Handle RuntimeError exceptions.

        Returns:
            True if it should retry, False if it should stop

        """
        if "Event loop is closed" not in str(rt) or attempt >= max_retries:
            return False

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
        await self._ensure_session()
        return True

    async def _handle_client_error(
        self,
        e: TimeoutError | aiohttp.ClientError,
        api_name: str,
        attempt: int,
        max_retries: int,
        base_delay: float,
    ) -> bool:
        """Handle client and timeout errors.

        Returns:
            True if it should retry, False if it should stop

        """
        # Track elapsed time for failed requests
        self.api_call_durations[api_name].append(0.0)

        if attempt >= max_retries or not isinstance(
            e,
            aiohttp.ClientConnectorError | aiohttp.ServerDisconnectedError | asyncio.TimeoutError,
        ):
            self.error_logger.exception("[%s] Request failed after %d attempts", api_name, attempt + 1)
            return False

        delay = base_delay * (2**attempt) * (0.8 + SECURE_RANDOM.random() * 0.4)
        self.console_logger.warning(
            "[%s] %s, retrying %d/%d in %.2fs",
            api_name,
            type(e).__name__,
            attempt + 1,
            max_retries,
            delay,
        )
        await asyncio.sleep(delay)
        return True

    async def _parse_json_response(
        self,
        response: aiohttp.ClientResponse,
        api_name: str,
        url: str,
        snippet: str,
    ) -> dict[str, Any] | None:
        """Parse JSON response and ensure it is a dict, logging if not."""
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
            # iTunes API returns text/javascript but the content is actually JSON
            self.console_logger.debug("[%s] ContentTypeError caught: %s from %s", api_name, cte, url)
            if api_name == "itunes":
                self.console_logger.debug("[%s] Attempting manual JSON parsing for iTunes", api_name)
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
            else:
                self.error_logger.exception(
                    "[%s] Content type error parsing JSON response from %s. Snippet: %s",
                    api_name,
                    url,
                    snippet[:200],
                )
        except json.JSONDecodeError:
            self.error_logger.exception(
                "[%s] Error parsing JSON response from %s. Snippet: %s",
                api_name,
                url,
                snippet[:200],
            )
        return None

    async def _safe_mark_for_verification(
        self,
        artist: str,
        album: str,
        *,
        reason: str = "no_year_found",
        metadata: dict[str, Any] | None = None,
        fire_and_forget: bool = False,
        recheck_days: int | None = None,
    ) -> None:
        """Safely mark an album for verification, optionally as fire-and-forget."""
        if not self.pending_verification_service:
            self.error_logger.warning(
                "Pending verification service not initialized - cannot mark '%s - %s' for verification. Check dependency injection configuration.",
                artist,
                album,
            )
            return
        try:
            if fire_and_forget:
                # Create a background task that won't block
                task = asyncio.create_task(
                    self.pending_verification_service.mark_for_verification(
                        artist=artist,
                        album=album,
                        reason=reason,
                        metadata=metadata,
                        recheck_days=recheck_days,
                    ),
                )
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)
                self.console_logger.debug(
                    "Queued verification for '%s - %s' (fire-and-forget)",
                    artist,
                    album,
                )
            else:
                # Direct await
                await self.pending_verification_service.mark_for_verification(
                    artist=artist,
                    album=album,
                    reason=reason,
                    metadata=metadata,
                    recheck_days=recheck_days,
                )
                self.console_logger.debug("Marked '%s - %s' for verification", artist, album)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            self.error_logger.warning("Failed to mark '%s - %s' for verification: %s", artist, album, e)

    async def _safe_remove_from_pending(self, artist: str, album: str) -> None:
        """Safely remove an album from the pending verification queue."""
        if not self.pending_verification_service:
            self.error_logger.warning(
                "Pending verification service not initialized - cannot remove '%s - %s' from pending. Check dependency injection configuration.",
                artist,
                album,
            )
            return
        try:
            await self.pending_verification_service.remove_from_pending(artist=artist, album=album)
            self.console_logger.debug("Removed '%s - %s' from pending verification", artist, album)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            self.error_logger.warning(
                "Failed to remove '%s - %s' from pending verification: %s",
                artist,
                album,
                e,
            )

    @staticmethod
    def _count_prerelease_tracks(tracks: list[dict[str, str]]) -> int:
        """Count tracks marked as prerelease."""
        return sum(track.get("track_status", "").lower() == "prerelease" for track in tracks)

    def _compute_future_year_stats(
        self,
        tracks: list[dict[str, str]],
        current_year: int,
    ) -> tuple[int, int, bool, bool]:
        """Calculate future-year related statistics."""
        future_year_count = 0
        max_year = 0
        for track in tracks:
            with contextlib.suppress(ValueError, TypeError):
                if year := track.get("year"):
                    year_int = int(year)
                    if year_int > current_year:
                        future_year_count += 1
                        max_year = max(max_year, year_int)

        total_tracks = len(tracks)
        ratio_triggered = future_year_count > 0 and future_year_count >= total_tracks * 0.5 if total_tracks else False
        significant = max_year > 0 and (max_year - current_year) > self.future_year_threshold
        return future_year_count, max_year, ratio_triggered, significant

    @staticmethod
    def _is_prerelease_album(
        prerelease_count: int,
        ratio_triggered: bool,
        significant_future_year: bool,
    ) -> bool:
        """Determine if album should be treated as prerelease."""
        return prerelease_count > 0 or (ratio_triggered and significant_future_year)

    def _handle_prerelease_album(
        self,
        artist: str,
        album: str,
        current_library_year: str,
        prerelease_count: int,
        future_year_count: int,
        max_future_year: int,
        total_tracks: int,
    ) -> None:
        """Log and mark prerelease albums for verification."""
        self.console_logger.info(
            "Album '%s - %s' detected as prerelease (%d prerelease tracks, %d future year tracks). Keeping current year: %s",
            artist,
            album,
            prerelease_count,
            future_year_count,
            current_library_year or "N/A",
        )

        metadata: dict[str, Any] = {
            "track_count": str(total_tracks),
            "future_year_threshold": str(self.future_year_threshold),
        }
        if max_future_year > 0:
            metadata["expected_year"] = str(max_future_year)

        task = asyncio.create_task(
            self._safe_mark_for_verification(
                artist,
                album,
                reason="prerelease",
                metadata=metadata,
                fire_and_forget=True,
                recheck_days=self.prerelease_recheck_days,
            ),
        )
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _log_future_year_within_threshold(self, artist: str, album: str) -> None:
        """Log debug message when future years are detected but under threshold."""
        self.console_logger.debug(
            "Future year detected for '%s - %s' but within threshold (%d year(s)); proceeding with update",
            artist,
            album,
            self.future_year_threshold,
        )

    def should_update_album_year(
        self,
        tracks: list[dict[str, str]],
        artist: str = "",
        album: str = "",
        current_library_year: str = "",
    ) -> bool:
        """Determine whether to update the year for an album based on the status of its tracks."""
        if not tracks:
            return True

        if not self.skip_prerelease:
            return True

        current_year = dt.now(tz=UTC).year
        prerelease_count = self._count_prerelease_tracks(tracks)
        future_year_count, max_future_year, ratio_triggered, significant_future_year = self._compute_future_year_stats(tracks, current_year)
        if self._is_prerelease_album(prerelease_count, ratio_triggered, significant_future_year):
            self._handle_prerelease_album(
                artist,
                album,
                current_library_year,
                prerelease_count,
                future_year_count,
                max_future_year,
                len(tracks),
            )
            return False

        if ratio_triggered and not significant_future_year:
            self._log_future_year_within_threshold(artist, album)

        return True

    async def get_album_year(
        self,
        artist: str,
        album: str,
        current_library_year: str | None = None,
    ) -> tuple[str | None, bool]:
        """Determine the original release year for an album using optimized API calls and revised scoring."""
        # Initialize and prepare inputs
        try:
            inputs = await self._initialize_year_search(artist, album, current_library_year)
            if not inputs:
                return None, False
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            if debug.year:
                self.error_logger.exception("Error in get_album_year initialization: %s", e)
            return None, False

        artist_norm, album_norm, log_artist, log_album, artist_region = inputs

        # Main processing
        try:
            # Fetch and process API results
            all_releases = await self._fetch_all_api_results(artist_norm, album_norm, artist_region, log_artist, log_album)

            if not all_releases:
                return await self._handle_no_results(artist, album, log_artist, log_album, current_library_year)

            return await self._process_api_results(all_releases, artist, album, log_artist, log_album, current_library_year)

        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, RuntimeError):
            return self._handle_year_search_error(log_artist, log_album, current_library_year)
        finally:
            self.release_scorer.clear_artist_period_context()

    async def _initialize_year_search(
        self, artist: str, album: str, current_library_year: str | None
    ) -> tuple[str, str, str, str, str | None] | None:
        """Initialize year search with logging and context setup."""
        if debug.year:
            self.console_logger.info("get_album_year called with artist='%s' album='%s'", artist, album)

        # Debug mode for script-specific text processing
        script_type = detect_primary_script(artist)
        if script_type not in {ScriptType.LATIN, ScriptType.UNKNOWN}:
            self._log_script_debug(script_type)

        # Normalize inputs
        artist_norm, album_norm, log_artist, log_album = self._prepare_search_inputs(artist, album)

        # Log initialization
        self._log_search_initialization(log_artist, log_album, current_library_year, artist_norm, album_norm)

        # Get artist context
        artist_region = await self._setup_artist_context(artist_norm, log_artist)

        return artist_norm, album_norm, log_artist, log_album, artist_region

    def _log_script_debug(self, script_type: ScriptType) -> None:
        """Log debug information for script-specific text processing."""
        if not debug.api:
            return

        script_emoji_map = {
            ScriptType.CYRILLIC: "🇺🇦",
            ScriptType.CHINESE: "🇨🇳",
            ScriptType.JAPANESE: "🇯🇵",
            ScriptType.KOREAN: "🇰🇷",
            ScriptType.ARABIC: "🇸🇦",
            ScriptType.HEBREW: "🇮🇱",
            ScriptType.GREEK: "🇬🇷",
            ScriptType.THAI: "🇹🇭",
            ScriptType.DEVANAGARI: "🇮🇳",
            ScriptType.MIXED: "🌍",
        }

        emoji = script_emoji_map.get(script_type, "🌍")
        self.console_logger.info(f"{emoji} Processing {script_type.value} artist")
        self.console_logger.info(
            f"{emoji} Token status: Discogs=%s, LastFM=%s",
            "LOADED" if self.discogs_token else "MISSING",
            "LOADED" if self.lastfm_api_key else "MISSING",
        )

        script_priorities = self._get_script_config_priorities(script_type)
        primary_apis = script_priorities.get("primary", ["musicbrainz"])
        self.console_logger.info(f"{emoji} Primary APIs for {script_type.value}: {primary_apis}")

    def _log_search_initialization(
        self, log_artist: str, log_album: str, current_library_year: str | None, artist_norm: str, album_norm: str
    ) -> None:
        """Log search initialization details."""
        if not debug.year:
            return

        self.console_logger.info("Starting normalization...")
        self.console_logger.info("Normalization complete: artist_norm='%s' album_norm='%s'", artist_norm, album_norm)
        self.console_logger.info(
            "Starting year determination: artist='%s' album='%s' current_library_year='%s' current_system_year=%d",
            log_artist,
            log_album,
            current_library_year or "None",
            self.current_year,
        )
        self.console_logger.info(
            "Searching for original release year: '%s - %s' (current: %s)",
            log_artist,
            log_album,
            current_library_year or "none",
        )

    def _handle_year_search_error(self, log_artist: str, log_album: str, current_library_year: str | None) -> tuple[str | None, bool]:
        """Handle errors during year search and return fallback year."""
        self.error_logger.exception(
            "Unexpected error in get_album_year for '%s - %s'",
            log_artist,
            log_album,
        )
        # Apply defensive fix to prevent current year contamination
        if current_library_year and is_valid_year(current_library_year, self.min_valid_year, self.current_year):
            # Explicitly reject the current system year as suspicious
            if current_library_year == str(self.current_year):
                self.console_logger.warning(
                    self._SUSPICIOUS_CURRENT_YEAR_MSG,
                    current_library_year,
                    log_artist,
                    log_album,
                )
                return None, False

            return current_library_year, False
        return None, False

    @staticmethod
    def _prepare_search_inputs(artist: str, album: str) -> tuple[str, str, str, str]:
        """Prepare normalized and display names for API search."""
        artist_norm = normalize_name(artist)
        album_norm = normalize_name(album)
        log_artist = artist if artist != artist_norm else artist_norm
        log_album = album if album != album_norm else album_norm
        return artist_norm, album_norm, log_artist, log_album

    async def _setup_artist_context(self, artist_norm: str, log_artist: str) -> str | None:
        """Set up artist context for release scoring."""
        try:
            # Get artist's activity period for context (cached)
            if debug.year:
                self.console_logger.info("Fetching artist activity period for '%s'...", artist_norm)
            activity_result = await self.musicbrainz_client.get_artist_activity_period(artist_norm)
            if debug.year:
                self.console_logger.info("Activity period result: %s", activity_result)
            start_year, end_year = None, None

            if activity_result and len(activity_result) == ACTIVITY_PERIOD_TUPLE_LENGTH:
                start_year_val, end_year_val = activity_result
                start_year = int(start_year_val) if start_year_val else None
                end_year = int(end_year_val) if end_year_val else None

            # Store as ArtistPeriodContext
            self.artist_period_context = ArtistPeriodContext(start_year=start_year, end_year=end_year)
            activity_log = f"({start_year or '?'} - {end_year or 'present'})" if start_year or end_year else "(activity period unknown)"
            self.console_logger.info("Artist activity period context: %s", activity_log)

            # Get the artist's likely region for scoring context (cached)
            artist_region = await self.musicbrainz_client.get_artist_region(artist_norm)
            if artist_region:
                self.console_logger.info("Artist region context: %s", artist_region.upper())

            # Set the artist period context in the scorer
            if self.artist_period_context:
                self.release_scorer.set_artist_period_context(self.artist_period_context)

            return str(artist_region) if artist_region else None

        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as context_err:
            self.error_logger.warning("Error fetching artist context for '%s': %s", log_artist, context_err)
            return None

    async def _fetch_all_api_results(
        self,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
        log_artist: str,
        log_album: str,
    ) -> list[ScoredRelease]:
        """Fetch scored releases from all API providers with Cyrillic-aware logic."""
        self._log_api_search_start(artist_norm, album_norm, artist_region, log_artist, log_album)

        # Try script-optimized search first
        artist_script = detect_primary_script(log_artist)
        album_script = detect_primary_script(log_album)
        primary_script = artist_script if artist_script != ScriptType.UNKNOWN else album_script

        if primary_script not in (ScriptType.LATIN, ScriptType.UNKNOWN):
            script_results = await self._try_script_optimized_search(primary_script, artist_norm, album_norm, artist_region)
            if script_results:
                return script_results

        # Standard API search (all providers concurrently)
        return await self._execute_standard_api_search(artist_norm, album_norm, artist_region, log_artist, log_album)

    def _log_api_search_start(self, artist_norm: str, album_norm: str, artist_region: str | None, log_artist: str, log_album: str) -> None:
        """Log API search initialization."""
        if not debug.api:
            return

        self.console_logger.info(
            "Starting API search with parameters: artist_norm='%s', album_norm='%s', artist_region='%s'",
            artist_norm,
            album_norm,
            artist_region or "None",
        )
        self.console_logger.info("Original names: artist='%s', album='%s'", log_artist, log_album)

    async def _try_script_optimized_search(
        self,
        script_type: ScriptType,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
    ) -> list[ScoredRelease] | None:
        """Try script-optimized API search based on detected script type."""
        if debug.api:
            self.console_logger.info(f"{script_type.value} detected - trying script-optimized search")

        api_lists = self._get_script_api_priorities(script_type)

        # Try primary APIs first
        results = await self._try_api_list(api_lists["primary"], artist_norm, album_norm, artist_region, script_type, is_fallback=False)
        if results:
            return results

        # Try fallback APIs if primary failed
        if debug.api:
            self.console_logger.info(f"Primary APIs failed for {script_type.value} - trying fallback")
        return await self._try_api_list(api_lists["fallback"], artist_norm, album_norm, artist_region, script_type, is_fallback=True)

    def _get_script_api_priorities(self, script_type: ScriptType) -> dict[str, list[str]]:
        """Get script-specific API priorities from config."""
        script_priorities = self._get_script_config_priorities(script_type)
        primary_raw = script_priorities.get("primary", ["musicbrainz"])
        fallback_raw = script_priorities.get("fallback", ["lastfm"])

        primary = primary_raw if isinstance(primary_raw, list) else ["musicbrainz"]
        fallback = fallback_raw if isinstance(fallback_raw, list) else ["lastfm"]

        return {
            "primary": self._apply_preferred_order(primary),
            "fallback": self._apply_preferred_order(fallback),
        }

    def _get_script_config_priorities(self, script_type: ScriptType) -> dict[str, Any]:
        """Get script-specific API priorities from configuration file.

        Args:
            script_type: The script type (e.g., CYRILLIC, LATIN, etc.)

        Returns:
            Dictionary containing primary and fallback API configurations for the script type
        """
        year_config = self.config.get("year_retrieval", {})
        script_api_priorities = year_config.get("script_api_priorities", {})
        default_config = script_api_priorities.get("default", {})
        script_priorities: dict[str, Any] = script_api_priorities.get(script_type.value, default_config)
        return script_priorities

    async def _try_api_list(
        self, api_names: list[str], artist_norm: str, album_norm: str, artist_region: str | None, script_type: ScriptType, is_fallback: bool
    ) -> list[ScoredRelease] | None:
        """Try a list of API names and return the first successful result."""
        normalized_names = [self._normalize_api_name(name) for name in api_names]
        for api_name in normalized_names:
            results = await self._try_single_api(api_name, artist_norm, album_norm, artist_region, script_type, is_fallback)
            if results:
                return results
        return None

    async def _try_single_api(
        self, api_name: str, artist_norm: str, album_norm: str, artist_region: str | None, script_type: ScriptType, is_fallback: bool
    ) -> list[ScoredRelease] | None:
        """Try a single API and return results if successful."""
        try:
            api_client = self._get_api_client(api_name)
            if not api_client:
                if debug.api and not is_fallback:
                    self.console_logger.debug(f"{api_name} client not available, skipping")
                return None

            if debug.api:
                self.console_logger.info(f"Trying {api_name} for {script_type.value} text")
            results: list[ScoredRelease] = await self._call_api_with_proper_params(api_client, api_name, artist_norm, album_norm, artist_region)

            if results:
                if debug.api:
                    result_type = "Fallback" if is_fallback else "Primary"
                    self.console_logger.info(f"{result_type} {api_name} found %d results for {script_type.value}", len(results))
                return results

        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            if debug.api:
                self.console_logger.warning(f"{api_name} failed for {script_type.value}: %s", e)

        return None

    @staticmethod
    async def _call_api_with_proper_params(
        api_client: MusicBrainzClient | DiscogsClient | LastFmClient | AppleMusicClient,
        api_name: str,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
    ) -> list[ScoredRelease]:
        """Call API with proper parameters based on what the API accepts."""
        result: list[ScoredRelease]
        if api_name in {"musicbrainz", "discogs"}:
            # MusicBrainz and Discogs accept artist_region parameter
            assert isinstance(api_client, MusicBrainzClient | DiscogsClient)
            result = await api_client.get_scored_releases(artist_norm, album_norm, artist_region)
        else:
            # LastFm and AppleMusic don't accept artist_region parameter
            assert isinstance(api_client, LastFmClient | AppleMusicClient)
            result = await api_client.get_scored_releases(artist_norm, album_norm)
        return result

    def _get_api_client(self, api_name: str) -> MusicBrainzClient | DiscogsClient | LastFmClient | AppleMusicClient | None:
        """Get API client by name."""
        api_mapping: dict[str, MusicBrainzClient | DiscogsClient | LastFmClient | AppleMusicClient] = {
            "musicbrainz": self.musicbrainz_client,
            "discogs": self.discogs_client,
            "lastfm": self.lastfm_client,
            "itunes": self.applemusic_client,
            "applemusic": self.applemusic_client,
        }
        return api_mapping.get(api_name)

    async def _execute_standard_api_search(
        self, artist_norm: str, album_norm: str, artist_region: str | None, log_artist: str, log_album: str
    ) -> list[ScoredRelease]:
        """Execute standard concurrent API search across all providers."""
        api_order = self._apply_preferred_order(["musicbrainz", "discogs", "itunes"] + (["lastfm"] if self.use_lastfm else []))
        api_tasks: list[Coroutine[Any, Any, list[ScoredRelease]]] = []
        ordered_names: list[str] = []

        for api_name in api_order:
            api_client = self._get_api_client(api_name)
            if not api_client:
                continue
            api_tasks.append(
                self._call_api_with_proper_params(
                    api_client,
                    api_name,
                    artist_norm,
                    album_norm,
                    artist_region,
                )
            )
            ordered_names.append(api_name)

        if not api_tasks:
            return []

        results = await asyncio.gather(*api_tasks, return_exceptions=True)
        return self._process_api_task_results(results, ordered_names, log_artist, log_album, artist_norm, album_norm)

    def _process_api_task_results(
        self, results: list[Any], api_names: list[str], log_artist: str, log_album: str, artist_norm: str, album_norm: str
    ) -> list[ScoredRelease]:
        """Process results from concurrent API tasks."""
        all_releases: list[ScoredRelease] = []

        for api_name, result in zip(api_names, results, strict=True):
            if isinstance(result, Exception):
                self._log_api_error(api_name, log_artist, log_album, result)
            elif isinstance(result, list) and result:
                all_releases.extend(result)
                self.console_logger.info("Received %d scored releases from %s", len(result), api_name.title())
            elif not result:
                self._log_empty_api_result(api_name, log_artist, log_album, artist_norm, album_norm)

        self._log_api_summary(log_artist, log_album, len(all_releases))
        return all_releases

    def _log_api_error(self, api_name: str, log_artist: str, log_album: str, error: Exception) -> None:
        """Log API error details."""
        self.error_logger.warning(
            "API call to %s failed for '%s - %s': %s: %s",
            api_name,
            log_artist,
            log_album,
            type(error).__name__,
            error,
        )

    def _log_empty_api_result(self, api_name: str, log_artist: str, log_album: str, artist_norm: str, album_norm: str) -> None:
        """Log empty API result details."""
        if not debug.api:
            return

        self.console_logger.warning(
            "%s returned EMPTY results for '%s - %s' (search params: artist_norm='%s', album_norm='%s')",
            api_name.title(),
            log_artist,
            log_album,
            artist_norm,
            album_norm,
        )

    def _log_api_summary(self, log_artist: str, log_album: str, total_releases: int) -> None:
        """Log API search summary."""
        if not debug.api:
            return

        self.console_logger.info(
            "API summary for '%s - %s': Total releases found: %d (MusicBrainz, Discogs, iTunes%s)",
            log_artist,
            log_album,
            total_releases,
            ", Last.fm" if self.use_lastfm else "",
        )

    async def _handle_no_results(
        self,
        artist: str,
        album: str,
        log_artist: str,
        log_album: str,
        current_library_year: str | None,
    ) -> tuple[str | None, bool]:
        """Handle case when no API results are found."""
        self.console_logger.warning("No release data found from any API for '%s - %s'", log_artist, log_album)
        await self._safe_mark_for_verification(artist, album)
        # Apply defensive fix to prevent current year contamination
        if current_library_year and is_valid_year(current_library_year, self.min_valid_year, self.current_year):
            # Explicitly reject the current system year as suspicious
            if current_library_year == str(self.current_year):
                self.console_logger.warning(
                    self._SUSPICIOUS_CURRENT_YEAR_MSG,
                    current_library_year,
                    log_artist,
                    log_album,
                )
                result_year = None
            else:
                result_year = current_library_year
        else:
            result_year = None
        return result_year, False

    async def _process_api_results(
        self,
        all_releases: list[ScoredRelease],
        artist: str,
        album: str,
        log_artist: str,
        log_album: str,
        current_library_year: str | None,
    ) -> tuple[str | None, bool]:
        """Process API results and determine the best release year."""
        # Aggregate scores by year
        year_scores = self._aggregate_year_scores(all_releases)

        if not year_scores:
            self.console_logger.warning(
                "No valid years found after processing API results for '%s - %s'",
                log_artist,
                log_album,
            )
            await self._safe_mark_for_verification(artist, album)
            fallback_year = self._get_fallback_year_when_no_api_results(current_library_year, log_artist, log_album)
            return fallback_year, False

        # Determine the best year and definitive status
        best_year, is_definitive = self._select_best_year(year_scores)

        self.console_logger.info("Selected year: %s. Definitive? %s", best_year, is_definitive)

        if not is_definitive:
            await self._safe_mark_for_verification(artist, album)
        else:
            await self._safe_remove_from_pending(artist, album)

        return best_year, is_definitive

    def _aggregate_year_scores(self, all_releases: list[ScoredRelease]) -> defaultdict[str, list[int]]:
        """Aggregate release scores by year, filtering out invalid years."""
        year_scores: defaultdict[str, list[int]] = defaultdict(list)

        for release in all_releases:
            year_value = release.get("year")
            year = str(year_value) if year_value is not None else None
            score = int(release.get("score", 0))
            if year and is_valid_year(year, self.min_valid_year, self.current_year):
                year_scores[year].append(score)

        return year_scores

    def _get_fallback_year_when_no_api_results(self, current_library_year: str | None, log_artist: str, log_album: str) -> str | None:
        """Apply defensive fix to prevent current year contamination when no API results found."""
        if current_library_year and is_valid_year(current_library_year, self.min_valid_year, self.current_year):
            # Explicitly reject the current system year as suspicious
            if current_library_year == str(self.current_year):
                self.console_logger.warning(
                    self._SUSPICIOUS_CURRENT_YEAR_MSG,
                    current_library_year,
                    log_artist,
                    log_album,
                )
                return None
            return current_library_year
        return None

    def _select_best_year(self, year_scores: defaultdict[str, list[int]]) -> tuple[str, bool]:
        """Select the best year from aggregated scores and determine if definitive."""
        final_year_scores = self._compute_final_year_scores(year_scores)
        sorted_years = self._sort_years_by_score(final_year_scores)
        self._log_ranked_years(sorted_years)

        best_year, best_score, best_year_is_future = self._determine_best_year_candidate(sorted_years)

        # Initialize variables to avoid potential unbound variable errors
        score_thresholds = self._calculate_score_thresholds(best_score)
        has_score_conflict = self._check_score_conflicts(sorted_years, best_year_is_future)

        # Check for suspiciously old years when we have only one result
        if len(sorted_years) == 1:
            best_year, is_definitive = self._validate_single_result(best_year, best_score)
            if not is_definitive:
                self.console_logger.warning("Single result validation failed for year %s - marking as non-definitive", best_year)
                return best_year, False
        else:
            is_definitive = self._determine_definitiveness(score_thresholds, best_year_is_future, has_score_conflict)

        if not is_definitive:
            self._log_non_definitive_reasons(best_year_is_future, score_thresholds, has_score_conflict, best_score)

        return best_year, is_definitive

    @staticmethod
    def _compute_final_year_scores(year_scores: defaultdict[str, list[int]]) -> dict[str, int]:
        """Get the maximum score for each year."""
        return {year: max(scores) for year, scores in year_scores.items() if scores}

    @staticmethod
    def _sort_years_by_score(final_year_scores: dict[str, int]) -> list[tuple[str, int]]:
        """Sort years primarily by score (desc), secondarily by year (asc)."""
        return sorted(final_year_scores.items(), key=lambda item: (-item[1], int(item[0])))

    def _log_ranked_years(self, sorted_years: list[tuple[str, int]]) -> None:
        """Log the ranked years for debugging."""
        log_scores = ", ".join([f"{y}:{s}" for y, s in sorted_years[:MAX_LOGGED_YEARS]])
        truncation_indicator = "..." if len(sorted_years) > MAX_LOGGED_YEARS else ""
        self.console_logger.info("Ranked year scores (Year:MaxScore): %s%s", log_scores, truncation_indicator)

    def _determine_best_year_candidate(self, sorted_years: list[tuple[str, int]]) -> tuple[str, int, bool]:
        """Determine the best year candidate, handling future vs non-future and reissue vs original preferences."""
        best_year, best_score = sorted_years[0]
        best_year_is_future = int(best_year) > self.current_year

        # If we have multiple candidates, check for future vs non-future preference
        if len(sorted_years) > 1 and best_year_is_future:
            best_year, best_score, best_year_is_future = self._apply_future_year_preference(sorted_years, best_year, best_score, best_year_is_future)

        # After handling future year preference, check for original vs reissue preference
        if len(sorted_years) > 1 and not best_year_is_future:
            best_year, best_score = self._apply_original_release_preference(sorted_years, best_year, best_score)

        return best_year, best_score, best_year_is_future

    def _apply_future_year_preference(
        self,
        sorted_years: list[tuple[str, int]],
        best_year: str,
        best_score: int,
        best_year_is_future: bool,
    ) -> tuple[str, int, bool]:
        """Apply preference for non-future years when scores are close."""
        second_year, second_best_score = sorted_years[1]
        second_is_future = int(second_year) > self.current_year
        score_difference = best_score - second_best_score

        if score_difference < self.definitive_score_diff and not second_is_future:
            self.console_logger.info(
                "Preferring non-future year %s over future %s (scores: %d vs %d)",
                second_year,
                best_year,
                second_best_score,
                best_score,
            )
            return second_year, second_best_score, False

        return best_year, best_score, best_year_is_future

    def _apply_original_release_preference(
        self,
        sorted_years: list[tuple[str, int]],
        best_year: str,
        best_score: int,
    ) -> tuple[str, int]:
        """Apply preference for earlier years (likely original releases) over later years (likely reissues)."""
        # Only apply this logic if the best year seems like it could be a reissue
        # (i.e., there are significantly earlier years with similar scores)
        best_year_int = int(best_year)

        # Enhanced reissue detection: check for large year gaps
        all_years = [int(year_str) for year_str, _ in sorted_years]
        if len(all_years) > 1:
            earliest_year = min(all_years)
            latest_year = max(all_years)
            year_gap = latest_year - earliest_year

            # If there's a significant gap, and the best year is not the earliest,
            # it's likely a reissue scenario
            if year_gap > MIN_YEAR_GAP_FOR_REISSUE_DETECTION and best_year_int > earliest_year:
                self.console_logger.info(
                    "[ORIGINAL_RELEASE_FIX] Detected potential reissue scenario: year range %d-%d (%d years gap), best year %s not earliest",
                    earliest_year,
                    latest_year,
                    year_gap,
                    best_year,
                )
                # Increase preference for earlier years in reissue scenarios
                effective_score_threshold = self.definitive_score_diff * 2
            else:
                effective_score_threshold = self.definitive_score_diff
        else:
            effective_score_threshold = self.definitive_score_diff

        # Look for earlier years that might be the original release
        # Collect all valid candidates within score threshold, then pick the earliest
        valid_candidates: list[tuple[str, int]] = []

        for candidate_year, candidate_score in sorted_years[1:]:
            candidate_year_int = int(candidate_year)
            score_difference = best_score - candidate_score
            year_difference = best_year_int - candidate_year_int

            # If we find an earlier year within the score threshold, and it's at least a few years earlier,
            # add it as a candidate for the likely original release
            if score_difference <= effective_score_threshold and year_difference >= MIN_REISSUE_YEAR_DIFFERENCE:
                valid_candidates.append((candidate_year, candidate_score))

            # If the score difference is significant, stop looking (reissue has much better data)
            if score_difference >= self.definitive_score_diff:
                break

        # If we found valid candidates, pick the earliest year
        if valid_candidates:
            # Find the earliest year among all valid candidates
            earliest_candidate_tuple: tuple[str, int] = min(valid_candidates, key=lambda x: int(x[0]))
            selected_year: str = earliest_candidate_tuple[0]
            selected_score: int = earliest_candidate_tuple[1]
            year_difference = best_year_int - int(selected_year)

            self.console_logger.info(
                "[ORIGINAL_RELEASE_FIX] Preferring earliest year %s over later year %s "
                "(likely original vs reissue, scores: %d vs %d, year diff: %d, threshold: %d)",
                selected_year,
                best_year,
                selected_score,
                best_score,
                year_difference,
                effective_score_threshold,
            )
            return selected_year, selected_score

        return best_year, best_score

    def _validate_single_result(self, best_year: str, best_score: int) -> tuple[str, bool]:
        """Validate single API results for suspicious old years that might be incorrect."""
        year_int = int(best_year)
        current_year = self.current_year

        # If the year is suspiciously old compared to current year (>3 years difference)
        # and we only got one result with a low-to-medium score, be cautious
        year_diff = current_year - year_int

        if year_diff > MAX_SUSPICIOUS_YEAR_DIFFERENCE and best_score < MIN_CONFIDENT_SCORE_THRESHOLD:  # Suspiciously old + not high confidence
            self.console_logger.warning(
                "SINGLE_RESULT_VALIDATION: Year %s is %d years old with only score %d from single API - "
                "this could be incorrect metadata, marking as non-definitive",
                best_year,
                year_diff,
                best_score,
            )
            return best_year, False

        # Otherwise, apply normal score thresholds
        score_thresholds = self._calculate_score_thresholds(best_score)
        is_definitive = score_thresholds["high_score_met"]

        return best_year, is_definitive

    def _calculate_score_thresholds(self, best_score: int) -> dict[str, bool]:
        """Calculate various score threshold checks."""
        return {
            "very_high_score": best_score >= VERY_HIGH_SCORE_THRESHOLD,
            "high_score_met": best_score >= self.definitive_score_threshold,
        }

    def _check_score_conflicts(self, sorted_years: list[tuple[str, int]], best_year_is_future: bool) -> bool:
        """Check for score conflicts between competing years."""
        if len(sorted_years) <= 1:
            self.console_logger.debug("Only one candidate year found.")
            return False

        best_year, best_score = sorted_years[0]
        second_year, second_best_score = sorted_years[1]
        score_difference = best_score - second_best_score

        if score_difference >= self.definitive_score_diff:
            self.console_logger.debug(
                "Clear score winner: %s:%d vs %s:%d (diff=%d)",
                best_year,
                best_score,
                second_year,
                second_best_score,
                score_difference,
            )
            return False

        return self._evaluate_score_conflict(
            best_year,
            best_score,
            second_year,
            second_best_score,
            score_difference,
            best_year_is_future,
        )

    def _evaluate_score_conflict(
        self,
        best_year: str,
        best_score: int,
        second_year: str,
        second_best_score: int,
        score_difference: int,
        best_year_is_future: bool,
    ) -> bool:
        """Evaluate whether close scores constitute a conflict."""
        second_is_future = int(second_year) > self.current_year

        if not best_year_is_future and second_is_future:
            self.console_logger.debug("Keeping non-future year %s over future %s", best_year, second_year)
            return False

        # Both future or both non-future with similar scores = conflict
        self.console_logger.debug(
            "Score conflict: %s:%d vs %s:%d (diff=%d, threshold=%d)",
            best_year,
            best_score,
            second_year,
            second_best_score,
            score_difference,
            self.definitive_score_diff,
        )
        return True

    @staticmethod
    def _determine_definitiveness(
        score_thresholds: dict[str, bool],
        best_year_is_future: bool,
        has_score_conflict: bool,
    ) -> bool:
        """Determine if the year selection is definitive."""
        return score_thresholds["high_score_met"] and not best_year_is_future and (score_thresholds["very_high_score"] or not has_score_conflict)

    def _log_non_definitive_reasons(
        self,
        best_year_is_future: bool,
        score_thresholds: dict[str, bool],
        has_score_conflict: bool,
        best_score: int,
    ) -> None:
        """Log reasons why the year selection is not definitive."""
        reason: list[str] = []
        if best_year_is_future:
            reason.append("future year")
        if not score_thresholds["high_score_met"]:
            reason.append(f"score {best_score} < {self.definitive_score_threshold}")
        if has_score_conflict and not score_thresholds["very_high_score"]:
            reason.append("competing years with similar scores")
        self.console_logger.debug("Not definitive: %s", ", ".join(reason))

    def _score_release_wrapper(
        self,
        release: dict[str, Any],
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
        source: str = "unknown",
    ) -> float:
        """Public wrapper for release scoring."""
        return float(self.release_scorer.score_original_release(release, artist_norm, album_norm, artist_region=artist_region, source=source))

    async def get_artist_activity_period(
        self,
        artist_norm: str,
    ) -> tuple[int | None, int | None]:
        """Retrieve the period of activity for an artist from MusicBrainz.

        This method delegates to the MusicBrainz client.

        Args:
            artist_norm: Normalized artist name

        Returns:
            Tuple of (start_year, end_year) as integers or (None, None) if not found

        """
        # Delegate to MusicBrainz client
        str_result = await self.musicbrainz_client.get_artist_activity_period(artist_norm)

        # Convert string years to integers to match protocol
        start_year = int(str_result[0]) if str_result[0] else None
        end_year = int(str_result[1]) if str_result[1] else None

        return start_year, end_year

    async def get_year_from_discogs(
        self,
        artist: str,
        album: str,
    ) -> str | None:
        """Fetch the earliest release year for an album from Discogs.

        This method delegates to the Discogs client.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Year string or None if not found

        """
        # Normalize inputs
        artist_norm = normalize_name(artist)
        album_norm = normalize_name(album)

        # Delegate to the Discogs client
        result: str | None = await self.discogs_client.get_year_from_discogs(artist_norm, album_norm)
        return result


# Factory function for easy instantiation
def create_external_api_orchestrator(
    config: dict[str, Any],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    analytics: Analytics,
    cache_service: CacheOrchestrator,
    pending_verification_service: PendingVerificationService,
) -> ExternalApiOrchestrator:
    """Create the configured ExternalApiOrchestrator instance.

    Args:
        config: Configuration dictionary
        console_logger: Logger for general output
        error_logger: Logger for error messages and warnings
        analytics: Analytics service for performance tracking
        cache_service: Service for caching API responses
        pending_verification_service: Service for managing verification queue

    Returns:
        The configured ExternalApiOrchestrator instance

    """
    return ExternalApiOrchestrator(
        config=config,
        console_logger=console_logger,
        error_logger=error_logger,
        analytics=analytics,
        cache_service=cache_service,
        pending_verification_service=pending_verification_service,
    )
