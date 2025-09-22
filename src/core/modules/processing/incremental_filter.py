"""Incremental filtering service for Music Genre Updater.

This module handles filtering tracks based on incremental update criteria,
separating this concern from genre-specific logic.
"""

import itertools
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.core.modules.processing.base_processor import BaseProcessor
from src.utils.data.models import TrackDict

if TYPE_CHECKING:
    from src.utils.monitoring import Analytics


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
        """Initialize the IncrementalFilterService.

        Args:
            console_logger: Logger for console output
            error_logger: Logger for error messages
            analytics: Analytics instance for tracking
            config: Configuration dictionary
            dry_run: Whether to run in dry-run mode

        """
        super().__init__(console_logger, error_logger, analytics, config, dry_run)

    def filter_tracks_for_incremental_update(
        self,
        tracks: list[TrackDict],
        last_run_time: datetime | None,
    ) -> list[TrackDict]:
        """Filter tracks to only include those that need processing in incremental mode.

        This method filters tracks based on two criteria:
        1. Tracks with missing or unknown genres
        2. Tracks added after the last run time

        Args:
            tracks: All tracks from the music library
            last_run_time: Time of last incremental run

        Returns:
            Filtered list of tracks that need processing

        """
        if last_run_time is None:
            self.console_logger.info("No last run time found, processing all %d tracks", len(tracks))
            return tracks

        new_tracks: list[TrackDict] = []
        missing_genre_tracks: list[TrackDict] = []

        for track in tracks:
            # Always include tracks with empty/unknown genre to repair metadata
            if self._is_missing_or_unknown_genre(track):
                missing_genre_tracks.append(track)

            # Include if added after last run
            date_added = self._parse_date_added(track)
            if date_added and date_added > last_run_time:
                new_tracks.append(track)

        # Deduplicate by track id, prioritizing new_tracks entries
        # Use itertools.chain to avoid memory overhead of list concatenation
        seen: set[str] = set()
        combined: list[TrackDict] = []
        for t in itertools.chain(new_tracks, missing_genre_tracks):
            tid = str(t.get("id", ""))
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

    @staticmethod
    def _is_missing_or_unknown_genre(track: TrackDict) -> bool:
        """Check if track has missing or unknown genre.

        Args:
            track: Track to check

        Returns:
            True if genre is missing or unknown

        """
        genre_val = track.get("genre", "")
        if not isinstance(genre_val, str):
            return True

        genre_stripped = genre_val.strip()
        return not genre_stripped or genre_stripped.lower() in {"unknown", ""}

    @staticmethod
    def _parse_date_added(track: TrackDict) -> datetime | None:
        """Parse the date_added field from track.

        Args:
            track: Track containing date_added field

        Returns:
            Parsed datetime or None if parsing fails

        """
        try:
            date_added_str = track.get("date_added", "")
            if isinstance(date_added_str, str) and date_added_str:
                return datetime.strptime(date_added_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return None
        return None

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get the list of dry-run actions recorded.

        Returns:
            List of dry-run action dictionaries

        """
        return self._dry_run_actions
