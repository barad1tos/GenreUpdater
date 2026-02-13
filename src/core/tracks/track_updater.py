"""Track-level year update operations.

Handles applying year values to tracks with validation, deduplication,
retry logic, and bulk update batching.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from core.models.track_models import ChangeLogEntry
from core.models.track_status import can_edit_metadata
from core.models.validators import is_empty_year

if TYPE_CHECKING:
    import logging
    from collections.abc import Coroutine

    from core.models.track_models import AppConfig
    from core.models.types import TrackDict
    from core.retry_handler import DatabaseRetryHandler
    from core.tracks.track_processor import TrackProcessor


class TrackUpdater:
    """Applies year values to tracks with validation and retry.

    Responsibilities:
    - Collect and validate tracks that need year updates
    - Execute bulk updates with batching and retry
    - Record successful updates in change log
    """

    def __init__(
        self,
        *,
        track_processor: TrackProcessor,
        retry_handler: DatabaseRetryHandler,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
    ) -> None:
        self.track_processor = track_processor
        self.retry_handler = retry_handler
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.config = config

    async def update_tracks_for_album(
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
            self.record_successful_updates(tracks_needing_update, year, artist, album, updated_tracks, changes_log)

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
    def record_successful_updates(
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
        batch_size = self.config.apple_script_concurrency
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
