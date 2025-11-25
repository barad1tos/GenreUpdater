"""Year retrieval functionality for Music Genre Updater.

This module handles fetching and updating album years from external APIs.
"""

import asyncio
import logging
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.shared.data.models import ChangeLogEntry, TrackDict
from src.shared.data.protocols import (
    CacheServiceProtocol,
    ExternalApiServiceProtocol,
    PendingVerificationServiceProtocol,
)
from src.shared.data.album_type_detection import (
    AlbumType,
    YearHandlingStrategy,
    detect_album_type,
)
from src.shared.data.track_status import can_edit_metadata, filter_available_tracks, is_prerelease_status, is_subscription_status
from src.shared.data.validators import is_valid_year
from src.shared.monitoring import Analytics

from .track_processor import TrackProcessor

if TYPE_CHECKING:
    from collections.abc import Coroutine


def is_empty_year(year_value: Any) -> bool:
    """Check if a year value is considered empty.

    Args:
        year_value: Year value to check

    Returns:
        True if the year is empty (None, empty string, or whitespace-only)
    """
    return not year_value or not str(year_value).strip()


def _is_reasonable_year(year: str) -> bool:
    """Check if year looks reasonable.

    Args:
        year: Year string to validate

    Returns:
        True if year looks reasonable, False otherwise

    """
    try:
        y = int(year)
        # Reasonable = 1900 to current year + 1
        # DON'T block future years for new releases!
        current_year = datetime.now(UTC).year
        return YearRetriever.MIN_VALID_YEAR <= y <= current_year + 1
    except (ValueError, TypeError):
        return False


class YearRetriever:
    """Manages album year retrieval and updates."""

    # Constants for validation and logic thresholds
    MIN_VALID_YEAR = 1900  # Minimum reasonable year for music releases
    PARITY_THRESHOLD = 2  # Maximum track difference for year parity detection
    TOP_YEARS_COUNT = 2  # Number of top years to compare for parity detection
    # Require a strong majority before applying dominant year to all tracks
    DOMINANCE_MIN_SHARE = 0.6  # 60% of ALL album tracks must share the year
    # Safety guard for suspicious album names (e.g., overly short, likely truncated)
    SUSPICIOUS_ALBUM_MIN_LEN = 3  # album names with length <= 3 are suspicious
    SUSPICIOUS_MANY_YEARS = 3  # if >= 3 unique years present, skip auto updates
    MAX_RETRY_DELAY_SECONDS = 10.0  # Safety cap for configurable retry delay

    # Year fallback configuration defaults (can be overridden via config)
    DEFAULT_YEAR_DIFFERENCE_THRESHOLD = 5  # Max years difference before flagging as suspicious
    DEFAULT_FALLBACK_ENABLED = True  # Enable fallback system by default
    DEFAULT_ABSURD_YEAR_THRESHOLD = 1970  # Years before this are suspicious for modern artists

    @staticmethod
    def _resolve_non_negative_int(value: Any, default: int) -> int:
        """Convert arbitrary value to non-negative int with fallback."""
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return default
        return candidate if candidate >= 0 else default

    @staticmethod
    def _resolve_positive_int(value: Any, default: int) -> int:
        """Convert arbitrary value to strictly positive int with fallback."""
        result = YearRetriever._resolve_non_negative_int(value, default)
        return result if result > 0 else default

    @staticmethod
    def _resolve_non_negative_float(value: Any, default: float) -> float:
        """Convert arbitrary value to non-negative float with fallback."""
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            return default
        return candidate if candidate >= 0 else default

    def __init__(
        self,
        track_processor: TrackProcessor,
        cache_service: CacheServiceProtocol,
        external_api: ExternalApiServiceProtocol,
        pending_verification: PendingVerificationServiceProtocol,
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
            console_logger: Logger for console output
            error_logger: Logger for error messages
            analytics: Analytics instance for tracking
            config: Configuration dictionary
            dry_run: Whether to run in dry-run mode

        """
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

        year_config = self.config.get("year_retrieval", {}) if isinstance(self.config, dict) else {}
        processing_config = year_config.get("processing", {}) if isinstance(year_config, dict) else {}

        self.skip_prerelease = bool(processing_config.get("skip_prerelease", True))
        self.future_year_threshold = YearRetriever._resolve_non_negative_int(processing_config.get("future_year_threshold"), default=1)
        self.prerelease_recheck_days = YearRetriever._resolve_positive_int(
            processing_config.get("prerelease_recheck_days"),
            default=30,
        )

        self.track_retry_attempts = YearRetriever._resolve_positive_int(self.config.get("max_retries"), default=3)
        self.track_retry_delay = YearRetriever._resolve_non_negative_float(self.config.get("retry_delay_seconds"), default=1.0)

        # Logic configuration (year validation thresholds)
        logic_config = year_config.get("logic", {}) if isinstance(year_config, dict) else {}
        self.absurd_year_threshold = YearRetriever._resolve_positive_int(
            logic_config.get("absurd_year_threshold"),
            default=self.DEFAULT_ABSURD_YEAR_THRESHOLD,
        )

        # Fallback configuration (for handling uncertain year updates)
        fallback_config = year_config.get("fallback", {}) if isinstance(year_config, dict) else {}
        self.fallback_enabled = bool(fallback_config.get("enabled", self.DEFAULT_FALLBACK_ENABLED))
        self.year_difference_threshold = YearRetriever._resolve_positive_int(
            fallback_config.get("year_difference_threshold"),
            default=self.DEFAULT_YEAR_DIFFERENCE_THRESHOLD,
        )

    @staticmethod
    def _extract_future_years(album_tracks: list[TrackDict]) -> list[int]:
        """Extract future years from album tracks.

        Args:
            album_tracks: List of tracks from the album

        Returns:
            List of years that are in the future

        """
        current_year = datetime.now(UTC).year
        future_years: list[int] = []

        for track in album_tracks:
            year_value = track.get("year")
            if year_value is not None:
                try:
                    year_int = int(float(str(year_value)))
                    if year_int > current_year:
                        future_years.append(year_int)
                except (ValueError, TypeError):
                    # Skip invalid year values
                    continue

        return future_years

    @staticmethod
    def _extract_release_years(album_tracks: list[TrackDict]) -> list[str]:
        """Extract valid release years from album tracks.

        Args:
            album_tracks: List of tracks from the album

        Returns:
            List of valid release years from track metadata

        """
        release_years: list[str] = []
        for track in album_tracks:
            release_year_value = track.get("release_year")
            if release_year_value and is_valid_year(release_year_value):
                # Convert to string to ensure type safety
                release_years.append(str(release_year_value))
        return release_years

    async def _handle_future_years_found(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
        future_years: list[int],
    ) -> bool:
        """Handle case when future years are found in tracks.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: Album tracks
            future_years: List of future years found

        Returns:
            True if album should be skipped

        """
        if not self.skip_prerelease or not future_years:
            return False

        current_year = datetime.now(UTC).year
        max_future_year = max(future_years)

        if max_future_year - current_year <= self.future_year_threshold:
            self.console_logger.debug(
                "Detected future year %s for '%s - %s' but within configured threshold (%d year(s)); continuing processing",
                max_future_year,
                artist,
                album,
                self.future_year_threshold,
            )
            return False

        self.console_logger.info(
            "Skipping prerelease album '%s - %s' with future year(s): %s",
            artist,
            album,
            max_future_year,
        )
        metadata = {
            "expected_year": str(max_future_year),
            "track_count": str(len(album_tracks)),
        }
        await self.pending_verification.mark_for_verification(
            artist,
            album,
            reason="prerelease",
            metadata=metadata,
            recheck_days=self.prerelease_recheck_days,
        )
        return True

    async def _handle_release_years_found(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],  # Currently unused but may be needed for future logic
        release_years: list[str],
    ) -> str | None:
        """Handle case when release years are found in track metadata.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: Album tracks (currently unused but reserved for future logic)
            release_years: List of release years found

        Returns:
            Year string if valid, None if it should skip

        """
        _ = album_tracks  # Explicitly mark as unused for linter
        year_counts = Counter(release_years)
        most_common_year = year_counts.most_common(1)[0][0]

        # DELETE: Trust the system, no arbitrary future year blocking
        self.console_logger.info(
            "Using release year %s from Music.app metadata for '%s - %s' (fallback)",
            most_common_year,
            artist,
            album,
        )

        # Store in the cache for future use
        await self.cache_service.store_album_year_in_cache(artist, album, most_common_year)
        return most_common_year

    def _validate_track_ids(self, track_ids: list[str], year: str) -> list[str]:
        """Validate the track IDs before the bulk update.

        Args:
            track_ids: List of track IDs to validate
            year: Year to be applied

        Returns:
            List of valid track IDs

        """
        valid_ids: list[str] = []
        for track_id in track_ids:
            if track_id:
                valid_ids.append(track_id)
            else:
                self.error_logger.warning(
                    "Invalid track ID: empty or None for year %s",
                    year,
                )
        return valid_ids

    async def _update_track_with_retry(
        self,
        track_id: str,
        new_year: str,
        max_retries: int | None = None,
    ) -> bool:
        """Update a track's year with exponential backoff retry logic.

        Args:
            track_id: Track ID to update
            new_year: Year to set
            max_retries: Optional override for retry attempts

        Returns:
            True if successful, False otherwise

        """
        attempts = self.track_retry_attempts if max_retries is None else YearRetriever._resolve_positive_int(max_retries, self.track_retry_attempts)
        retry_delay = self.track_retry_delay if self.track_retry_delay > 0 else 1.0
        retry_delay = min(retry_delay, self.MAX_RETRY_DELAY_SECONDS)
        last_exception: Exception | None = None

        for attempt in range(attempts):
            try:
                result = await self.track_processor.update_track_async(
                    track_id=track_id,
                    new_year=new_year,
                )

                if result:
                    return True

                # If the result is False but no exception, it might be a no-change scenario
                self.console_logger.debug(
                    "Update returned False for track %s (attempt %d/%d)",
                    track_id,
                    attempt + 1,
                    attempts,
                )

            except (OSError, ValueError, RuntimeError) as e:
                last_exception = e
                if attempt < attempts - 1:
                    # Add jitter to prevent thundering herd
                    jitter = 0.1 * retry_delay
                    sleep_time = retry_delay + random.uniform(-jitter, jitter)  # noqa: S311
                    self.console_logger.warning(
                        "Failed to update year for track %s (attempt %d/%d): %s. Retrying in %.1fs...",
                        track_id,
                        attempt + 1,
                        attempts,
                        last_exception,
                        sleep_time,
                    )
                    await asyncio.sleep(sleep_time)
                retry_delay = min(retry_delay * 2, self.MAX_RETRY_DELAY_SECONDS)  # Exponential backoff with cap
            else:
                return False

        # If we reach here, all attempts failed
        if last_exception:
            self.error_logger.exception(
                "Failed to update year for track %s after %d attempts",
                track_id,
                attempts,
            )
        return False

    @Analytics.track_instance_method("year_update_bulk")
    async def update_album_tracks_bulk_async(
        self,
        track_ids: list[str],
        year: str,
    ) -> tuple[int, int]:
        """Update year for multiple tracks in bulk.

        Args:
            track_ids: List of track IDs to update
            year: Year to set

        Returns:
            Tuple of (successful_count, failed_count)

        """
        # Validate inputs
        valid_track_ids = self._validate_track_ids(track_ids, year)
        if not valid_track_ids:
            self.console_logger.warning("No valid track IDs to update")
            return 0, len(track_ids)

        # Process in batches
        batch_size = self.config.get("apple_script_concurrency", 2)
        successful = 0
        failed = 0

        for i in range(0, len(valid_track_ids), batch_size):
            batch = valid_track_ids[i : i + batch_size]

            # Create update tasks with retry logic
            tasks: list[Coroutine[Any, Any, bool]] = []
            for track_id in batch:
                task = self._update_track_with_retry(
                    track_id=track_id,
                    new_year=year,
                )
                tasks.append(task)

            # Execute batch
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count results
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    self.error_logger.error("Failed to update track: %s", result)
                elif result:
                    successful += 1
                else:
                    failed += 1

        # Log summary
        self.console_logger.info(
            "Year update results: %d successful, %d failed",
            successful,
            failed,
        )

        return successful, failed

    @staticmethod
    def normalize_collaboration_artist(artist: str) -> str:
        """Normalize collaboration artists to main artist.

        For collaborations like "Main Artist & Other" or "Main Artist feat. Other",
        extract the main artist to group all tracks together.

        Args:
            artist: Artist name potentially containing collaborations

        Returns:
            Main artist name for grouping
        """
        # Common collaboration separators
        separators = [" & ", " feat. ", " feat ", " ft. ", " ft ", " vs. ", " vs ", " with ", " and ", " x ", " X "]

        return next(
            (artist.split(separator)[0].strip() for separator in separators if separator in artist),
            artist,
        )

    @staticmethod
    def _group_tracks_by_album(
        tracks: list[TrackDict],
    ) -> dict[tuple[str, str], list[TrackDict]]:
        """Group tracks by album (album_artist, album) key.

        Uses album_artist instead of artist to properly handle collaboration tracks
        where multiple artists appear on the same album.

        Args:
            tracks: List of tracks to group

        Returns:
            Dictionary mapping (album_artist, album) tuples to lists of tracks

        """
        albums: dict[tuple[str, str], list[TrackDict]] = defaultdict(list)
        for track in tracks:
            # Use album_artist instead of artist for grouping to handle collaborations properly
            # This ensures tracks with different artists but same album_artist are grouped together
            album_artist = str(track.get("album_artist", ""))
            album = str(track.get("album", ""))

            # Fallback to normalized artist if album_artist is empty
            if not album_artist or not album_artist.strip():
                raw_artist = str(track.get("artist", ""))
                album_artist = YearRetriever.normalize_collaboration_artist(raw_artist)

            album_key = (album_artist, album)
            albums[album_key].append(track)
        return albums

    class _AlbumProcessingProgress:
        """Track album processing progress and emit informative logs."""

        def __init__(self, total: int, logger: logging.Logger) -> None:
            self.total = total
            self.logger = logger
            self.processed = 0
            self.lock = asyncio.Lock()
            self.interval = max(1, total // 10) if total > 0 else 1

        async def record(self) -> None:
            """Increment processed counter and log progress when appropriate."""
            async with self.lock:
                self.processed += 1
                if self.processed % self.interval == 0 or self.processed == self.total:
                    self.logger.info("Album processing progress: %d/%d", self.processed, self.total)

    def _warn_legacy_year_config(self, year_config: dict[str, Any]) -> None:
        """Emit warnings when user still relies on the legacy config format."""
        if "batch_size" in year_config and "processing" not in year_config:
            self.console_logger.warning(
                "⚠️ Legacy config detected: 'year_retrieval.batch_size' should be 'year_retrieval.processing.batch_size'. "
                "Update your config file for optimal performance."
            )
        if "delay_between_batches" in year_config and "processing" not in year_config:
            self.console_logger.warning(
                "⚠️ Legacy config detected: 'year_retrieval.delay_between_batches' should be 'year_retrieval.processing.delay_between_batches'. "
                "Update your config file for optimal performance."
            )

    @staticmethod
    def _get_processing_settings(year_config: dict[str, Any]) -> tuple[int, int, bool]:
        """Extract batch processing settings with sane fallbacks."""
        processing_config = year_config.get("processing", {})
        batch_size_raw = processing_config.get("batch_size", 10)
        delay_raw = processing_config.get("delay_between_batches", 60)
        adaptive_delay_raw = processing_config.get("adaptive_delay", False)

        try:
            batch_size = int(batch_size_raw)
        except (TypeError, ValueError):
            batch_size = 10

        try:
            delay_between_batches = int(delay_raw)
        except (TypeError, ValueError):
            delay_between_batches = 60

        adaptive_delay = bool(adaptive_delay_raw)
        return max(1, batch_size), max(0, delay_between_batches), adaptive_delay

    def _determine_concurrency_limit(self, year_config: dict[str, Any]) -> int:
        """Compute concurrency limit based on AppleScript and API limits."""
        api_concurrency_raw = year_config.get("rate_limits", {}).get("concurrent_api_calls")
        apple_script_concurrency_raw = self.config.get("apple_script_concurrency", 1)

        try:
            apple_script_concurrency = int(apple_script_concurrency_raw)
        except (TypeError, ValueError):
            apple_script_concurrency = 1

        try:
            api_concurrency = int(api_concurrency_raw) if api_concurrency_raw is not None else None
        except (TypeError, ValueError):
            api_concurrency = None

        if api_concurrency is None or api_concurrency <= 0:
            return max(1, apple_script_concurrency)
        return max(1, min(apple_script_concurrency, api_concurrency))

    @staticmethod
    def _should_use_sequential_processing(adaptive_delay: bool, concurrency_limit: int) -> bool:
        """Return True when the legacy sequential mode should remain active."""
        return not adaptive_delay and concurrency_limit == 1

    async def _process_batches_sequentially(
        self,
        album_items: list[tuple[tuple[str, str], list[TrackDict]]],
        batch_size: int,
        delay_between_batches: int,
        total_batches: int,
        total_albums: int,
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
    ) -> None:
        """Process albums strictly sequentially with explicit pauses."""
        for batch_start in range(0, total_albums, batch_size):
            batch_end = min(batch_start + batch_size, total_albums)
            batch_index = batch_start // batch_size + 1

            self.console_logger.info("Processing batch %d/%d", batch_index, total_batches)

            for album_key, album_tracks in album_items[batch_start:batch_end]:
                artist_name, album_name = album_key
                self.console_logger.debug("DEBUG: About to process album '%s - %s'", artist_name, album_name)
                await self._process_single_album(artist_name, album_name, album_tracks, updated_tracks, changes_log)

            if batch_end < total_albums and delay_between_batches > 0:
                self.console_logger.info("Waiting %d seconds before next batch...", delay_between_batches)
                await asyncio.sleep(delay_between_batches)

    async def _process_album_entry(
        self,
        album_index: int,
        total_albums: int,
        album_entry: tuple[tuple[str, str], list[TrackDict]],
        semaphore: asyncio.Semaphore,
        progress: _AlbumProcessingProgress,
        concurrency_limit: int,
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
    ) -> None:
        """Process a single album within concurrency limits and update progress."""
        album_key, album_tracks = album_entry
        artist_name, album_name = album_key

        self.console_logger.debug(
            "DEBUG: Queued album %d/%d '%s - %s' (concurrency=%d)",
            album_index + 1,
            total_albums,
            artist_name,
            album_name,
            concurrency_limit,
        )

        async with semaphore:
            self.console_logger.debug("DEBUG: About to process album '%s - %s'", artist_name, album_name)
            await self._process_single_album(artist_name, album_name, album_tracks, updated_tracks, changes_log)

        await progress.record()

    async def _process_batches_concurrently(
        self,
        album_items: list[tuple[tuple[str, str], list[TrackDict]]],
        batch_size: int,
        total_batches: int,
        total_albums: int,
        concurrency_limit: int,
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
        adaptive_delay: bool,
    ) -> None:
        """Process albums concurrently using adaptive pacing and shared semaphore."""
        semaphore = asyncio.Semaphore(concurrency_limit)
        progress = YearRetriever._AlbumProcessingProgress(total_albums, self.console_logger)

        for batch_start in range(0, total_albums, batch_size):
            batch_end = min(batch_start + batch_size, total_albums)
            batch_index = batch_start // batch_size + 1
            batch_slice = album_items[batch_start:batch_end]

            self.console_logger.info(
                "Processing batch %d/%d (size=%d, concurrency=%d, adaptive_delay=%s)",
                batch_index,
                total_batches,
                len(batch_slice),
                concurrency_limit,
                adaptive_delay,
            )

            async with asyncio.TaskGroup() as task_group:
                for offset, album_entry in enumerate(batch_slice):
                    album_position = batch_start + offset
                    task_group.create_task(
                        self._process_album_entry(
                            album_position,
                            total_albums,
                            album_entry,
                            semaphore,
                            progress,
                            concurrency_limit,
                            updated_tracks,
                            changes_log,
                        )
                    )

            self.console_logger.info(
                "Completed batch %d/%d (%d/%d albums processed)",
                batch_index,
                total_batches,
                batch_end,
                total_albums,
            )

    def _should_skip_album_due_to_existing_years(self, album_tracks: list[TrackDict], artist: str, album: str) -> bool:
        """Check if the album should be skipped due to existing years.

        Skip only if ALL tracks have the SAME valid year.
        Process if: empty years OR inconsistent years (for dominant year logic).

        Args:
            album_tracks: List of tracks in the album
            artist: Artist name for logging
            album: Album name for logging

        Returns:
            True if the album should be skipped, False otherwise

        """
        if tracks_with_empty_year := [track for track in album_tracks if is_empty_year(track.get("year"))]:
            self.console_logger.info(
                "Album '%s - %s' has %d tracks with empty/null year - will process",
                artist,
                album,
                len(tracks_with_empty_year),
            )
            return False

        # 2. Collect all non-empty years
        non_empty_years = [
            str(track.get("year"))
            for track in album_tracks
            if track.get("year") and str(track.get("year")).strip() and is_valid_year(track.get("year"))
        ]

        if not non_empty_years:
            self.console_logger.debug("Album '%s - %s' has no valid years - will process", artist, album)
            return False

        # 3. Check for year consistency - ONLY skip if ALL tracks have SAME year AND SAME release_year
        unique_years = set(non_empty_years)

        # Also check release_year consistency
        non_empty_release_years = [
            str(track.get("release_year"))
            for track in album_tracks
            if track.get("release_year") and str(track.get("release_year")).strip() and is_valid_year(track.get("release_year"))
        ]
        unique_release_years = set(non_empty_release_years) if non_empty_release_years else set()

        if len(unique_years) == 1 and len(unique_release_years) <= 1:
            # All tracks have the same valid year AND consistent release_year - skip
            year = next(iter(unique_years))
            release_year = next(iter(unique_release_years)) if unique_release_years else "N/A"
            self.console_logger.debug(
                "Skipping '%s - %s' (all %d tracks have same year: %s, release_year: %s)",
                artist,
                album,
                len(non_empty_years),
                year,
                release_year,
            )
            return True

        # Check if we need to process due to release_year inconsistency
        if len(unique_years) == 1:
            self.console_logger.info(
                "Album '%s - %s' has consistent year (%s) but inconsistent release_years: %s - will process",
                artist,
                album,
                next(iter(unique_years)),
                ", ".join(f"{ry}" for ry in unique_release_years),
            )
            return False

        # Multiple different years - process for dominant year logic
        year_counts = Counter(non_empty_years)
        most_common = year_counts.most_common(2)
        self.console_logger.info(
            "Album '%s - %s' has inconsistent years: %s - will apply dominant year logic",
            artist,
            album,
            ", ".join(f"{year}({count})" for year, count in most_common),
        )
        return False

    async def _determine_album_year(self, artist: str, album: str, album_tracks: list[TrackDict]) -> str | None:
        """Determine year for album using simplified Linus approach.

        Order: dominant year -> consensus release_year -> API -> None

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks in the album

        Returns:
            Year string if found, None otherwise

        """
        self.console_logger.info(
            "[YEAR_DEBUG] _determine_album_year called: artist='%s' album='%s'",
            artist,
            album,
        )

        if dominant_year := self._get_dominant_year(album_tracks):
            # Using dominant year from tracks (noise reduction)
            return dominant_year

        if consensus_year := self._get_consensus_release_year(album_tracks):
            # Using consensus release_year (noise reduction)
            # Cache for future use
            await self.cache_service.store_album_year_in_cache(artist, album, consensus_year)
            return consensus_year

        # 3. Check cache (might have from previous API call)
        cached_year = await self.cache_service.get_album_year_from_cache(artist, album)
        if cached_year:
            # Using cached year (noise reduction)
            return cached_year

        # 4. API as last resort (no local data or parity situation)
        # Calling external API for year (noise reduction)
        try:
            year_result, is_definitive = await self.external_api.get_album_year(artist, album)
            # API returned result (noise reduction)
        except (OSError, ValueError, RuntimeError) as e:
            self.console_logger.exception("[YEAR_DEBUG] Exception in get_album_year: %s", e)
            self.error_logger.exception("[YEAR_DEBUG] Full exception details:")
            return None

        if year_result:
            # Apply fallback logic to validate/skip the proposed year
            validated_year = await self._apply_year_fallback(
                proposed_year=year_result,
                album_tracks=album_tracks,
                is_definitive=is_definitive,
                artist=artist,
                album=album,
            )

            if validated_year is not None:
                # Store validated year in cache
                await self.cache_service.store_album_year_in_cache(artist, album, validated_year)
                return validated_year

        # No year found anywhere OR fallback rejected update
        return None

    async def _check_album_prerelease_status(self, artist: str, album: str, album_tracks: list[TrackDict]) -> bool:
        """Check if the album should be skipped due to prerelease status.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks on the album

        Returns:
            True if the album should be skipped, False otherwise

        """
        if not self.skip_prerelease:
            return False

        prerelease_tracks = [
            track
            for track in album_tracks
            if is_prerelease_status(track.track_status if isinstance(track.track_status, str) else None)
        ]
        if prerelease_tracks:
            self.console_logger.info(
                "Skipping album '%s - %s': %d of %d tracks are prerelease (read-only)",
                artist,
                album,
                len(prerelease_tracks),
                len(album_tracks),
            )
            # Mark for future verification
            await self.pending_verification.mark_for_verification(
                artist,
                album,
                reason="prerelease",
                metadata={
                    "track_count": str(len(album_tracks)),
                    "prerelease_count": str(len(prerelease_tracks)),
                },
                recheck_days=self.prerelease_recheck_days,
            )
            return True
        return False

    def _identify_tracks_needing_update(self, album_tracks: list[TrackDict], year: str) -> tuple[list[str], list[TrackDict]]:
        """Identify which tracks need year updates.

        Args:
            album_tracks: List of tracks in the album
            year: Target year to set

        Returns:
            Tuple of (track_ids, tracks_needing_update)

        """
        seen_ids: set[str] = set()
        track_ids: list[str] = []
        tracks_needing_update: list[TrackDict] = []

        for track in album_tracks:
            if not (track_id_value := track.get("id", "")):
                continue

            track_id = str(track_id_value)

            # Skip duplicates
            if track_id in seen_ids:
                continue
            seen_ids.add(track_id)
            track_current_year = track.get("year", "")
            track_status = track.track_status if isinstance(track.track_status, str) else None
            # Skip read-only tracks (e.g., prerelease)
            if not can_edit_metadata(track_status):
                self.console_logger.debug(
                    "Skipping read-only track %s (status: %s)",
                    track_id,
                    track_status or "unknown",
                )
                continue

            if YearRetriever._track_needs_year_update(track_current_year, year):
                track_ids.append(track_id)
                tracks_needing_update.append(track)
                self.console_logger.debug(
                    "Track %s needs year update from '%s' to '%s'",
                    track_id,
                    track_current_year or "empty",
                    year,
                )
            else:
                self.console_logger.debug("Track %s already has correct year %s, skipping", track_id, year)

        return track_ids, tracks_needing_update

    @staticmethod
    def _track_needs_year_update(current_year: str | int | None, target_year: str) -> bool:
        """Check if a track needs its year updated.

        Args:
            current_year: Current year value (may be None, empty, or string/int)
            target_year: Target year to set

        Returns:
            True if track needs update, False otherwise

        """
        # Treat empty/null year as needing update
        if is_empty_year(current_year):
            return True
        # Update if year is different
        return str(current_year) != target_year

    # -------------------------------------------------------------------------
    # Year Fallback Methods
    # -------------------------------------------------------------------------

    @staticmethod
    def _get_existing_year_from_tracks(tracks: list[TrackDict]) -> str | None:
        """Extract most common existing year from tracks.

        Uses Counter to find the most frequently occurring year among tracks.

        Args:
            tracks: List of tracks to analyze

        Returns:
            Most common year string, or None if no valid years found
        """
        years = [
            str(track.get("year"))
            for track in tracks
            if track.get("year") and not is_empty_year(track.get("year"))
        ]
        if not years:
            return None
        # Return most common year
        counter = Counter(years)
        most_common = counter.most_common(1)
        return most_common[0][0] if most_common else None

    def _is_year_change_dramatic(self, existing: str, proposed: str) -> bool:
        """Check if year change exceeds threshold.

        A dramatic change (e.g., 2018→1998) suggests the API returned
        a reissue/compilation year rather than the original.

        Args:
            existing: Current year value
            proposed: Proposed new year value

        Returns:
            True if difference exceeds YEAR_DIFFERENCE_THRESHOLD
        """
        try:
            existing_int = int(existing)
            proposed_int = int(proposed)
            difference = abs(existing_int - proposed_int)
            return difference > self.year_difference_threshold
        except (ValueError, TypeError):
            return False

    async def _apply_year_fallback(
        self,
        proposed_year: str,
        album_tracks: list[TrackDict],
        is_definitive: bool,
        artist: str,
        album: str,
    ) -> str | None:
        """Combined fallback logic for year decisions.

        Decision Tree:
        1. IF is_definitive=True → APPLY year (high confidence from API)
        2. IF proposed_year < absurd_threshold AND no existing year → MARK + SKIP
           (Catches absurd years like Gorillaz→1974 when no existing to compare)
        3. IF existing year is EMPTY → APPLY year (nothing to preserve, year is reasonable)
        4. IF is_special_album_type → MARK for verification + SKIP/UPDATE
        5. IF |proposed - existing| > THRESHOLD → MARK + PRESERVE existing
        6. ELSE → APPLY year

        Args:
            proposed_year: Year from API
            album_tracks: List of tracks in the album
            is_definitive: Whether API is confident in the year
            artist: Artist name
            album: Album name

        Returns:
            Year to apply (proposed or existing), or None to skip update
        """
        # Fallback disabled - use original behavior (always apply, mark low confidence)
        if not self.fallback_enabled:
            if not is_definitive:
                await self.pending_verification.mark_for_verification(artist, album)
            return proposed_year

        # Rule 1: High confidence from API - apply directly
        if is_definitive:
            self.console_logger.debug(
                "[FALLBACK] Applying year %s for %s - %s (high confidence)",
                proposed_year, artist, album
            )
            return proposed_year

        # Get existing year from tracks (needed for subsequent rules)
        existing_year = self._get_existing_year_from_tracks(album_tracks)

        # Rule 2: Absurd year detection (when no existing year to compare)
        # Catches cases like Gorillaz→1974 (band formed 1998) without artist period data
        try:
            proposed_int = int(proposed_year)
            is_absurd = proposed_int < self.absurd_year_threshold
        except (ValueError, TypeError):
            is_absurd = False

        if is_absurd and not existing_year:
            await self.pending_verification.mark_for_verification(
                artist=artist,
                album=album,
                reason="absurd_year_no_existing",
                metadata={
                    "proposed_year": proposed_year,
                    "absurd_threshold": self.absurd_year_threshold,
                    "confidence": "very_low",
                },
            )
            self.console_logger.warning(
                "[FALLBACK] Skipping absurd year %s for %s - %s "
                "(year < %d threshold, no existing year to validate)",
                proposed_year, artist, album, self.absurd_year_threshold
            )
            return None  # Skip update - absurd year with no baseline

        # Rule 3: No existing year - nothing to preserve (year passed absurd check)
        if not existing_year:
            self.console_logger.debug(
                "[FALLBACK] Applying year %s for %s - %s (no existing year to preserve)",
                proposed_year, artist, album
            )
            return proposed_year

        # Rule 4: Check for special album types
        album_info = detect_album_type(album)
        if album_info.album_type != AlbumType.NORMAL:
            reason = f"special_album_{album_info.album_type.value}"
            await self.pending_verification.mark_for_verification(
                artist=artist,
                album=album,
                reason=reason,
                metadata={
                    "existing_year": existing_year,
                    "proposed_year": proposed_year,
                    "album_type": album_info.album_type.value,
                    "detected_pattern": album_info.detected_pattern,
                    "confidence": "low",
                },
            )

            if album_info.strategy == YearHandlingStrategy.MARK_AND_SKIP:
                self.console_logger.warning(
                    "[FALLBACK] Skipping year update for %s - %s "
                    "(special album type: %s, pattern: '%s'). "
                    "Existing: %s, Proposed: %s",
                    artist, album, album_info.album_type.value,
                    album_info.detected_pattern, existing_year, proposed_year
                )
                return None  # Skip update
            # MARK_AND_UPDATE: continue with proposed year
            self.console_logger.info(
                "[FALLBACK] Updating year for %s - %s (reissue detected: %s)",
                artist, album, album_info.detected_pattern
            )
            return proposed_year

        # Rule 5: Check for dramatic year change
        if self._is_year_change_dramatic(existing_year, proposed_year):
            await self.pending_verification.mark_for_verification(
                artist=artist,
                album=album,
                reason="suspicious_year_change",
                metadata={
                    "existing_year": existing_year,
                    "proposed_year": proposed_year,
                    "year_difference": abs(int(existing_year) - int(proposed_year)),
                    "confidence": "low",
                },
            )
            self.console_logger.warning(
                "[FALLBACK] Preserving existing year %s for %s - %s "
                "(dramatic change to %s detected, diff > %d years)",
                existing_year, artist, album, proposed_year,
                self.year_difference_threshold
            )
            return None  # Skip update, preserve existing

        # Rule 6: Apply year (low confidence but reasonable change)
        self.console_logger.debug(
            "[FALLBACK] Applying year %s for %s - %s (low confidence but reasonable change)",
            proposed_year, artist, album
        )
        return proposed_year

    @staticmethod
    def _create_updated_track(track: TrackDict, year: str) -> TrackDict:
        """Create an updated track with the new year.

        Args:
            track: Original track
            year: New year to set

        Returns:
            Updated TrackDict with new year

        """
        # Use TrackDict's copy method with keyword arguments
        return track.copy(year=year)

    @staticmethod
    def _create_change_entry(track: TrackDict, artist: str, album: str, year: str) -> ChangeLogEntry:
        """Create a change log entry for the track update.

        Args:
            track: The track being updated
            artist: Artist name
            album: Album name
            year: New year value

        Returns:
            Change log entry object

        """
        return ChangeLogEntry(
            timestamp=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            change_type="year_update",
            track_id=str(track.get("id", "")),
            artist=artist,
            album_name=album,
            track_name=str(track.get("name", "")),
            old_year=(
                str(track.get("year")) if track.get("year") is not None else ""
            ),
            new_year=year if year is not None else "",
        )

    async def _update_tracks_for_album(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
        year: str,
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
    ) -> None:
        """Update tracks for a specific album and record changes.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks in the album
            year: Year to set
            updated_tracks: List to append updated tracks to
            changes_log: List to append change entries to

        """
        track_ids, tracks_needing_update = self._identify_tracks_needing_update(album_tracks, year)

        if not track_ids:
            self.console_logger.info(
                "All tracks for '%s - %s' already have year %s, skipping update",
                artist,
                album,
                year,
            )
            return

        successful, _ = await self.update_album_tracks_bulk_async(track_ids, year)

        if successful > 0:
            for track in tracks_needing_update:
                updated_track = YearRetriever._create_updated_track(track, year)
                updated_tracks.append(updated_track)

                change_entry = YearRetriever._create_change_entry(track, artist, album, year)
                # Ensure new_year always has a value - use actually set year if available
                if not change_entry.new_year and updated_track.year:
                    change_entry.new_year = str(updated_track.year)
                changes_log.append(change_entry)

                # Keep the in-memory snapshot aligned with Music.app for downstream sync
                track.year = year
                track.new_year = year

    @staticmethod
    def _get_available_tracks(album_tracks: list[TrackDict]) -> list[TrackDict]:
        """Get tracks that are available for processing (exclude prerelease).

        Args:
            album_tracks: List of tracks in the album

        Returns:
            List of available tracks

        """
        return filter_available_tracks(album_tracks)

    def _handle_no_year_found(self, artist: str, album: str, album_tracks: list[TrackDict]) -> None:
        """Handle case when no year could be determined for the album.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks in the album

        """
        if available_tracks := self._get_available_tracks(album_tracks):
            # For available tracks without a year, we still want to log this
            self.console_logger.warning(
                "Album '%s - %s' has %d available tracks but no year could be determined. Tracks remain unchanged.",
                artist,
                album,
                len(available_tracks),
            )
        else:
            self.console_logger.debug(
                "Skipping '%s - %s' - no year found and no available tracks",
                artist,
                album,
            )

    async def _check_suspicious_album(self, artist: str, album: str, album_tracks: list[TrackDict]) -> bool:
        """Check if album is suspicious and should be skipped.

        Returns:
            True if album should be skipped, False otherwise
        """
        try:
            album_str = album or ""
            non_empty_years = [str(track.get("year")) for track in album_tracks if track.get("year") and str(track.get("year")).strip()]
            unique_years = set(non_empty_years) if non_empty_years else set()
            if len(album_str) <= self.SUSPICIOUS_ALBUM_MIN_LEN and len(unique_years) >= self.SUSPICIOUS_MANY_YEARS:
                self.console_logger.warning(
                    "Safety check: Suspicious album '%s - %s' detected (%d unique years, name length=%d). "
                    "Skipping automatic updates and marking for verification.",
                    artist,
                    album,
                    len(unique_years),
                    len(album_str),
                )
                await self.pending_verification.mark_for_verification(
                    artist,
                    album,
                    reason="suspicious_album_name",
                    metadata={
                        "unique_years": str(len(unique_years)),
                        "album_name_length": str(len(album_str)),
                    },
                )
                return True
        except (AttributeError, TypeError, ValueError) as e:
            # Do not fail album processing because of guard logic errors
            self.error_logger.exception("Error during suspicious album safety check for '%s - %s': %s", artist, album, e)
        return False

    async def _process_dominant_year(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
        dominant_year: str,
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
    ) -> bool:
        """Process album using dominant year logic.

        Returns:
            True if processing was completed, False if should continue with regular year determination
        """
        # Check for inconsistent years in the album
        non_empty_years = [str(track.get("year")) for track in album_tracks if track.get("year") and str(track.get("year")).strip()]
        unique_years = set(non_empty_years) if non_empty_years else set()

        # Apply dominant year if there are empty tracks OR inconsistent years
        tracks_needing_update = [track for track in album_tracks if is_empty_year(track.get("year"))]

        # Add tracks with inconsistent years (if album has multiple different years)
        if len(unique_years) > 1:
            tracks_needing_update.extend([track for track in album_tracks if track.get("year") and str(track.get("year")).strip() != dominant_year])

        if tracks_needing_update := list({track.get("id"): track for track in tracks_needing_update}.values()):
            empty_count = len([t for t in tracks_needing_update if is_empty_year(t.get("year"))])
            inconsistent_count = len(tracks_needing_update) - empty_count

            self.console_logger.info(
                "Applying dominant year %s to %d tracks (%d empty, %d inconsistent) in '%s - %s'",
                dominant_year,
                len(tracks_needing_update),
                empty_count,
                inconsistent_count,
                artist,
                album,
            )
            # Apply dominant year directly to tracks and update Music.app
            await self._update_tracks_for_album(artist, album, tracks_needing_update, dominant_year, updated_tracks, changes_log)
            return True
        return False

    async def _process_single_album(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
    ) -> None:
        """Process a single album for year updates.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks in the album
            updated_tracks: List to append updated tracks to
            changes_log: List to append change entries to

        """
        # Filter to only subscription tracks - year updates are only for subscription tracks
        subscription_tracks = [
            track
            for track in album_tracks
            if is_subscription_status(track.track_status if isinstance(track.track_status, str) else None)
        ]

        if not subscription_tracks:
            self.console_logger.debug(
                "Skipping album '%s - %s': no subscription tracks (all tracks have non-subscription status)",
                artist,
                album,
            )
            return

        # Continue processing with subscription tracks only
        album_tracks = subscription_tracks

        self.console_logger.debug("DEBUG: Processing album '%s - %s' with %d tracks", artist, album, len(album_tracks))

        # Safety guard: suspicious album names with many unique years
        if await self._check_suspicious_album(artist, album, album_tracks):
            return

        # Check if all tracks are prerelease (read-only)
        if await self._check_album_prerelease_status(artist, album, album_tracks):
            self.console_logger.debug("DEBUG: Skipping '%s - %s' - all tracks prerelease", artist, album)
            return

        # Check for prerelease albums (future years)
        future_years = self._extract_future_years(album_tracks)
        if future_years and await self._handle_future_years_found(artist, album, album_tracks, future_years):
            return

        # Check if we should skip this album due to existing years
        if self._should_skip_album_due_to_existing_years(album_tracks, artist, album):
            self.console_logger.debug("DEBUG: Skipping '%s - %s' - all tracks have same year", artist, album)
            return

        # Try dominant year processing first
        if (dominant_year := self._get_dominant_year(album_tracks)) and await self._process_dominant_year(
            artist, album, album_tracks, dominant_year, updated_tracks, changes_log
        ):
            return

        # Determine the year for this album
        year = await self._determine_album_year(artist, album, album_tracks)

        # Handle case where no year was found
        if not year:
            self._handle_no_year_found(artist, album, album_tracks)
            return

        # Update tracks for this album
        await self._update_tracks_for_album(artist, album, album_tracks, year, updated_tracks, changes_log)

    async def _update_album_years_logic(
        self,
        tracks: list[TrackDict],
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Core logic for updating album years.

        Args:
            tracks: Tracks to process

        Returns:
            Tuple of (updated_tracks, change_logs)

        """
        # Group tracks by album
        albums = YearRetriever._group_tracks_by_album(tracks)
        self.console_logger.info("Processing %d albums for year updates", len(albums))

        # Initialize result containers
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        # Process albums in batches
        await self._process_albums_in_batches(albums, updated_tracks, changes_log)

        return updated_tracks, changes_log

    async def _process_albums_in_batches(
        self,
        albums: dict[tuple[str, str], list[TrackDict]],
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
    ) -> None:
        """Process albums in batches with rate limiting.

        Args:
            albums: Dictionary of albums grouped by (artist, album) key
            updated_tracks: List to append updated tracks to
            changes_log: List to append change entries to

        """
        year_config = self.config.get("year_retrieval", {})
        self._warn_legacy_year_config(year_config)

        batch_size, delay_between_batches, adaptive_delay = self._get_processing_settings(year_config)

        album_items = list(albums.items())
        total_albums = len(album_items)
        if total_albums == 0:
            return

        total_batches = (total_albums + batch_size - 1) // batch_size

        concurrency_limit = self._determine_concurrency_limit(year_config)

        if self._should_use_sequential_processing(adaptive_delay, concurrency_limit):
            await self._process_batches_sequentially(
                album_items,
                batch_size,
                delay_between_batches,
                total_batches,
                total_albums,
                updated_tracks,
                changes_log,
            )
            return

        await self._process_batches_concurrently(
            album_items,
            batch_size,
            total_batches,
            total_albums,
            concurrency_limit,
            updated_tracks,
            changes_log,
            adaptive_delay,
        )

    async def get_album_years_with_logs(
        self,
        tracks: list[TrackDict],
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Get album year updates with change logs (public API for pipeline integration).

        Args:
            tracks: Tracks to process

        Returns:
            Tuple of (updated_tracks, change_logs)

        """
        return await self._update_album_years_logic(tracks)

    async def process_album_years(
        self,
        tracks: list[TrackDict],
        force: bool = False,
    ) -> bool:
        """Process and update album years for given tracks.

        Args:
            tracks: Tracks to process
            force: Force update even if year exists

        Returns:
            True if successful, False otherwise

        """
        if not self.config.get("year_retrieval", {}).get("enabled", True):
            self.console_logger.info("Year retrieval is disabled in config")
            return True

        try:
            self.console_logger.info("Starting album year updates (force=%s)", force)
            self._last_updated_tracks = []

            # Initialize external API service if needed
            if not hasattr(self.external_api, "_initialized"):
                # Initializing external API service (noise reduction)
                await self.external_api.initialize()
                # External API service initialized (noise reduction)

            # Run the update logic
            updated_tracks, _changes_log = await self._update_album_years_logic(tracks)
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

            # Additional logging if no tracks were updated
            if len(updated_tracks) == 0 and albums_with_empty_year > 0:
                self.console_logger.warning(
                    "No album years were updated despite %d albums having empty years. "
                    "This likely means APIs could not find release information for these albums.",
                    albums_with_empty_year,
                )

            # Generate report for problematic albums
            min_attempts = self.config.get("reporting", {}).get("min_attempts_for_report", 3)
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

    @Analytics.track_instance_method("year_discogs_update")
    async def update_years_from_discogs(
        self,
        tracks: list[TrackDict],
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Update years specifically from Discogs API.

        Args:
            tracks: Tracks to process

        Returns:
            Tuple of (updated_tracks, change_logs)

        """
        # This is a wrapper for the main logic
        # Could be extended to use Discogs-specific features
        return await self._update_album_years_logic(tracks)

    def _check_release_year_inconsistency(self, tracks: list[TrackDict], years: list[str], most_common_year: str) -> str | None:
        """Check if all tracks have same year but different release_years."""
        if len(set(years)) != 1:  # Not all tracks have same year
            return None

        release_years = [str(track.get("release_year")) for track in tracks if track.get("release_year") and str(track.get("release_year")).strip()]
        if len(set(release_years)) > 1:
            self.console_logger.info(
                "All tracks have same year %s but inconsistent release_years %s - using consistent track year",
                most_common_year,
                ", ".join(sorted(set(release_years))),
            )
            return most_common_year
        return None

    def _check_year_parity(self, year_counts: Counter[str]) -> bool:
        """Check if there's parity between top years."""
        top_two: list[tuple[str, int]] = year_counts.most_common(self.TOP_YEARS_COUNT)
        if len(top_two) != self.TOP_YEARS_COUNT:
            return False

        diff = abs(top_two[0][1] - top_two[1][1])
        if diff <= self.PARITY_THRESHOLD:
            self.console_logger.info(
                "Year parity detected: %s (%d) vs %s (%d) - need API", top_two[0][0], top_two[0][1], top_two[1][0], top_two[1][1]
            )
            return True
        return False

    def _get_dominant_year(self, tracks: list[TrackDict]) -> str | None:
        """Find dominant year among tracks using majority rule.

        Calculates dominance based on ALL tracks in album, not just tracks for years.
        A year is dominant only if >50% of ALL album tracks have that year.

        Note: Years "0" and empty strings are excluded from dominance calculation
        as they represent placeholder/default values in Music.app.

        Args:
            tracks: List of ALL tracks in the album to analyze

        Returns:
            Dominant year string if found, None if no clear majority or parity

        """
        # Collect all non-empty years (excluding "0" placeholder)
        years: list[str] = []
        for track in tracks:
            year = track.get("year")
            if year and str(year).strip() not in ["", "0"]:
                years.append(str(year))

        if not years:
            return None

        # Count frequency
        year_counts: Counter[str] = Counter(years)
        total_album_tracks = len(tracks)
        most_common: tuple[str, int] = year_counts.most_common(1)[0]
        tracks_with_empty_year = [track for track in tracks if is_empty_year(track.get("year"))]

        # Check for release_year inconsistency case
        if result := self._check_release_year_inconsistency(tracks, years, most_common[0]):
            return result

        # Check for clear majority by configured threshold of ALL album tracks
        if most_common[1] >= total_album_tracks * self.DOMINANCE_MIN_SHARE:
            self.console_logger.info(
                "Dominant year %s found (%d/%d tracks - %.1f%%)",
                most_common[0],
                most_common[1],
                total_album_tracks,
                (most_common[1] / total_album_tracks) * 100,
            )
            return most_common[0]

        # Handle collaboration albums: some empty years but otherwise consistent
        result_year = None
        if len(year_counts) == 1 and tracks_with_empty_year and years:  # COLLABORATION FIX
            self.console_logger.info(
                "Using available year %s for %d tracks without years (collaboration album pattern)", most_common[0], len(tracks_with_empty_year)
            )
            result_year = most_common[0]

        if result_year:
            return result_year

        # Check for parity
        if self._check_year_parity(year_counts):
            return None

        # Most frequent year but not a strong majority of album
        self.console_logger.info(
            "No dominant year (below %.0f%%): %s has %d/%d album tracks (%.1f%%) - need API",
            self.DOMINANCE_MIN_SHARE * 100,
            most_common[0],
            most_common[1],
            total_album_tracks,
            (most_common[1] / total_album_tracks) * 100,
        )
        return None

    def _get_consensus_release_year(self, tracks: list[TrackDict]) -> str | None:
        """Get release_year if all tracks agree (consensus).

        Args:
            tracks: List of tracks to check

        Returns:
            Consensus release_year string if found, None otherwise

        """
        release_years = [str(track.get("release_year")) for track in tracks if track.get("release_year")]

        if not release_years:
            return None

        # Check if ALL tracks have the same release_year (consensus)
        unique_years = set(release_years)
        if len(unique_years) == 1:
            year = next(iter(unique_years))
            if _is_reasonable_year(year):
                self.console_logger.info("Consensus release_year: %s (all %d tracks agree)", year, len(release_years))
                return year

        # Multiple release years - no consensus
        if len(unique_years) > 1:
            self.console_logger.info(
                "Multiple release_years found: %s - no consensus", ", ".join(f"{y} ({release_years.count(y)})" for y in unique_years)
            )

        return None

    def _identify_anomalous_tracks(self, tracks: list[TrackDict], dominant_year: str) -> list[TrackDict]:
        """Identify tracks with years different from dominant year.

        Args:
            tracks: List of tracks to check
            dominant_year: The dominant year to compare against

        Returns:
            List of tracks with anomalous years

        """
        anomalous_tracks: list[TrackDict] = []
        for track in tracks:
            track_year = str(track.get("year", ""))

            # Track has year but it's not a dominant anomaly
            if track_year and track_year.strip() not in ["", "0"] and track_year != dominant_year:
                anomalous_tracks.append(track)
                self.console_logger.info("Track '%s' has anomalous year %s (dominant: %s)", track.get("name", "Unknown"), track_year, dominant_year)

        return anomalous_tracks

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get the list of dry-run actions recorded.

        Returns:
            List of dry-run action dictionaries

        """
        return self._dry_run_actions

    def get_last_updated_tracks(self) -> list[TrackDict]:
        """Return the tracks updated during the most recent processing run."""
        return self._last_updated_tracks

    def set_last_updated_tracks(self, tracks: list[TrackDict]) -> None:
        """Store the tracks updated during processing for snapshot tracking.

        Args:
            tracks: List of updated track records

        """
        self._last_updated_tracks = tracks
