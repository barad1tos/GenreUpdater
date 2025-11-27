"""Genre management functionality for Music Genre Updater.

This module handles determining dominant genres for artists and
updating track genres accordingly.
"""

import asyncio
import itertools
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.core.models.metadata import (
    determine_dominant_genre_for_artist,
    group_tracks_by_artist,
)
from src.core.models.track import ChangeLogEntry, TrackDict
from src.core.models.track_status import can_edit_metadata, normalize_track_status

from .track_base import BaseProcessor

if TYPE_CHECKING:
    from src.metrics import Analytics

    from .processor import TrackProcessor


class GenreManager(BaseProcessor):
    """Manages genre determination and updates for tracks."""

    def __init__(
        self,
        track_processor: "TrackProcessor",
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics: "Analytics",
        config: dict[str, Any],
        dry_run: bool = False,
    ) -> None:
        """Initialize the GenreManager.

        Args:
            track_processor: Track processor for updating tracks
            console_logger: Logger for console output
            error_logger: Logger for error messages
            analytics: Analytics instance for tracking
            config: Configuration dictionary
            dry_run: Whether to run in dry-run mode

        """
        super().__init__(console_logger, error_logger, analytics, config, dry_run)
        self.track_processor = track_processor

    @staticmethod
    def is_missing_or_unknown_genre(track: TrackDict) -> bool:
        """Check if track has missing or unknown genre.

        Args:
            track: Track to check

        Returns:
            True if genre is missing, empty, or 'unknown'
        """
        genre_val = track.genre

        # Check type before applying string operations
        if not isinstance(genre_val, str):
            return True

        genre_stripped = genre_val.strip()
        return not genre_stripped or genre_stripped.lower() in {"unknown", ""}

    @staticmethod
    def parse_date_added(track: TrackDict) -> datetime | None:
        """Parse track's date_added field to datetime.

        Args:
            track: Track with date_added field

        Returns:
            Parsed datetime with UTC timezone, or None if parsing fails
        """
        try:
            date_added_str = track.date_added or ""
            if isinstance(date_added_str, str) and date_added_str:
                return datetime.strptime(date_added_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return None
        return None

    def filter_tracks_for_incremental_update(
        self,
        tracks: list[TrackDict],
        last_run_time: datetime | None,
    ) -> list[TrackDict]:
        """Filter tracks to only include those added since the last run.

        Args:
            tracks: All tracks
            last_run_time: Time of last incremental run

        Returns:
            Filtered list of tracks added since last run

        """
        if last_run_time is None:
            self.console_logger.info("No last run time found, processing all %d tracks", len(tracks))
            return tracks

        new_tracks: list[TrackDict] = []
        missing_genre_tracks: list[TrackDict] = []

        for track in tracks:
            # Always include tracks with empty/unknown genre to repair metadata
            if self.is_missing_or_unknown_genre(track):
                missing_genre_tracks.append(track)

            # Include if added after last run
            date_added = self.parse_date_added(track)
            if date_added and date_added > last_run_time:
                new_tracks.append(track)

        # Deduplicate by track id, prioritizing new_tracks entries
        # Use itertools.chain to avoid memory overhead of list concatenation
        seen: set[str] = set()
        combined: list[TrackDict] = []
        for t in itertools.chain(new_tracks, missing_genre_tracks):
            tid = str(t.id or "")
            # Check for missing or empty ID (but allow '0' which is falsy but valid)
            if not tid or tid in seen:
                continue
            seen.add(tid)
            combined.append(t)

        self.console_logger.info(
            "Found %d new tracks since %s; including %d with missing/unknown genre (combined %d)",
            len(new_tracks),
            last_run_time.strftime("%Y-%m-%d %H:%M:%S"),
            len(missing_genre_tracks),
            len(combined),
        )
        return combined

    def _validate_track_for_update(self, track: TrackDict) -> tuple[bool, str]:
        """Validate if track can be updated.

        Args:
            track: Track to validate

        Returns:
            Tuple of (is_valid, track_id)
        """
        track_id = track.id or ""
        track_status = track.track_status

        if not track_id:
            self.error_logger.error("Track missing 'id' field")
            return False, ""

        # Skip prerelease tracks (read-only)
        if not can_edit_metadata(track_status):
            self.console_logger.debug(
                "Skipping read-only track %s (status: %s)",
                track_id,
                track_status or "unknown",
            )
            return False, track_id

        return True, track_id

    def _log_track_update_decision(
        self,
        track: TrackDict,
        track_id: str,
        current_genre: str,
        new_genre: str,
        force_update: bool,
    ) -> None:
        """Log track update decision for debugging.

        Args:
            track: Track being updated
            track_id: Track identifier
            current_genre: Current genre value
            new_genre: New genre value
            force_update: Whether force update is enabled
        """
        track_name = track.name or "Unknown"
        track_status = normalize_track_status(track.track_status)

        if current_genre != new_genre:
            self.console_logger.debug(
                "Track %s (%s): Current='%s', New='%s', Status='%s', Force=%s -> WILL UPDATE",
                track_id,
                track_name,
                current_genre,
                new_genre,
                track_status or "unknown",
                force_update,
            )
        else:
            self.console_logger.debug(
                "Track %s (%s): Current='%s', New='%s', Status='%s', Force=%s -> SKIP (same genre)",
                track_id,
                track_name,
                current_genre,
                new_genre,
                track_status or "unknown",
                force_update,
            )

    async def _update_track_genre(
        self,
        track: TrackDict,
        new_genre: str,
        force_update: bool,
    ) -> tuple[TrackDict | None, ChangeLogEntry | None]:
        """Update a single track's genre if needed.

        Args:
            track: The track to update
            new_genre: New genre to apply
            force_update: Whether to force update even if genre matches

        Returns:
            Tuple of (updated_track, change_log_entry) or (None, None) if no update

        """
        # Validate track
        is_valid, track_id = self._validate_track_for_update(track)
        if not is_valid:
            return None, None

        track_name = track.name or "Unknown"
        current_genre = track.genre or ""

        # Log decision
        self._log_track_update_decision(track, track_id, current_genre, new_genre, force_update)

        # Check if update is needed
        if not force_update and current_genre.strip() == new_genre.strip():
            return None, None

        # Perform the update
        success = await self.track_processor.update_track_async(
            track_id=track_id,
            new_genre=new_genre,
            original_artist=str(track.artist or ""),
            original_album=str(track.album or ""),
            original_track=track_name,
        )

        if success:
            # Create updated track and change log
            track.genre = new_genre
            updated_track = track.copy(genre=new_genre)

            change_log = ChangeLogEntry(
                timestamp=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                change_type="genre_update",
                track_id=str(track_id),
                artist=str(track.artist or ""),
                track_name=str(track.name or ""),
                album_name=str(track.album or ""),
                old_genre=str(current_genre),
                new_genre=new_genre,
            )

            return updated_track, change_log

        self.error_logger.error("Failed to update genre for track %s", track_id)
        return None, None

    async def _gather_with_error_handling(
        self,
        tasks: list[asyncio.Task[Any]],
        operation_name: str,
    ) -> list[Any]:
        """Gather tasks with proper error handling and logging.

        Args:
            tasks: List of tasks to gather
            operation_name: Name of the operation for logging

        Returns:
            List of successful results

        """
        results = await asyncio.gather(*tasks, return_exceptions=True)

        successful_results: list[Any] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.error_logger.error(
                    "%s task %d failed: %s",
                    operation_name,
                    i,
                    result,
                    exc_info=result,
                )
            else:
                successful_results.append(result)

        return successful_results

    def _log_artist_debug_info(self, artist_name: str, artist_tracks: list[TrackDict]) -> None:
        """Log debug information for specific artists.

        Args:
            artist_name: Name of the artist
            artist_tracks: All tracks by this artist

        """
        if artist_name == "Green Carnation":
            self.console_logger.info("DEBUG: Green Carnation tracks details:")
            for track in artist_tracks:
                track_id = track.id or ""
                track_name = track.name or ""
                current_genre = track.genre or ""
                track_status = track.track_status or ""
                album = track.album or ""
                self.console_logger.info(
                    "  Track %s: %s | Album: %s | Genre: %s | Status: %s", track_id, track_name, album, current_genre, track_status
                )

    @staticmethod
    def process_batch_results(batch_results: list[Any], updated_tracks: list[TrackDict], change_logs: list[ChangeLogEntry]) -> None:
        """Process batch results and update collections.

        Args:
            batch_results: Results from batch processing
            updated_tracks: List to append updated tracks to
            change_logs: List to append change logs to

        """
        for result in batch_results:
            if result and result[0]:  # (updated_track, change_log)
                updated_tracks.append(result[0])
                if result[1]:
                    change_logs.append(result[1])

    async def _process_artist_genres(
        self,
        artist_name: str,
        all_artist_tracks: list[TrackDict],
        force_update: bool,
        tracks_to_update: list[TrackDict] | None = None,
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Process all tracks for a single artist.

        Args:
            artist_name: Name of the artist
            all_artist_tracks: All tracks by this artist
            force_update: Whether to force update all tracks

        Returns:
            Tuple of (updated_tracks, change_logs)

        """
        # Determine dominant genre
        dominant_genre = determine_dominant_genre_for_artist(
            all_artist_tracks,
            self.error_logger,
        )

        if not dominant_genre:
            self.console_logger.warning("Could not determine dominant genre for artist: %s", artist_name)
            return [], []

        # Only log if there will be actual updates - reduce noise
        # Dominant genre determined, but individual track updates will be logged separately if needed

        # DEBUG: Log track details for specific artists
        self._log_artist_debug_info(artist_name, all_artist_tracks)

        # Create update tasks
        # Decide which tracks to update
        target_tracks = tracks_to_update if tracks_to_update is not None else all_artist_tracks

        update_tasks: list[Any] = []
        for track in target_tracks:
            task = asyncio.create_task(self._update_track_genre(track, dominant_genre, force_update))
            update_tasks.append(task)

        # Process in batches
        batch_size = self.config.get("genre_update", {}).get("batch_size", 50)
        updated_tracks: list[TrackDict] = []
        change_logs: list[ChangeLogEntry] = []

        for i in range(0, len(update_tasks), batch_size):
            batch = update_tasks[i : i + batch_size]
            batch_results = await self._gather_with_error_handling(batch, f"Genre update for {artist_name}")

            self.process_batch_results(batch_results, updated_tracks, change_logs)

            # Reduced noise - only log if significant updates occurred

        return updated_tracks, change_logs

    async def update_genres_by_artist_async(
        self,
        tracks: list[TrackDict],
        last_run_time: datetime | None = None,
        force: bool = False,
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Update genres for all tracks, grouped by artist.

        Args:
            tracks: All tracks to process
            last_run_time: Time of last run for incremental updates
            force: Force update all tracks

        Returns:
            Tuple of (all_updated_tracks, all_change_logs)

        """
        # Group all tracks by artist (we will compute per-artist dominant on full set,
        # and then choose per-track updates incrementally)
        grouped_tracks = group_tracks_by_artist(tracks)

        if not grouped_tracks:
            self.console_logger.info("No tracks to process for genre updates")
            return [], []

        self.console_logger.info(
            "Processing genres for %d artists with %d total tracks",
            len(grouped_tracks),
            len(tracks),
        )

        # Process each artist
        all_updated_tracks: list[TrackDict] = []
        all_change_logs: list[ChangeLogEntry] = []

        # Use a semaphore to limit concurrent artists
        concurrent_limit = self.config.get("genre_update", {}).get("concurrent_limit", 5)
        semaphore = asyncio.Semaphore(concurrent_limit)

        # Create tasks for all artists via a thin wrapper to reduce complexity here
        artist_tasks: list[Any] = []
        for artist_name, artist_tracks in grouped_tracks.items():
            task = asyncio.create_task(
                self._process_single_artist_wrapper(
                    artist_name=artist_name,
                    artist_tracks=artist_tracks,
                    last_run=last_run_time,
                    force=force,
                    semaphore=semaphore,
                )
            )
            artist_tasks.append(task)

        # Process all artists
        results = await self._gather_with_error_handling(artist_tasks, "Artist processing")

        # Aggregate results
        for updated_tracks, change_logs in results:
            all_updated_tracks.extend(updated_tracks)
            all_change_logs.extend(change_logs)

        # Summary
        self.console_logger.info(
            "Genre update complete: %d tracks updated across %d artists",
            len(all_updated_tracks),
            len(grouped_tracks),
        )

        return all_updated_tracks, all_change_logs

    async def _process_single_artist_wrapper(
        self,
        artist_name: str,
        artist_tracks: list[TrackDict],
        last_run: datetime | None,
        force: bool,
        semaphore: asyncio.Semaphore,
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Select tracks for update and process a single artist under a semaphore.

        Args:
            artist_name: The artist name.
            artist_tracks: All tracks for the artist.
            last_run: Last run timestamp for incremental logic.
            force: Force updates regardless of filters.
            semaphore: Concurrency guard for processing.

        Returns:
            Tuple of (updated_tracks, change_logs) for this artist.
        """
        dominant = determine_dominant_genre_for_artist(artist_tracks, self.error_logger)
        to_update = self._select_tracks_to_update_for_artist(artist_tracks, last_run, force, dominant)
        if not to_update:
            return [], []
        async with semaphore:
            return await self._process_artist_genres(artist_name, artist_tracks, force, to_update)

    def _select_tracks_to_update_for_artist(
        self,
        artist_tracks: list[TrackDict],
        last_run: datetime | None,
        force_flag: bool,
        dominant_genre: str | None,
    ) -> list[TrackDict]:
        """Build list of tracks for update based on incremental rules and dominance.

        - Always include tracks with missing/unknown genre.
        - Include tracks added after last_run.
        - Include tracks whose genre differs from dominant_genre.
        - If force_flag is True, include all tracks.
        """
        if not dominant_genre and not force_flag:
            return []

        candidates = self._filter_tracks_for_update(artist_tracks, last_run, force_flag, dominant_genre)

        # De-duplicate by id
        return self.deduplicate_tracks_by_id(candidates)

    def _filter_tracks_for_update(
        self,
        artist_tracks: list[TrackDict],
        last_run: datetime | None,
        force_flag: bool,
        dominant_genre: str | None,
    ) -> list[TrackDict]:
        """Filter tracks that need genre updates based on various criteria.

        Args:
            artist_tracks: List of tracks for the artist
            last_run: Timestamp of last incremental run
            force_flag: Whether to force update all tracks
            dominant_genre: The dominant genre for this artist

        Returns:
            List of tracks that should be updated
        """
        candidates: list[TrackDict] = []
        for t in artist_tracks:
            if force_flag or self.is_missing_or_unknown_genre(t):
                candidates.append(t)
                continue

            added_dt = self.parse_date_added(t)
            if last_run is not None and added_dt and added_dt > last_run:
                candidates.append(t)
                continue

            genre_val = t.genre or ""
            if isinstance(genre_val, str) and genre_val.strip() and dominant_genre and (genre_val != dominant_genre):
                candidates.append(t)

        return candidates

    @staticmethod
    def deduplicate_tracks_by_id(tracks: list[TrackDict]) -> list[TrackDict]:
        """Remove duplicate tracks based on track ID.

        Args:
            tracks: List of tracks that may contain duplicates

        Returns:
            List of unique tracks without duplicates
        """
        seen_ids: set[str] = set()
        unique: list[TrackDict] = []
        for t in tracks:
            tid = t.id or ""
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            unique.append(t)
        return unique

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get the list of dry-run actions recorded.

        Returns:
            List of dry-run action dictionaries

        """
        return self._dry_run_actions

    # Test-only methods for accessing private functionality
    async def test_update_track_genre(
        self,
        track: TrackDict,
        new_genre: str,
        force_update: bool,
    ) -> tuple[TrackDict | None, ChangeLogEntry | None]:
        """Test-only access to _update_track_genre method."""
        return await self._update_track_genre(track, new_genre, force_update)

    async def test_gather_with_error_handling(
        self,
        tasks: list[Any],
        operation_name: str,
    ) -> list[Any]:
        """Test-only access to _gather_with_error_handling method."""
        return await self._gather_with_error_handling(tasks, operation_name)

    def test_log_artist_debug_info(self, artist_name: str, artist_tracks: list[TrackDict]) -> None:
        """Test-only access to _log_artist_debug_info method."""
        return self._log_artist_debug_info(artist_name, artist_tracks)

    def test_filter_tracks_for_update(
        self,
        artist_tracks: list[TrackDict],
        last_run: datetime | None,
        force_flag: bool,
        dominant_genre: str | None,
    ) -> list[TrackDict]:
        """Test-only access to _filter_tracks_for_update method."""
        return self._filter_tracks_for_update(artist_tracks, last_run, force_flag, dominant_genre)

    def test_select_tracks_to_update_for_artist(
        self,
        artist_tracks: list[TrackDict],
        last_run: datetime | None,
        force_flag: bool,
        dominant_genre: str | None,
    ) -> list[TrackDict]:
        """Test-only access to _select_tracks_to_update_for_artist method."""
        return self._select_tracks_to_update_for_artist(artist_tracks, last_run, force_flag, dominant_genre)

    async def test_process_artist_genres(
        self,
        artist_name: str,
        all_artist_tracks: list[TrackDict],
        force_update: bool,
        tracks_to_update: list[TrackDict] | None = None,
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Test-only access to _process_artist_genres method."""
        return await self._process_artist_genres(artist_name, all_artist_tracks, force_update, tracks_to_update)

    async def test_process_single_artist_wrapper(
        self,
        artist_name: str,
        artist_tracks: list[TrackDict],
        last_run: datetime | None,
        force: bool,
        semaphore: Any,
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Test-only access to _process_single_artist_wrapper method."""
        return await self._process_single_artist_wrapper(artist_name, artist_tracks, last_run, force, semaphore)
