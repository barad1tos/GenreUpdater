"""Year retrieval functionality for Music Genre Updater.

This module provides a facade for album year retrieval and updates.
The actual logic is delegated to specialized components:
- YearDeterminator: Core year determination logic
- YearBatchProcessor: Batch processing with concurrency control
- YearConsistencyChecker: Year consistency analysis
- YearFallbackHandler: Fallback logic for uncertain updates
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.models.validators import is_empty_year
from metrics import Analytics

from .year_batch import YearBatchProcessor
from .year_consistency import YearConsistencyChecker
from .year_determination import YearDeterminator
from .year_fallback import YearFallbackHandler
from .year_utils import (
    normalize_collaboration_artist,
    resolve_non_negative_float,
    resolve_non_negative_int,
    resolve_positive_int,
)

if TYPE_CHECKING:
    import logging

    from core.models.protocols import (
        CacheServiceProtocol,
        ExternalApiServiceProtocol,
        PendingVerificationServiceProtocol,
    )
    from core.models.track_models import ChangeLogEntry, TrackDict
    from core.retry_handler import DatabaseRetryHandler

    from .track_processor import TrackProcessor


class YearRetriever:
    """Facade for album year retrieval and updates.

    This class coordinates the year retrieval subsystem, delegating
    actual work to specialized components while maintaining backward
    compatibility with existing code.
    """

    # Public constants (API contract - do not change)
    MIN_VALID_YEAR = 1900
    PARITY_THRESHOLD = 2
    TOP_YEARS_COUNT = 2
    DOMINANCE_MIN_SHARE = 0.6
    SUSPICIOUS_ALBUM_MIN_LEN = 3
    SUSPICIOUS_MANY_YEARS = 3
    MAX_RETRY_DELAY_SECONDS = 10.0
    DEFAULT_YEAR_DIFFERENCE_THRESHOLD = 5
    DEFAULT_FALLBACK_ENABLED = True
    DEFAULT_ABSURD_YEAR_THRESHOLD = 1970
    DEFAULT_SUSPICION_THRESHOLD_YEARS = 10

    # Backward compatibility: expose utility methods as static methods
    _resolve_non_negative_int = staticmethod(resolve_non_negative_int)
    _resolve_positive_int = staticmethod(resolve_positive_int)
    _resolve_non_negative_float = staticmethod(resolve_non_negative_float)
    normalize_collaboration_artist = staticmethod(normalize_collaboration_artist)

    def __init__(
        self,
        track_processor: TrackProcessor,
        cache_service: CacheServiceProtocol,
        external_api: ExternalApiServiceProtocol,
        pending_verification: PendingVerificationServiceProtocol,
        retry_handler: DatabaseRetryHandler,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics: Analytics,
        config: dict[str, Any],
        dry_run: bool = False,
    ) -> None:
        """Initialize the YearRetriever.

        Args:
            track_processor: Track processor for updating tracks
            cache_service: Cache service for storing years
            external_api: External API service for fetching years
            pending_verification: Service for managing pending verifications
            retry_handler: Retry handler for transient error recovery
            console_logger: Logger for console output
            error_logger: Logger for error messages
            analytics: Analytics instance for tracking
            config: Configuration dictionary
            dry_run: Whether to run in dry-run mode

        """
        # Store references for backward compatibility
        self.track_processor = track_processor
        self.cache_service = cache_service
        self.external_api = external_api
        self.pending_verification = pending_verification
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.analytics = analytics
        self.config = config
        self.dry_run = dry_run
        self._dry_run_actions: list[dict[str, Any]] = []
        self._last_updated_tracks: list[TrackDict] = []

        # Extract configuration
        year_config = self.config.get("year_retrieval", {}) if isinstance(self.config, dict) else {}
        fallback_config = year_config.get("fallback", {}) if isinstance(year_config, dict) else {}
        logic_config = year_config.get("logic", {}) if isinstance(year_config, dict) else {}

        # Configuration values (exposed for backward compatibility)
        self.fallback_enabled = bool(fallback_config.get("enabled", self.DEFAULT_FALLBACK_ENABLED))
        self.year_difference_threshold = resolve_positive_int(
            fallback_config.get("year_difference_threshold"),
            default=self.DEFAULT_YEAR_DIFFERENCE_THRESHOLD,
        )
        self.absurd_year_threshold = resolve_positive_int(
            logic_config.get("absurd_year_threshold"),
            default=self.DEFAULT_ABSURD_YEAR_THRESHOLD,
        )
        self.suspicion_threshold_years = resolve_positive_int(
            logic_config.get("suspicion_threshold_years"),
            default=self.DEFAULT_SUSPICION_THRESHOLD_YEARS,
        )

        # Initialize consistency checker
        self.year_consistency_checker = YearConsistencyChecker(
            console_logger=self.console_logger,
            top_years_count=self.TOP_YEARS_COUNT,
            parity_threshold=self.PARITY_THRESHOLD,
            dominance_min_share=self.DOMINANCE_MIN_SHARE,
            suspicion_threshold_years=self.suspicion_threshold_years,
        )

        # Initialize fallback handler
        self.year_fallback_handler = YearFallbackHandler(
            console_logger=self.console_logger,
            pending_verification=self.pending_verification,
            fallback_enabled=self.fallback_enabled,
            absurd_year_threshold=self.absurd_year_threshold,
            year_difference_threshold=self.year_difference_threshold,
            api_orchestrator=self.external_api,
        )

        # Initialize year determinator
        self._year_determinator = YearDeterminator(
            cache_service=self.cache_service,
            external_api=self.external_api,
            pending_verification=self.pending_verification,
            consistency_checker=self.year_consistency_checker,
            fallback_handler=self.year_fallback_handler,
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            config=self.config,
        )

        # Initialize batch processor
        self._batch_processor = YearBatchProcessor(
            year_determinator=self._year_determinator,
            track_processor=self.track_processor,
            retry_handler=retry_handler,
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            config=self.config,
            analytics=self.analytics,
            dry_run=self.dry_run,
        )

    # =========================================================================
    # Public API - Main Entry Points
    # =========================================================================

    async def process_album_years(
        self,
        tracks: list[TrackDict],
        force: bool = False,
    ) -> bool:
        """Process and update album years for given tracks.

        Args:
            tracks: Tracks to process
            force: Force update even if year exists (bypasses cache/skip checks)

        Returns:
            True if successful, False otherwise

        """
        if not self.config.get("year_retrieval", {}).get("enabled", True):
            self.console_logger.info("Year retrieval is disabled in config")
            return True

        try:
            self.console_logger.info("Starting album year updates (force=%s)", force)
            self._last_updated_tracks = []

            # Initialize external API service if not already initialized
            # Note: initialize() is idempotent - safe to call multiple times
            if not getattr(self.external_api, "_initialized", False):
                await self.external_api.initialize()

            # Run the update logic with force flag
            updated_tracks, _changes_log = await self._update_album_years_logic(tracks, force=force)
            self._last_updated_tracks = updated_tracks

            # Summary with detailed statistics
            albums_processed = len({f"{t.get('artist', '')} - {t.get('album', '')}" for t in tracks if t.get("album")})
            albums_with_empty_year = len([t for t in tracks if is_empty_year(t.get("year"))])

            self.console_logger.info(
                "Album year update complete: %d tracks updated from %d albums processed (%d had empty years)",
                len(updated_tracks),
                albums_processed,
                albums_with_empty_year,
            )

            if len(updated_tracks) == 0 and albums_with_empty_year > 0:
                self.console_logger.warning(
                    "No album years were updated despite %d albums having empty years. "
                    "This likely means APIs could not find release information for these albums.",
                    albums_with_empty_year,
                )

            # Generate report for problematic albums
            raw_min_attempts = self.config.get("reporting", {}).get("min_attempts_for_report", 3)
            min_attempts = resolve_positive_int(raw_min_attempts, default=3)
            problematic_count = await self.pending_verification.generate_problematic_albums_report(min_attempts=min_attempts)
            if problematic_count > 0:
                self.console_logger.warning(
                    "Found %d albums that failed to get year after %d+ attempts",
                    problematic_count,
                    min_attempts,
                )

        except (OSError, ValueError, RuntimeError):
            self.error_logger.exception("Error in the album year processing")
            return False

        return True

    async def get_album_years_with_logs(
        self,
        tracks: list[TrackDict],
        force: bool = False,
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Get album year updates with change logs.

        This is the public API for pipeline integration.

        Args:
            tracks: Tracks to process
            force: If True, bypass skip checks and re-query API for all albums

        Returns:
            Tuple of (updated_tracks, change_logs)

        """
        return await self._update_album_years_logic(tracks, force=force)

    @Analytics.track_instance_method("year_discogs_update")
    async def update_years_from_discogs(
        self,
        tracks: list[TrackDict],
        force: bool = False,
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Update years specifically from Discogs API.

        Args:
            tracks: Tracks to process
            force: If True, bypass skip checks and re-query API for all albums

        Returns:
            Tuple of (updated_tracks, change_logs)

        """
        return await self._update_album_years_logic(tracks, force=force)

    # =========================================================================
    # State Accessors
    # =========================================================================

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get a list of dry-run actions that would have been performed."""
        return self._batch_processor.get_dry_run_actions()

    def get_last_updated_tracks(self) -> list[TrackDict]:
        """Get the list of tracks updated in the last run."""
        return self._last_updated_tracks

    def set_last_updated_tracks(self, tracks: list[TrackDict]) -> None:
        """Set the list of last updated tracks.

        Args:
            tracks: List of updated tracks

        """
        self._last_updated_tracks = tracks

    # =========================================================================
    # Internal Methods
    # =========================================================================

    async def _update_album_years_logic(
        self,
        tracks: list[TrackDict],
        force: bool = False,
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Core logic for updating album years.

        Args:
            tracks: Tracks to process
            force: If True, bypass skip checks and re-query API for all albums

        Returns:
            Tuple of (updated_tracks, change_logs)

        """
        # Group tracks by album
        albums = YearBatchProcessor.group_tracks_by_album(tracks)
        self.console_logger.info("Processing %d albums for year updates", len(albums))

        # Initialize result containers
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        # Process albums in batches
        await self._batch_processor.process_albums_in_batches(albums, updated_tracks, changes_log, force=force)

        return updated_tracks, changes_log

    # =========================================================================
    # Backward Compatibility - Delegated Static Methods
    # =========================================================================

    @staticmethod
    def _group_tracks_by_album(
        tracks: list[TrackDict],
    ) -> dict[tuple[str, str], list[TrackDict]]:
        """Group tracks by album. Delegates to YearBatchProcessor."""
        return YearBatchProcessor.group_tracks_by_album(tracks)

    @staticmethod
    def _extract_future_years(album_tracks: list[TrackDict]) -> list[int]:
        """Extract future years. Delegates to YearDeterminator."""
        return YearDeterminator.extract_future_years(album_tracks)

    @staticmethod
    def _extract_release_years(album_tracks: list[TrackDict]) -> list[str]:
        """Extract release years. Delegates to YearDeterminator."""
        return YearDeterminator.extract_release_years(album_tracks)

    async def update_album_tracks_bulk_async(
        self,
        tracks: list[TrackDict],
        year: str,
        artist: str,
        album: str,
    ) -> tuple[int, int]:
        """Update year for multiple tracks.
        Delegates to YearBatchProcessor.

        Args:
            tracks: List of tracks to update.
            year: Year value to set.
            artist: Artist name for contextual logging.
            album: Album name for contextual logging.

        Returns:
            Tuple of (successful_count, failed_count).
        """
        return await self._batch_processor.update_album_tracks_bulk_async(
            tracks=tracks,
            year=year,
            artist=artist,
            album=album,
        )
