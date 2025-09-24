"""Incremental filtering service for Music Genre Updater.

Handles selection of tracks for incremental pipeline runs, keeping the
responsibility separate from genre-specific logic.
"""

from __future__ import annotations

import itertools
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.domain.tracks.base_processor import BaseProcessor
from src.shared.data.models import TrackDict

if TYPE_CHECKING:
    from src.shared.monitoring import Analytics


class IncrementalFilterService(BaseProcessor):
    """Service for filtering tracks in incremental update mode."""

    def __init__(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics: "Analytics",
        config: dict[str, Any],
        dry_run: bool = False,
    ) -> None:
        """Initialize the incremental filter service."""
        super().__init__(console_logger, error_logger, analytics, config, dry_run)

    def filter_tracks_for_incremental_update(
        self,
        tracks: list[TrackDict],
        last_run_time: datetime | None,
    ) -> list[TrackDict]:
        """Return the subset of tracks that require processing.

        Args:
            tracks: All tracks from the music library.
            last_run_time: Timestamp of the last successful incremental run.

        """
        if last_run_time is None:
            self.console_logger.info("No last run time found, processing all %d tracks", len(tracks))
            return tracks

        new_tracks: list[TrackDict] = []
        missing_genre_tracks: list[TrackDict] = []

        for track in tracks:
            if self._is_missing_or_unknown_genre(track):
                missing_genre_tracks.append(track)

            date_added = self._parse_date_added(track)
            if date_added and date_added > last_run_time:
                new_tracks.append(track)

        seen: set[str] = set()
        combined: list[TrackDict] = []
        for candidate in itertools.chain(new_tracks, missing_genre_tracks):
            track_id = str(candidate.get("id", ""))
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)
            combined.append(candidate)

        self.console_logger.info(
            "Found %d new tracks since %s; including %d with missing/unknown genre (combined %d)",
            len(new_tracks),
            last_run_time.strftime("%Y-%m-%d %H:%M:%S"),
            len(missing_genre_tracks),
            len(combined),
        )
        return combined

    @staticmethod
    def _is_missing_or_unknown_genre(track: TrackDict) -> bool:
        genre_val = track.get("genre", "")
        if not isinstance(genre_val, str):
            return True
        return not genre_val.strip() or genre_val.strip().lower() in {"unknown", ""}

    @staticmethod
    def _parse_date_added(track: TrackDict) -> datetime | None:
        try:
            date_added_str = track.get("date_added", "")
            if isinstance(date_added_str, str) and date_added_str:
                return datetime.strptime(date_added_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return None
        return None

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Return recorded dry-run actions."""
        return self._dry_run_actions
