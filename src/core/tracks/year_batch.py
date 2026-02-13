"""Batch processing for album year updates.

This module handles batch processing of album year updates,
including concurrency control and progress tracking.

Track-level update operations are delegated to TrackUpdater,
and prerelease handling is delegated to PrereleaseHandler.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
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
from core.models.track_status import (
    can_edit_metadata,
    filter_available_tracks,
)
from core.models.validators import is_empty_year

from .prerelease_handler import PrereleaseHandler
from .track_updater import TrackUpdater
from .year_determination import YearDeterminator
from .year_utils import normalize_collaboration_artist

if TYPE_CHECKING:
    import logging

    from core.models.protocols import AnalyticsProtocol
    from core.models.track_models import AppConfig, ChangeLogEntry, TrackDict
    from core.retry_handler import DatabaseRetryHandler
    from core.tracks.track_processor import TrackProcessor


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
    - Orchestration of prerelease handling and track updates
    """

    def __init__(
        self,
        *,
        year_determinator: YearDeterminator,
        track_processor: TrackProcessor,
        retry_handler: DatabaseRetryHandler,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
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
            config: Typed application configuration
            analytics: Service for performance tracking
            dry_run: Whether to run in dry-run mode

        """
        self.year_determinator = year_determinator
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.config = config
        self.analytics = analytics
        self.dry_run = dry_run
        self._dry_run_actions: list[dict[str, Any]] = []

        self._prerelease_handler = PrereleaseHandler(
            console_logger=console_logger,
            config=config,
            pending_verification=year_determinator.pending_verification,
            prerelease_recheck_days=year_determinator.prerelease_recheck_days,
        )

        self._track_updater = TrackUpdater(
            track_processor=track_processor,
            retry_handler=retry_handler,
            console_logger=console_logger,
            error_logger=error_logger,
            config=config,
        )

    # Batch strategy

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
        processing = self.config.year_retrieval.processing

        batch_size = processing.batch_size
        delay_between_batches = int(processing.delay_between_batches)
        adaptive_delay = processing.adaptive_delay

        album_items = list(grouped_albums.items())
        total_albums = len(album_items)
        if total_albums == 0:
            return

        concurrency_limit = self._determine_concurrency_limit()

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

    def _determine_concurrency_limit(self) -> int:
        """Compute concurrency limit based on AppleScript and API limits."""
        api_concurrency = self.config.year_retrieval.rate_limits.concurrent_api_calls
        apple_script_concurrency = self.config.apple_script_concurrency
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
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Log any exceptions that occurred (skip CancelledError - it's graceful shutdown)
                for album_entry, result in zip(batch_slice, results, strict=True):
                    if isinstance(result, asyncio.CancelledError):
                        continue
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

    # Album processing pipeline

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
        should_continue, editable_tracks = await self._prerelease_handler.handle_prerelease_tracks(artist, album, album_tracks)
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

        # Check for future years
        future_years = YearDeterminator.extract_future_years(album_tracks)
        if future_years and await self.year_determinator.handle_future_years(artist, album, album_tracks, future_years):
            return

        # Detect user manual changes (year_set_by_mgu is set but differs from current year)
        self._detect_user_year_changes(artist, album, album_tracks)

        # Check if we should skip this album (force=True bypasses this)
        should_skip, skip_reason = await self.year_determinator.should_skip_album(album_tracks, artist, album, force=force)
        if should_skip:
            self.console_logger.info("[SKIP] %s - %s: %s", artist, album, skip_reason)
            return

        # Force API query if reissue detection triggered
        force_api = force or skip_reason == "needs_api_verification"

        # Determine the year for this album
        year = await self.year_determinator.determine_album_year(artist, album, album_tracks, force=force_api)

        if not year:
            self._handle_no_year_found(artist, album, album_tracks)
            return

        # Update tracks for this album
        await self._track_updater.update_tracks_for_album(artist, album, album_tracks, year, updated_tracks, changes_log)

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
            await self._track_updater.update_tracks_for_album(artist, album, tracks_needing_update, dominant_year, updated_tracks, changes_log)
            return True

        return False

    def _handle_no_year_found(self, artist: str, album: str, album_tracks: list[TrackDict]) -> None:
        """Handle case when no year could be determined for the album."""
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

    # Utilities

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

    # Backward compatibility delegation

    async def update_album_tracks_bulk_async(
        self,
        tracks: list[TrackDict],
        year: str,
        artist: str,
        album: str,
    ) -> tuple[int, int]:
        """Update year for multiple tracks. Delegates to TrackUpdater."""
        return await self._track_updater.update_album_tracks_bulk_async(
            tracks=tracks,
            year=year,
            artist=artist,
            album=album,
        )
