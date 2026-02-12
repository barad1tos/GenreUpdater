"""Batch processing for album year updates.

This module handles batch processing of album year updates,
including concurrency control and progress tracking.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from core.logger import get_shared_console
from core.models.track_models import ChangeLogEntry
from core.models.track_status import (
    can_edit_metadata,
    filter_available_tracks,
    is_prerelease_status,
)
from core.models.validators import is_empty_year

from .year_determination import YearDeterminator
from .year_utils import normalize_collaboration_artist

if TYPE_CHECKING:
    import logging
    from collections.abc import Coroutine

    from core.models.track_models import TrackDict
    from core.retry_handler import DatabaseRetryHandler
    from core.tracks.track_processor import TrackProcessor
    from core.models.protocols import AnalyticsProtocol


_PROGRESS_DESCRIPTION = "Processing albums"


def _create_album_progress() -> Progress:
    """Create a configured Rich Progress instance for album processing."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}[/cyan]"),
        BarColumn(bar_width=30),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=get_shared_console(),
    )


class YearBatchProcessor:
    """Handles batch processing of album years.

    Responsibilities:
    - Batch album processing with rate limiting
    - Sequential and concurrent processing modes
    - Progress tracking and reporting
    - Integration with YearDeterminator for year logic
    """

    def __init__(
        self,
        *,
        year_determinator: YearDeterminator,
        track_processor: TrackProcessor,
        retry_handler: DatabaseRetryHandler,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        analytics: AnalyticsProtocol,
        dry_run: bool = False,
    ) -> None:
        """Initialize the YearBatchProcessor.

        Args:
            year_determinator: Component for determining album years
            track_processor: Processor for track updates
            retry_handler: Retry handler for transient error recovery
            console_logger: Logger for console output
            error_logger: Logger for error messages
            config: Configuration dictionary
            analytics: Analytics instance for tracking
            dry_run: Whether to run in dry-run mode

        """
        self.year_determinator = year_determinator
        self.track_processor = track_processor
        self.retry_handler = retry_handler
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.config = config
        self.analytics = analytics
        self.dry_run = dry_run
        self._dry_run_actions: list[dict[str, Any]] = []

    async def process_albums_in_batches(
        self,
        grouped_albums: dict[tuple[str, str], list[TrackDict]],
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
        force: bool = False,
    ) -> None:
        """Process albums in batches with rate limiting.

        Args:
            grouped_albums: Dictionary mapping (artist, album) tuples to lists of tracks.
            updated_tracks: List to append updated tracks to.
            changes_log: List to append change entries to.
            force: If True, bypass skip checks and re-query API for all albums.

        """
        year_config = self.config.get("year_retrieval", {})
        self._warn_legacy_year_config(year_config)

        batch_size, delay_between_batches, adaptive_delay = self._get_processing_settings(year_config)

        album_items = list(grouped_albums.items())
        total_albums = len(album_items)
        if total_albums == 0:
            return

        concurrency_limit = self._determine_concurrency_limit(year_config)

        if self._should_use_sequential_processing(adaptive_delay, concurrency_limit):
            await self._process_batches_sequentially(
                album_items,
                batch_size,
                delay_between_batches,
                total_albums,
                updated_tracks,
                changes_log,
                force=force,
            )
            return

        await self._process_batches_concurrently(
            album_items,
            batch_size,
            total_albums,
            concurrency_limit,
            updated_tracks,
            changes_log,
            force=force,
        )

    def _warn_legacy_year_config(self, year_config: dict[str, Any]) -> None:
        """Emit warnings when user still relies on the legacy config format."""
        if "batch_size" in year_config and "processing" not in year_config:
            self.console_logger.warning(
                "Legacy config detected: 'year_retrieval.batch_size' should be 'year_retrieval.processing.batch_size'. "
                "Update your config file for optimal performance."
            )
        if "delay_between_batches" in year_config and "processing" not in year_config:
            self.console_logger.warning(
                "Legacy config detected: 'year_retrieval.delay_between_batches' should be "
                "'year_retrieval.processing.delay_between_batches'. Update your config file for optimal performance."
            )

    @staticmethod
    def _get_processing_settings(year_config: dict[str, Any]) -> tuple[int, int, bool]:
        """Extract batch processing settings with correct fallbacks."""
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
        total_albums: int,
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
        force: bool = False,
    ) -> None:
        """Process albums strictly sequentially with explicit pauses."""
        progress = _create_album_progress()
        with progress:
            task_id = progress.add_task(_PROGRESS_DESCRIPTION, total=total_albums)

            for batch_start in range(0, total_albums, batch_size):
                batch_end = min(batch_start + batch_size, total_albums)

                for album_key, album_tracks in album_items[batch_start:batch_end]:
                    artist_name, album_name = album_key
                    self.console_logger.debug("Processing album '%s - %s'", artist_name, album_name)
                    await self._process_single_album(artist_name, album_name, album_tracks, updated_tracks, changes_log, force=force)
                    progress.update(task_id, advance=1)

                if batch_end < total_albums and delay_between_batches > 0:
                    progress.update(task_id, description=f"Waiting {delay_between_batches}s...")
                    await asyncio.sleep(delay_between_batches)
                    progress.update(task_id, description=_PROGRESS_DESCRIPTION)

    async def _process_album_entry(
        self,
        album_entry: tuple[tuple[str, str], list[TrackDict]],
        semaphore: asyncio.Semaphore,
        progress: Progress,
        task_id: Any,
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
        force: bool = False,
    ) -> None:
        """Process a single album within concurrency limits and update progress.

        Uses try/finally to ensure progress is always updated, even if
        processing fails. Exceptions are allowed to propagate so they can
        be caught by asyncio.gather in the caller.

        Args:
            album_entry: Tuple of ((artist, album), tracks).
            semaphore: Concurrency limiting semaphore.
            progress: Rich progress instance for UI updates.
            task_id: Progress task ID for this batch.
            updated_tracks: Shared list to append updated tracks to.
            changes_log: Shared list to append change log entries to.
            force: If True, bypass skip checks and re-query APIs.

        """
        album_key, album_tracks = album_entry
        artist_name, album_name = album_key

        try:
            async with semaphore:
                self.console_logger.debug("Processing album '%s - %s'", artist_name, album_name)
                await self._process_single_album(artist_name, album_name, album_tracks, updated_tracks, changes_log, force=force)
        finally:
            progress.update(task_id, advance=1)

    async def _process_batches_concurrently(
        self,
        album_items: list[tuple[tuple[str, str], list[TrackDict]]],
        batch_size: int,
        total_albums: int,
        concurrency_limit: int,
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
        force: bool = False,
    ) -> None:
        """Process albums concurrently with error resilience.

        Uses asyncio.gather with return_exceptions=True so that one album
        failure doesn't crash the entire batch. Failed albums are logged
        but processing continues for remaining albums.

        Args:
            album_items: List of (album_key, tracks) tuples to process.
            batch_size: Number of albums to process in each batch.
            total_albums: Total number of albums for progress tracking.
            concurrency_limit: Maximum concurrent album processing tasks.
            updated_tracks: Shared list to append updated tracks to.
            changes_log: Shared list to append change log entries to.
            force: If True, bypass skip checks and re-query APIs.

        """
        semaphore = asyncio.Semaphore(concurrency_limit)

        progress = _create_album_progress()
        with progress:
            task_id = progress.add_task(_PROGRESS_DESCRIPTION, total=total_albums)

            for batch_start in range(0, total_albums, batch_size):
                batch_end = min(batch_start + batch_size, total_albums)
                batch_slice = album_items[batch_start:batch_end]

                # Create tasks for all albums in this batch
                tasks = [
                    self._process_album_entry(
                        album_entry,
                        semaphore,
                        progress,
                        task_id,
                        updated_tracks,
                        changes_log,
                        force=force,
                    )
                    for album_entry in batch_slice
                ]

                # Use gather with return_exceptions for resilience
                # One album failure won't crash the entire batch
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Log any exceptions that occurred (skip CancelledError - it's graceful shutdown)
                for album_entry, result in zip(batch_slice, results, strict=True):
                    if isinstance(result, asyncio.CancelledError):
                        continue  # Skip canceled tasks - not a failure, just shutdown
                    if isinstance(result, BaseException):
                        album_key, _ = album_entry
                        artist_name, album_name = album_key
                        album_key, album_tracks = album_entry
                        self.error_logger.warning(
                            "Failed to process album %s/%s (%d tracks, force=%s): %s: %s",
                            artist_name,
                            album_name,
                            len(album_tracks),
                            force,
                            type(result).__name__,
                            result,
                        )

    async def _process_single_album(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
        force: bool = False,
    ) -> None:
        """Process a single album for year updates.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks in the album
            updated_tracks: List to append updated tracks to
            changes_log: List to append change entries to
            force: If True, bypass skip checks and re-query API

        """
        # Handle prerelease tracks: check mode, filter, and mark for verification if needed
        should_continue, editable_tracks = await self._handle_prerelease_tracks(artist, album, album_tracks)
        if not should_continue:
            return

        # For albums without prerelease, filter to editable tracks
        if not editable_tracks:
            editable_tracks = [track for track in album_tracks if can_edit_metadata(track.track_status)]

        if not editable_tracks:
            self.console_logger.debug(
                "Skipping album '%s - %s': no editable tracks",
                artist,
                album,
            )
            return

        album_tracks = editable_tracks

        self.console_logger.debug("Processing album '%s - %s' with %d tracks", artist, album, len(album_tracks))

        # Safety checks (never bypassed by force - these are data integrity guards)
        if await self.year_determinator.check_suspicious_album(artist, album, album_tracks):
            return

        # NOTE: check_prerelease_status is no longer called here because:
        # 1. We already checked prerelease status above on ORIGINAL tracks
        # 2. album_tracks now contains only editable tracks (no prerelease)
        # The old call would always return False, wasting cycles

        # Check for future years
        future_years = YearDeterminator.extract_future_years(album_tracks)
        if future_years and await self.year_determinator.handle_future_years(artist, album, album_tracks, future_years):
            return

        # Detect user manual changes (year_set_by_mgu is set but differs from current year)
        # Behavior: log and re-process (override user's change with API data)
        self._detect_user_year_changes(artist, album, album_tracks)

        # Check if we should skip this album (force=True bypasses this)
        # Pre-checks prevent wasted API calls by checking inexpensive conditions first
        should_skip, skip_reason = await self.year_determinator.should_skip_album(album_tracks, artist, album, force=force)
        if should_skip:
            self.console_logger.info("[SKIP] %s - %s: %s", artist, album, skip_reason)
            return

        # Force API query if reissue detection triggered (year=current but no release_year)
        force_api = force or skip_reason == "needs_api_verification"

        # Determine the year for this album (handles dominant year, cache, and API)
        # Note: force=True bypasses dominant year and cache checks, always queries API
        year = await self.year_determinator.determine_album_year(artist, album, album_tracks, force=force_api)

        if not year:
            self._handle_no_year_found(artist, album, album_tracks)
            return

        # Update tracks for this album
        await self._update_tracks_for_album(artist, album, album_tracks, year, updated_tracks, changes_log)

    async def _handle_prerelease_tracks(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
    ) -> tuple[bool, list[TrackDict]]:
        """Handle prerelease track detection and filtering.

        Checks for prerelease tracks and applies the configured handling mode.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of all tracks in the album

        Returns:
            Tuple of (should_continue, editable_tracks):
            - should_continue: False if processing should stop (skip_all, mark_only, or no editable tracks)
            - editable_tracks: Filtered list of tracks that can be edited

        """
        original_track_count = len(album_tracks)
        prerelease_tracks = [track for track in album_tracks if is_prerelease_status(track.track_status)]
        has_prerelease = len(prerelease_tracks) > 0
        editable_tracks = [track for track in album_tracks if can_edit_metadata(track.track_status)]

        if not has_prerelease:
            return True, editable_tracks

        # Read and validate prerelease handling mode from config
        prerelease_handling = self._get_prerelease_handling_mode(artist, album)

        if prerelease_handling == "skip_all":
            self.console_logger.info(
                "[SKIP] %s - %s: contains prerelease tracks (%d/%d) - skip_all mode",
                artist,
                album,
                len(prerelease_tracks),
                original_track_count,
            )
            return False, []

        if prerelease_handling == "mark_only":
            self.console_logger.info(
                "[MARK] %s - %s: %d prerelease + %d editable - mark_only mode",
                artist,
                album,
                len(prerelease_tracks),
                len(editable_tracks),
            )
            await self.year_determinator.pending_verification.mark_for_verification(
                artist,
                album,
                reason="prerelease",
                metadata={
                    "track_count": str(original_track_count),
                    "prerelease_count": str(len(prerelease_tracks)),
                    "editable_count": str(len(editable_tracks)),
                    "mode": "mark_only",
                },
                recheck_days=self.year_determinator.prerelease_recheck_days,
            )
            return False, []

        # process_editable mode: check if we have any editable tracks
        if not editable_tracks:
            self.console_logger.debug(
                "Skipping album '%s - %s': no editable tracks (%d/%d tracks are prerelease)",
                artist,
                album,
                len(prerelease_tracks),
                original_track_count,
            )
            await self.year_determinator.pending_verification.mark_for_verification(
                artist,
                album,
                reason="prerelease",
                metadata={
                    "track_count": str(original_track_count),
                    "prerelease_count": str(len(prerelease_tracks)),
                    "all_prerelease": "true",
                },
                recheck_days=self.year_determinator.prerelease_recheck_days,
            )
            return False, []

        # Mixed album: mark for verification but continue processing editable tracks
        self.console_logger.info(
            "[MIXED] %s - %s: %d prerelease + %d editable - marking for verification, processing editable",
            artist,
            album,
            len(prerelease_tracks),
            len(editable_tracks),
        )
        await self.year_determinator.pending_verification.mark_for_verification(
            artist,
            album,
            reason="prerelease",
            metadata={
                "track_count": str(original_track_count),
                "prerelease_count": str(len(prerelease_tracks)),
                "editable_count": str(len(editable_tracks)),
                "mixed_album": "true",
            },
            recheck_days=self.year_determinator.prerelease_recheck_days,
        )
        return True, editable_tracks

    def _get_prerelease_handling_mode(self, artist: str, album: str) -> str:
        """Get validated prerelease handling mode from config.

        Args:
            artist: Artist name (for warning context)
            album: Album name (for warning context)

        Returns:
            Valid prerelease handling mode: 'process_editable', 'skip_all', or 'mark_only'

        """
        valid_modes = {"process_editable", "skip_all", "mark_only"}
        mode = self.config.get("year_retrieval", {}).get("processing", {}).get("prerelease_handling", "process_editable")

        if mode not in valid_modes:
            self.console_logger.warning(
                "Unknown prerelease_handling mode '%s' for %s - %s, defaulting to 'process_editable'. Valid options: %s",
                mode,
                artist,
                album,
                ", ".join(sorted(valid_modes)),
            )
            return "process_editable"

        return mode

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
            True if processing was completed, False if it should continue with regular year determination

        """
        non_empty_years = [str(track.get("year")) for track in album_tracks if track.get("year") and str(track.get("year")).strip()]
        unique_years = set(non_empty_years) if non_empty_years else set()

        # Apply dominant year if there are empty tracks OR inconsistent years
        tracks_needing_update = [track for track in album_tracks if is_empty_year(track.get("year"))]

        # Add tracks with inconsistent years
        if len(unique_years) > 1:
            tracks_needing_update.extend([track for track in album_tracks if track.get("year") and str(track.get("year")).strip() != dominant_year])

        # Deduplicate by track ID
        if tracks_needing_update := list({track.get("id"): track for track in tracks_needing_update}.values()):
            empty_count = len([track for track in tracks_needing_update if is_empty_year(track.get("year"))])
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
            await self._update_tracks_for_album(artist, album, tracks_needing_update, dominant_year, updated_tracks, changes_log)
            return True

        return False

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
        track_ids, tracks_needing_update = self._collect_tracks_for_update(album_tracks, year)

        if not track_ids:
            self.console_logger.info(
                "All tracks for '%s - %s' already have year %s, skipping update",
                artist,
                album,
                year,
            )
            return

        successful, _ = await self.update_album_tracks_bulk_async(
            tracks=tracks_needing_update,
            year=year,
            artist=artist,
            album=album,
        )

        if successful > 0:
            self._record_successful_updates(tracks_needing_update, year, artist, album, updated_tracks, changes_log)

    def _collect_tracks_for_update(
        self,
        album_tracks: list[TrackDict],
        year: str,
    ) -> tuple[list[str], list[TrackDict]]:
        """Collect tracks that need year updates.

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
            track_id = self._get_valid_track_id(track, seen_ids)
            if not track_id:
                continue

            seen_ids.add(track_id)

            if not self._can_update_track(track, track_id):
                continue

            current_year = track.get("year", "")
            if self._track_needs_year_update(current_year, year):
                track_ids.append(track_id)
                tracks_needing_update.append(track)
                self.console_logger.debug(
                    "Track %s needs year update from '%s' to '%s'",
                    track_id,
                    current_year or "empty",
                    year,
                )
            else:
                self.console_logger.debug("Track %s already has correct year %s, skipping", track_id, year)

        return track_ids, tracks_needing_update

    @staticmethod
    def _get_valid_track_id(track: TrackDict, seen_ids: set[str]) -> str | None:
        """Get a valid track ID if not already seen.

        Args:
            track: Track to get ID from
            seen_ids: Set of already seen IDs

        Returns:
            Track ID string or None if invalid/duplicate

        """
        track_id_value = track.get("id", "")
        if not track_id_value:
            return None

        track_id = str(track_id_value)
        return None if track_id in seen_ids else track_id

    def _can_update_track(self, track: TrackDict, track_id: str) -> bool:
        """Check if the track can be updated based on its status.

        Args:
            track: Track to check
            track_id: Track ID for logging

        Returns:
            True if track can be updated

        """
        track_status = track.track_status if isinstance(track.track_status, str) else None

        if not can_edit_metadata(track_status):
            self.console_logger.debug(
                "Skipping read-only track %s (status: %s)",
                track_id,
                track_status or "unknown",
            )
            return False

        return True

    @staticmethod
    def _record_successful_updates(
        tracks: list[TrackDict],
        year: str,
        artist: str,
        album: str,
        updated_tracks: list[TrackDict],
        changes_log: list[ChangeLogEntry],
    ) -> None:
        """Record successful track updates.

        Args:
            tracks: Tracks that were updated
            year: New year value
            artist: Artist name
            album: Album name
            updated_tracks: List to append updated tracks to
            changes_log: List to append change entries to

        """
        for track in tracks:
            updated_tracks.append(track.copy(year=year))

            old_year_value = track.get("year")
            changes_log.append(
                ChangeLogEntry(
                    timestamp=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                    change_type="year_update",
                    track_id=str(track.get("id", "")),
                    artist=artist,
                    album_name=album,
                    track_name=str(track.get("name", "")),
                    year_before_mgu=str(old_year_value) if old_year_value is not None else "",
                    year_set_by_mgu=year,
                )
            )

            # Preserve original year in year_before_mgu (only if not already set)
            if not track.year_before_mgu:
                track.year_before_mgu = str(old_year_value) if old_year_value else ""

            # Keep the in-memory snapshot aligned
            track.year = year
            track.year_set_by_mgu = year

    @staticmethod
    def _track_needs_year_update(current_year: str | int | None, target_year: str) -> bool:
        """Check if a track needs its year updated.

        Args:
            current_year: Current year value (may be None, empty, or string/int)
            target_year: Target year to set

        Returns:
            True if track needs update, False otherwise

        """
        if is_empty_year(current_year):
            return True
        return str(current_year) != target_year

    def _handle_no_year_found(self, artist: str, album: str, album_tracks: list[TrackDict]) -> None:
        """Handle case when no year could be determined for the album.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks in the album

        """
        if available_tracks := filter_available_tracks(album_tracks):
            self.console_logger.warning(
                "Album '%s - %s' has %d available tracks (of %d total) but no year could be determined. "
                "Check API responses or provide year manually.",
                artist,
                album,
                len(available_tracks),
                len(album_tracks),
            )
        else:
            self.console_logger.debug(
                "Skipping '%s - %s' - no year found and no available tracks",
                artist,
                album,
            )

    def _detect_user_year_changes(self, artist: str, album: str, album_tracks: list[TrackDict]) -> None:
        """Detect if user manually changed year in Music.app.

        If year_set_by_mgu is set (we previously updated) but current year differs,
        the user manually changed the year. Log this for visibility.
        Behavior: log and continue processing (override with API data).

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks in the album

        """
        for track in album_tracks:
            if track.year_set_by_mgu and track.year and track.year_set_by_mgu != track.year:
                self.console_logger.info(
                    "User manually changed year for '%s - %s': was %s (we set %s), now %s - will re-process",
                    artist,
                    album,
                    track.year_before_mgu or "unknown",
                    track.year_set_by_mgu,
                    track.year,
                )
                return  # Only log once per album

    @staticmethod
    def group_tracks_by_album(
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
            album_artist = str(track.get("album_artist", ""))
            album = str(track.get("album", ""))

            # Fallback to normalized artist if album_artist is empty
            if not album_artist or not album_artist.strip():
                raw_artist = str(track.get("artist", ""))
                album_artist = normalize_collaboration_artist(raw_artist)

            album_key = (album_artist, album)
            albums[album_key].append(track)

        return albums

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get a list of dry-run actions that would have been performed."""
        return self._dry_run_actions

    # =========================================================================
    # Track Update Methods
    # =========================================================================

    async def _update_track_with_retry(
        self,
        track_id: str,
        new_year: str,
        *,
        original_artist: str | None = None,
        original_album: str | None = None,
        original_track: str | None = None,
    ) -> bool:
        """Update a track's year with retry logic via DatabaseRetryHandler.

        Uses the injected retry_handler for automatic exponential backoff
        and transient error detection.

        Args:
            track_id: Track ID to update
            new_year: Year to set
            original_artist: Artist name for contextual logging
            original_album: Album name for contextual logging
            original_track: Track name for contextual logging

        Returns:
            True if successful, False otherwise

        """

        async def _do_update() -> bool:
            update_success = await self.track_processor.update_track_async(
                track_id=track_id,
                new_year=new_year,
                original_artist=original_artist,
                original_album=original_album,
                original_track=original_track,
            )
            if not update_success:
                # False result without exception - treat as permanent failure
                self.console_logger.debug(
                    "Update returned False for track %s (no-change or unsupported)",
                    track_id,
                )
                return False
            return True

        try:
            retry_result = await self.retry_handler.execute_with_retry(
                _do_update,
                f"track_update:{track_id}",
            )
            # Type narrowing — ty can't infer TypeVar from callable return type
            if not isinstance(retry_result, bool):
                msg = f"execute_with_retry returned {type(retry_result).__name__}, expected bool (track_id={track_id})"
                raise TypeError(msg)
            return retry_result
        except (OSError, ValueError, RuntimeError):
            # All retries exhausted
            self.error_logger.exception(
                "Failed to update year for track %s (artist=%s, album=%s, year=%s) after all retry attempts",
                track_id,
                original_artist or "unknown",
                original_album or "unknown",
                new_year,
            )
            return False

    async def update_album_tracks_bulk_async(
        self,
        tracks: list[TrackDict],
        year: str,
        artist: str,
        album: str,
    ) -> tuple[int, int]:
        """Update year for multiple tracks in bulk.

        Args:
            tracks: List of tracks to update
            year: Year to set
            artist: Artist name for contextual logging
            album: Album name for contextual logging

        Returns:
            Tuple of (successful_count, failed_count)

        """
        # Extract and validate track IDs
        track_ids = [str(track.get("id", "")) for track in tracks if track.get("id")]
        valid_track_ids = self._validate_track_ids(track_ids, artist=artist, album=album)
        if not valid_track_ids:
            self.console_logger.warning(
                "No valid track IDs to update for %s - %s (input: %d tracks, all IDs empty or invalid)",
                artist,
                album,
                len(tracks),
            )
            return 0, len(tracks)

        # Build mapping from track_id to track name for logging
        track_names: dict[str, str] = {str(track.get("id", "")): str(track.get("name", "")) for track in tracks if track.get("id")}

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
                    original_artist=artist,
                    original_album=album,
                    original_track=track_names.get(track_id),
                )
                tasks.append(task)

            # Execute batch
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count results
            for index, result in enumerate(results):
                if isinstance(result, Exception):
                    failed += 1
                    track_id_in_batch = batch[index] if index < len(batch) else "unknown"
                    self.error_logger.error(
                        "Failed to update track %s (artist=%s, album=%s, year=%s): %s",
                        track_id_in_batch,
                        artist,
                        album,
                        year,
                        result,
                    )
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

    def _validate_track_ids(
        self,
        track_ids: list[str],
        *,
        artist: str,
        album: str,
    ) -> list[str]:
        """Validate track IDs before bulk update.

        Args:
            track_ids: List of track IDs to validate
            artist: Artist name for contextual logging
            album: Album name for contextual logging

        Returns:
            List of valid track IDs

        """
        if not track_ids:
            return []

        valid_ids = [track_id for track_id in track_ids if track_id and str(track_id).strip()]

        if len(valid_ids) < len(track_ids):
            self.console_logger.warning(
                "Filtered out %d invalid track IDs for %s - %s (empty or whitespace)",
                len(track_ids) - len(valid_ids),
                artist,
                album,
            )

        return valid_ids
