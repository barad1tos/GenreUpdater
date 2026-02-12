"""External API Service Orchestrator.

This module provides the main coordination layer for fetching album release years
from multiple API providers (MusicBrainz, Discogs). It replaces the legacy
external API service with a modular architecture that maintains backward
compatibility while providing better separation of concerns.

The orchestrator handles:
- HTTP session management and connection pooling
- Rate limiting coordination across all API providers
- Request caching and response aggregation
- Dependency injection for cache and verification services
- Authentication token management with encryption support
- Release year determination using the sophisticated scoring algorithm
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import re
import ssl
from datetime import UTC
from datetime import datetime as dt
from typing import TYPE_CHECKING, Any, TypedDict

import aiohttp
import certifi

from core.debug_utils import debug
from core.logger import LogFormat
from core.models.script_detection import ScriptType, detect_primary_script
from core.models.validators import is_valid_year
from core.tracks.year_fallback import MAX_VERIFICATION_ATTEMPTS
from services.api.api_base import EnhancedRateLimiter, ScoredRelease
from services.api.applemusic import AppleMusicClient
from services.api.discogs import DiscogsClient
from services.api.musicbrainz import MusicBrainzClient
from services.api.request_executor import ApiRequestExecutor
from services.api.year_score_resolver import YearScoreResolver
from services.api.year_scoring import ArtistPeriodContext, create_release_scorer
from services.api.year_search_coordinator import YearSearchCoordinator
from stubs.cryptography.secure_config import SecureConfig, SecurityConfigError

if TYPE_CHECKING:
    import logging
    from collections.abc import Coroutine

    from core.models.track_models import AppConfig
    from metrics import Analytics
    from services.cache.orchestrator import CacheOrchestrator
    from services.pending_verification import PendingVerificationService


def normalize_name(name: str) -> str:
    """Normalize artist/album name for API queries.

    Performs substitutions that improve API matching:
    - & → and (Karma & Effect → Karma and Effect)
    - w/ → with (Split w/ Band → Split with Band)
    - Strips trailing compilation markers (Album + 4 → Album)
    - Normalizes whitespace

    Note: This is for API QUERIES, not for scoring/matching.
    Scoring uses ReleaseScorer._normalize_name which is more aggressive.
    """
    if not name:
        return name

    result = name

    # Common substitutions for better API matching
    substitutions = {
        " & ": " and ",
        "&": " and ",  # Handle no-space cases like "Fire&Water"
        " w/ ": " with ",
        " w/": " with ",
        " = ": " ",  # Liberation = Termination → Liberation Termination
        ":": " ",  # Issue #103: Colons break Lucene search (III:Trauma → III Trauma)
    }

    for old, new in substitutions.items():
        result = result.replace(old, new)

    # Strip trailing compilation markers: "+ 4", "+ 10" (number = # bonus tracks)
    # Pattern: " + " followed by digit(s) at end of string
    # More conservative than ".*" to preserve legitimate titles like "Album + Bonus Tracks"
    result = re.sub(r"\s*\+\s+\d+.*$", "", result)

    # Strip content after " / " (split albums - keep first part only)
    # "Robot Hive / Exodus" → "Robot Hive"
    # "House By the Cemetery / Mortal Massacre" → "House By the Cemetery"
    if " / " in result:
        result = result.split(" / ", maxsplit=1)[0].strip()

    # Normalize whitespace (multiple spaces to single)
    return re.sub(r"\s+", " ", result).strip()


# Constants
WAIT_TIME_LOG_THRESHOLD: float = 0.1
HTTP_TOO_MANY_REQUESTS: int = 429
HTTP_SERVER_ERROR: int = 500
YEAR_LENGTH: int = 4
API_RESPONSE_LOG_LIMIT: int = 500  # Unified limit for all API response logging
ACTIVITY_PERIOD_TUPLE_LENGTH: int = 2  # Expected length for activity period tuple
PENDING_TASKS_SHUTDOWN_TIMEOUT: float = 5.0  # Timeout for pending tasks during graceful shutdown

# Connection pool settings (optimized for long-running API sessions, Issue #104)
CONNECTOR_LIMIT_PER_HOST: int = 10
CONNECTOR_LIMIT_TOTAL: int = 50
KEEPALIVE_TIMEOUT_SECONDS: int = 30
DNS_CACHE_TTL_SECONDS: int = 300

SECURE_RANDOM: random.SystemRandom = random.SystemRandom()


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

    Coordinates API calls across multiple providers (MusicBrainz, Discogs)
    to determine the original release year for music albums. Provides rate limiting,
    caching, authentication, and sophisticated scoring to identify the most likely
    original release.

    This class implements a modular architecture for external API services,
    providing unified access to MusicBrainz and Discogs APIs.

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
    def _normalize_api_name(api_name: Any) -> str:
        """Normalize API name aliases to orchestrator-internal identifiers."""
        name = str(api_name).strip().lower()
        if name in {"applemusic", "itunes"}:
            return "itunes"
        if name not in {"musicbrainz", "discogs", "itunes"}:
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
        config: AppConfig,
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
        self.applemusic_client: AppleMusicClient

        # Initialize SecureConfig for encrypted token storage
        self.secure_config: SecureConfig | None = None
        try:
            self.secure_config = SecureConfig(logger=self.error_logger)
            self.console_logger.debug("%s initialized for encrypted token storage", LogFormat.entity("SecureConfig"))
        except SecurityConfigError as e:
            self.error_logger.warning("Failed to initialize SecureConfig: %s", e)
            self.secure_config = None

        # Extract and validate configuration
        self._extract_configuration()

        # Initialize rate limiters
        self._initialize_rate_limiters()

        # Initialize API request executor (handles HTTP requests with retry/caching)
        self.request_executor = ApiRequestExecutor(
            cache_service=cache_service,
            rate_limiters=self.rate_limiters,
            console_logger=console_logger,
            error_logger=error_logger,
            user_agent=self.user_agent,
            discogs_token=self.discogs_token,
            cache_ttl_days=self.cache_ttl_days,
            default_max_retries=self.default_api_max_retries,
            default_retry_delay=self.default_api_retry_delay,
        )

        # Initialize the scoring system first (needed for API client injection)
        self._initialize_scoring_system()

        # Statistics tracking - delegate to request_executor
        self.request_counts = self.request_executor.request_counts
        self.api_call_durations = self.request_executor.api_call_durations

        # Initialize state flag
        self._initialized = False

    def _extract_configuration(self) -> None:
        """Extract and validate configuration settings from typed AppConfig."""
        year_cfg = self.config.year_retrieval
        api_auth = year_cfg.api_auth

        # Load API tokens with SecureConfig
        self.discogs_token = self._load_secure_token(
            api_auth.discogs_token,
            "discogs_token",
            "DISCOGS_TOKEN",
        )

        # Load MusicBrainz identification
        self.musicbrainz_app_name = api_auth.musicbrainz_app_name or "MusicGenreUpdater/UnknownVersion"
        self.contact_email = api_auth.contact_email

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
                "Missing contact email - using placeholder. MusicBrainz API may reject or rate-limit requests.",
            )
            self.contact_email = "no-email-provided@example.com"

        # Setup User-Agent
        self.user_agent = (
            f"{self.musicbrainz_app_name} ({self.contact_email})"
            if self.contact_email and not self.contact_email.startswith("no-email")
            else self.musicbrainz_app_name
        )

        # Store typed sub-configs for rate limiter initialization
        self.rate_limits_config = year_cfg.rate_limits
        processing = year_cfg.processing
        logic = year_cfg.logic
        # scoring_config stays as dict for ReleaseScorer compatibility (PR C migration)
        self.scoring_config: dict[str, Any] = year_cfg.scoring.model_dump()

        # Extract processing parameters
        self.preferred_api = self._normalize_api_name(year_cfg.preferred_api.value)

        self.cache_ttl_days = processing.cache_ttl_days
        self.skip_prerelease = processing.skip_prerelease
        self.future_year_threshold = processing.future_year_threshold
        self.prerelease_recheck_days = processing.prerelease_recheck_days

        # Extract logic parameters
        self.min_valid_year = logic.min_valid_year
        self.definitive_score_threshold = logic.definitive_score_threshold
        self.definitive_score_diff = logic.definitive_score_diff
        self.current_year = dt.now(UTC).year

        # Global retry configuration sourced from top-level settings
        self.default_api_max_retries = self.config.max_retries
        self.default_api_retry_delay = self.config.retry_delay_seconds

    def _load_secure_token(self, config_value: str, key: str, env_var: str) -> str:
        """Load API token using SecureConfig with fallback to environment variables."""
        try:
            raw_token = self._get_raw_token(config_value, key, env_var)
            return self._process_token_security(raw_token, key) if raw_token else ""
        except (KeyError, ValueError, SecurityConfigError):
            self.error_logger.exception("Error loading %s", key)
            return ""

    def _get_raw_token(self, config_value: str, key: str, env_var: str) -> str:
        """Get raw token from config value or environment variables."""
        raw_token: str = str(config_value) if config_value else ""

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
        if self.secure_config is None:
            msg = f"secure_config must be initialized before decrypting token (key={key})"
            raise RuntimeError(msg)
        try:
            decrypted_token = self.secure_config.decrypt_token(encrypted_token, key)
            self.console_logger.debug("Successfully decrypted %s", key)
        except SecurityConfigError as e:
            self.error_logger.warning("Failed to decrypt %s, using as plaintext: %s", key, e)
            return encrypted_token

        return decrypted_token

    def _encrypt_token_for_future_storage(self, raw_token: str, key: str) -> None:
        """Encrypt a plaintext token and log the encrypted value for future use."""
        if self.secure_config is None:
            msg = f"secure_config must be initialized before encrypting token (key={key})"
            raise RuntimeError(msg)
        try:
            encrypted_token = self.secure_config.encrypt_token(raw_token, key)
            self.console_logger.info("Token '%s' encrypted. Update config.yaml with the encrypted value.", key)
            # Store encrypted value for manual config update (visible only in debug logs)
            self.console_logger.debug("Encrypted value for %s: %s", key, encrypted_token)
        except SecurityConfigError as e:
            self.error_logger.warning("Failed to encrypt %s: %s", key, e)

    def _initialize_rate_limiters(self) -> None:
        """Initialize rate limiters for each API provider."""
        rate_limits = self.rate_limits_config
        self.rate_limiters = {
            "discogs": EnhancedRateLimiter(
                requests_per_window=max(1, rate_limits.discogs_requests_per_minute),
                window_seconds=60.0,
            ),
            "musicbrainz": EnhancedRateLimiter(
                requests_per_window=max(1, int(rate_limits.musicbrainz_requests_per_second)),
                window_seconds=1.0,
            ),
            "itunes": EnhancedRateLimiter(
                requests_per_window=10,
                window_seconds=1.0,
            ),
        }

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
            analytics=self.analytics,
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

        # Initialize Apple Music Search API client
        self.applemusic_client = AppleMusicClient(
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            make_api_request_func=make_api_request_func,
            score_release_func=score_release_func,
        )

    def _initialize_scoring_system(self) -> None:
        """Initialize the release scoring system."""
        # Extract remaster keywords from config for edition normalization
        remaster_keywords = self.config.cleaning.remaster_keywords

        # Store for use in scoring (ReleaseScorer uses these for edition normalization)
        self.remaster_keywords: list[str] = remaster_keywords

        self.release_scorer = create_release_scorer(
            scoring_config=self.scoring_config,
            min_valid_year=self.min_valid_year,
            definitive_score_threshold=self.definitive_score_threshold,
            console_logger=self.console_logger,
            remaster_keywords=remaster_keywords,
        )
        self.year_score_resolver = YearScoreResolver(
            console_logger=self.console_logger,
            min_valid_year=self.min_valid_year,
            current_year=self.current_year,
            definitive_score_threshold=self.definitive_score_threshold,
            definitive_score_diff=self.definitive_score_diff,
            remaster_keywords=remaster_keywords,
        )

    def _initialize_year_search_coordinator(self) -> None:
        """Initialize the year search coordinator (after API clients are ready)."""
        self.year_search_coordinator = YearSearchCoordinator(
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            config=self.config,
            preferred_api=self.preferred_api,
            musicbrainz_client=self.musicbrainz_client,
            discogs_client=self.discogs_client,
            applemusic_client=self.applemusic_client,
            release_scorer=self.release_scorer,
        )

        # Scoring function is now properly injected during API client initialization

    async def initialize(self, force: bool = False) -> None:
        """Initialize the aiohttp ClientSession and API clients.

        Args:
            force: If True, close existing session and reinitialize.

        Raises:
            Exception: Re-raises any exception from initialization after cleanup.
        """
        if force and self.session and not self.session.closed:
            await self.session.close()
            self.session = None

        if self.session is None:
            self.session = self._create_client_session()
            try:
                self.request_executor.set_session(self.session)
                self._initialize_api_clients()
                self._initialize_year_search_coordinator()
            except Exception:
                # Clean up session on initialization failure to prevent resource leak
                if self.session and not self.session.closed:
                    await self.session.close()
                self.session = None
                # Clear request executor's session reference to prevent stale session usage
                self.request_executor.set_session(None)
                raise

            forced_text = " (forced)" if force else ""
            self.console_logger.info(
                "External API session initialized with User-Agent: %s%s",
                self.user_agent,
                forced_text,
            )
            self.console_logger.debug("API clients initialized")

            # Mark as initialized
            self._initialized = True

    def _create_client_session(self) -> aiohttp.ClientSession:
        """Create a new aiohttp ClientSession with proper SSL configuration."""
        timeout = aiohttp.ClientTimeout(total=45, connect=15, sock_connect=15, sock_read=30)

        # Use certifi for portable SSL certificate management with TLS 1.3
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3

        # Connection pooling settings optimized for long-running sessions (Issue #104)
        # - Removed force_close=True to enable HTTP keep-alive / connection reuse
        # - keepalive_timeout=30: Keep idle connections alive for 30 seconds
        # - enable_cleanup_closed=True: Properly cleanup SSL connections
        connector = aiohttp.TCPConnector(
            limit_per_host=CONNECTOR_LIMIT_PER_HOST,
            limit=CONNECTOR_LIMIT_TOTAL,
            keepalive_timeout=KEEPALIVE_TIMEOUT_SECONDS,
            enable_cleanup_closed=True,
            ttl_dns_cache=DNS_CACHE_TTL_SECONDS,
            ssl=ssl_context,
        )
        headers: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
        return aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers)

    async def close(self) -> None:
        """Close the orchestrator and clean up resources gracefully.

        This method:
        1. Waits for pending fire-and-forget tasks to complete (PENDING_TASKS_SHUTDOWN_TIMEOUT)
        2. Cancels any tasks that don't complete in time
        3. Clears the _pending_tasks set
        4. Logs API statistics
        5. Closes the HTTP session
        """
        # Wait for pending tasks with timeout
        if self._pending_tasks:
            self.console_logger.debug(
                "Waiting for %d pending tasks to complete...",
                len(self._pending_tasks),
            )
            done, pending = await asyncio.wait(
                self._pending_tasks,
                timeout=PENDING_TASKS_SHUTDOWN_TIMEOUT,
                return_when=asyncio.ALL_COMPLETED,
            )
            # Mark done set as intentionally unused (we only need pending for cancellation)
            _ = done

            # Cancel any tasks that didn't complete in time
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            self._pending_tasks.clear()

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
        self.console_logger.info("%s session closed", LogFormat.entity("ExternalApiOrchestrator"))

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
        """Make an API request with rate limiting, error handling, and retry logic.

        Delegates to ApiRequestExecutor for HTTP handling.
        """
        return await self.request_executor.execute_request(
            api_name=api_name,
            url=url,
            params=params,
            headers_override=headers_override,
            max_retries=max_retries,
            base_delay=base_delay,
            timeout_override=timeout_override,
        )

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

    async def _get_attempt_count(self, artist: str, album: str) -> int:
        """Get current verification attempt count for an album.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Number of verification attempts made (0 if not tracked or service unavailable).

        """
        if not self.pending_verification_service:
            return 0
        try:
            return await self.pending_verification_service.get_attempt_count(artist=artist, album=album)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            self.error_logger.warning(
                "Failed to get attempt count for '%s - %s': %s",
                artist,
                album,
                e,
            )
            return 0

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

    async def get_album_year(
        self,
        artist: str,
        album: str,
        current_library_year: str | None = None,
        earliest_track_added_year: int | None = None,
    ) -> tuple[str | None, bool, int, dict[str, int]]:
        """Determine the original release year for an album using optimized API calls and revised scoring.

        Returns:
            Tuple of (year, is_definitive, confidence_score, year_scores)
            year_scores: dict mapping each year found by APIs to its max score
        """
        # Initialize and prepare inputs
        try:
            inputs = await self._initialize_year_search(artist, album, current_library_year)
            if not inputs:
                return None, False, 0, {}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            if debug.year:
                self.error_logger.exception("Error in get_album_year initialization: %s", e)
            return None, False, 0, {}

        artist_norm, album_norm, log_artist, log_album, artist_region = inputs

        # Main processing
        try:
            # Fetch and process API results
            all_releases = await self._fetch_all_api_results(artist_norm, album_norm, artist_region, log_artist, log_album)

            if not all_releases:
                return await self._handle_no_results(artist, album, log_artist, log_album, current_library_year, earliest_track_added_year)

            return await self._process_api_results(
                all_releases, artist, album, log_artist, log_album, current_library_year, earliest_track_added_year
            )

        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, RuntimeError):
            return self._handle_year_search_error(log_artist, log_album, current_library_year, earliest_track_added_year)
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

        self.console_logger.info("Processing %s artist", script_type.value)
        self.console_logger.info(
            "Token status: Discogs=%s",
            "LOADED" if self.discogs_token else "MISSING",
        )

        # Get script-specific API priorities from config
        script_api_priorities = self.config.year_retrieval.script_api_priorities
        default_priority = script_api_priorities.get("default")
        script_priority = script_api_priorities.get(script_type.value, default_priority)
        primary_apis = script_priority.primary if script_priority else ["musicbrainz"]
        self.console_logger.info("Primary APIs for %s: %s", script_type.value, primary_apis)

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

    def _is_current_year_contamination(
        self,
        current_library_year: str | None,
        earliest_track_added_year: int | None,
    ) -> bool:
        """Check if current library year is likely contamination from auto-populated metadata.

        Contamination occurs when:
        - Library year equals current year AND
        - Track was NOT added recently (added in previous years or date unknown)
        - OR track date is in the future (impossible, indicates bad data)
        """
        if current_library_year != str(self.current_year):
            return False

        # Missing track date → can't verify, treat as contamination
        if earliest_track_added_year is None:
            return True

        # Future track date → bad data, treat as contamination
        if earliest_track_added_year > self.current_year:
            return True

        # Track added in previous year but has current year → contamination
        return earliest_track_added_year < self.current_year

    def _handle_year_search_error(
        self,
        log_artist: str,
        log_album: str,
        current_library_year: str | None,
        earliest_track_added_year: int | None = None,
    ) -> tuple[str | None, bool, int, dict[str, int]]:
        """Handle errors during year search and return fallback year.

        Returns:
            Tuple of (year, is_definitive, confidence_score, year_scores)
        """
        self.error_logger.exception(
            "Unexpected error in get_album_year for '%s - %s'",
            log_artist,
            log_album,
        )
        # Apply defensive fix to prevent current year contamination
        if current_library_year and is_valid_year(current_library_year, self.min_valid_year, self.current_year):
            if self._is_current_year_contamination(current_library_year, earliest_track_added_year):
                self.console_logger.warning(
                    self._SUSPICIOUS_CURRENT_YEAR_MSG,
                    current_library_year,
                    log_artist,
                    log_album,
                )
                return None, False, 0, {}
            return current_library_year, False, 0, {}
        return None, False, 0, {}

    @staticmethod
    def _prepare_search_inputs(artist: str, album: str) -> tuple[str, str, str, str]:
        """Prepare normalized and display names for API search.

        Strips quotes and parenthetical content from album name for cleaner API queries.
        APIs don't search for "(Deluxe Edition)" or "(Bonus Track Version)" -
        these are metadata, not album names.
        """
        artist_norm = normalize_name(artist)

        # Remove quotes from album name (e.g., "Survival of the Sickest" → Survival of the Sickest)
        album_clean = album.replace('"', "").replace("'", "")

        # Remove all parenthetical content from album for API queries
        # "(Deluxe Edition)", "(Bonus Track Version)", "(Remastered)" → removed
        album_clean = re.sub(r"\s*\([^)]*\)", "", album_clean).strip()
        album_norm = normalize_name(album_clean)

        log_artist = artist if artist != artist_norm else artist_norm
        log_album = album if album != album_norm else album_norm
        return artist_norm, album_norm, log_artist, log_album

    @staticmethod
    def _parse_activity_period(activity_result: tuple[int | None, int | None] | None) -> tuple[int | None, int | None]:
        """Parse activity period result into start/end years."""
        if not activity_result or len(activity_result) != ACTIVITY_PERIOD_TUPLE_LENGTH:
            return None, None
        start_year_val, end_year_val = activity_result
        start_year = int(start_year_val) if start_year_val else None
        end_year = int(end_year_val) if end_year_val else None
        return start_year, end_year

    def _log_activity_period(self, start_year: int | None, end_year: int | None) -> None:
        """Log the artist activity period."""
        activity_log = f"({start_year or '?'} - {end_year or 'present'})" if start_year or end_year else "(activity period unknown)"
        self.console_logger.info("Artist activity period context: %s", activity_log)

    async def _setup_artist_context(self, artist_norm: str, log_artist: str) -> str | None:
        """Set up artist context for release scoring."""
        try:
            # Get artist's activity period for context (cached)
            if debug.year:
                self.console_logger.info("Fetching artist activity period for '%s'...", artist_norm)
            activity_result = await self.musicbrainz_client.get_artist_activity_period(artist_norm)
            if debug.year:
                self.console_logger.info("Activity period result: %s", activity_result)

            start_year, end_year = self._parse_activity_period(activity_result)

            # Store as ArtistPeriodContext and set in scorer
            self.artist_period_context = ArtistPeriodContext(start_year=start_year, end_year=end_year)
            self._log_activity_period(start_year, end_year)
            self.release_scorer.set_artist_period_context(self.artist_period_context)

            # Get the artist's likely region for scoring context (cached)
            artist_region = await self.musicbrainz_client.get_artist_region(artist_norm)
            if artist_region:
                self.console_logger.info("Artist region context: %s", artist_region.upper())

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
        """Fetch scored releases from all API providers with script-aware logic."""
        return await self.year_search_coordinator.fetch_all_api_results(artist_norm, album_norm, artist_region, log_artist, log_album)

    async def _handle_no_results(
        self,
        artist: str,
        album: str,
        log_artist: str,
        log_album: str,
        current_library_year: str | None,
        earliest_track_added_year: int | None = None,
    ) -> tuple[str | None, bool, int, dict[str, int]]:
        """Handle case when no API results are found.

        Returns:
            Tuple of (year, is_definitive, confidence_score, year_scores)
        """
        self.console_logger.warning("No release data found from any API for '%s - %s'", log_artist, log_album)
        await self._safe_mark_for_verification(artist, album)
        # Apply defensive fix to prevent current year contamination
        if not (current_library_year and is_valid_year(current_library_year, self.min_valid_year, self.current_year)):
            return None, False, 0, {}

        if self._is_current_year_contamination(current_library_year, earliest_track_added_year):
            self.console_logger.warning(
                self._SUSPICIOUS_CURRENT_YEAR_MSG,
                current_library_year,
                log_artist,
                log_album,
            )
            return None, False, 0, {}
        return current_library_year, False, 0, {}

    async def _process_api_results(
        self,
        all_releases: list[ScoredRelease],
        artist: str,
        album: str,
        log_artist: str,
        log_album: str,
        current_library_year: str | None,
        earliest_track_added_year: int | None = None,
    ) -> tuple[str | None, bool, int, dict[str, int]]:
        """Process API results and determine the best release year.

        Returns:
            Tuple of (year, is_definitive, confidence_score, year_scores)
            year_scores: dict mapping each year found by APIs to its max score
        """
        # Aggregate scores by year using YearScoreResolver
        year_scores = self.year_score_resolver.aggregate_year_scores(all_releases)

        if not year_scores:
            self.console_logger.warning(
                "No valid years found for '%s - %s' (%d releases processed)",
                log_artist,
                log_album,
                len(all_releases) if all_releases else 0,
            )
            await self._safe_mark_for_verification(artist, album)
            fallback_year = self._get_fallback_year_when_no_api_results(current_library_year, log_artist, log_album, earliest_track_added_year)
            return fallback_year, False, 0, {}

        # Determine the best year and definitive status using YearScoreResolver
        best_year, is_definitive, confidence_score = self.year_score_resolver.select_best_year(
            year_scores,
            all_releases=all_releases,
            existing_year=current_library_year,
        )

        self.console_logger.info("Selected year: %s. Definitive? %s (confidence: %d%%)", best_year, is_definitive, confidence_score)

        # Rule 1: Trust matching years - if API year matches existing year, skip verification
        # Even with low confidence, a match confirms the existing year is correct
        if best_year and current_library_year and best_year == current_library_year:
            self.console_logger.debug(
                "Year matches existing (%s) for '%s - %s', trusting match",
                best_year,
                log_artist,
                log_album,
            )
            await self._safe_remove_from_pending(artist, album)
        elif not is_definitive:
            # Rule 2: Check attempt count for escalation before marking
            attempt_count = await self._get_attempt_count(artist, album)

            if attempt_count >= MAX_VERIFICATION_ATTEMPTS:
                # Escalation: After N attempts, accept best result if available
                if best_year is not None:
                    self.console_logger.warning(
                        "Verification limit reached for '%s - %s'. Accepting year %s after %d attempts",
                        log_artist,
                        log_album,
                        best_year,
                        attempt_count,
                    )
                    await self._safe_remove_from_pending(artist, album)
                else:
                    # No usable result after N attempts - log as unresolvable
                    self.console_logger.warning(
                        "Verification unresolvable for '%s - %s' after %d attempts, no API result",
                        log_artist,
                        log_album,
                        attempt_count,
                    )
                    # Don't mark again - it's already in pending with attempt count
            else:
                # Under the limit - mark for verification (increments attempt count)
                await self._safe_mark_for_verification(artist, album)
        else:
            await self._safe_remove_from_pending(artist, album)

        # Convert year_scores from list of scores to max score per year
        max_year_scores: dict[str, int] = {year: max(scores) for year, scores in year_scores.items()}
        return best_year, is_definitive, confidence_score, max_year_scores

    def _get_fallback_year_when_no_api_results(
        self,
        current_library_year: str | None,
        log_artist: str,
        log_album: str,
        earliest_track_added_year: int | None = None,
    ) -> str | None:
        """Apply defensive fix to prevent current year contamination when no API results found."""
        if current_library_year and is_valid_year(current_library_year, self.min_valid_year, self.current_year):
            if self._is_current_year_contamination(current_library_year, earliest_track_added_year):
                self.console_logger.warning(
                    self._SUSPICIOUS_CURRENT_YEAR_MSG,
                    current_library_year,
                    log_artist,
                    log_album,
                )
                return None
            return current_library_year
        return None

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
        activity_period = await self.musicbrainz_client.get_artist_activity_period(artist_norm)

        # Convert string years to integers to match protocol
        def safe_int(val: str | None) -> int | None:
            """Convert string to int, returning None for invalid or empty values."""
            if not val:
                return None
            try:
                return int(val)
            except ValueError:
                return None

        return safe_int(activity_period[0]), safe_int(activity_period[1])

    async def get_artist_start_year(self, artist_norm: str) -> int | None:
        """Get artist's career start year with caching and fallback.

        Uses MusicBrainz as primary source, iTunes as fallback.
        Results are cached in GenericCacheService.

        Args:
            artist_norm: Normalized artist name

        Returns:
            Artist's career start year, or None if not found

        Cache TTL:
            - Positive result: 1 year (31,536,000 seconds)
            - Negative result: 1 day (86,400 seconds)

        """
        cache_key = f"artist_start_year:{artist_norm}"

        # 1. Check cache first
        cached = self.cache_service.generic_service.get(cache_key)
        if cached is not None:
            # -1 is sentinel for "not found" (None can't be cached directly)
            if cached == -1:
                self.console_logger.debug(
                    "[orchestrator] Artist start year cache hit (negative): %s",
                    artist_norm,
                )
                return None
            # Ensure cached value is convertible to int
            if not isinstance(cached, (int, str)):
                self.console_logger.warning(
                    "[orchestrator] Invalid cached artist start year type for '%s': %s",
                    artist_norm,
                    type(cached).__name__,
                )
                return None
            cached_year = int(cached)
            self.console_logger.debug(
                "[orchestrator] Artist start year cache hit: %s → %d",
                artist_norm,
                cached_year,
            )
            return cached_year

        # 2. Try MusicBrainz (primary source)
        begin_year, _ = await self.get_artist_activity_period(artist_norm)
        if begin_year:
            self.cache_service.generic_service.set(cache_key, begin_year, ttl=31536000)
            self.console_logger.debug(
                "[orchestrator] Artist start year from MusicBrainz: %s → %d",
                artist_norm,
                begin_year,
            )
            return begin_year

        # 3. Fallback to iTunes
        itunes_year = await self.applemusic_client.get_artist_start_year(artist_norm)
        if itunes_year:
            self.cache_service.generic_service.set(cache_key, itunes_year, ttl=31536000)
            self.console_logger.debug(
                "[orchestrator] Artist start year from iTunes (fallback): %s → %d",
                artist_norm,
                itunes_year,
            )
            return itunes_year

        # 4. Cache negative result with shorter TTL
        self.cache_service.generic_service.set(cache_key, -1, ttl=86400)
        self.console_logger.debug(
            "[orchestrator] Artist start year not found, caching negative: %s",
            artist_norm,
        )
        return None

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
    config: AppConfig,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    analytics: Analytics,
    cache_service: CacheOrchestrator,
    pending_verification_service: PendingVerificationService,
) -> ExternalApiOrchestrator:
    """Create the configured ExternalApiOrchestrator instance.

    Args:
        config: Typed application configuration
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
