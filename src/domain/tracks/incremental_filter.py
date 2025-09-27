"""Incremental filtering service for Music Genre Updater.

Handles selection of tracks for incremental pipeline runs, keeping the
responsibility separate from genre-specific logic.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.domain.tracks.base_processor import BaseProcessor
from src.infrastructure.track_delta_service import TrackSummary, compute_track_delta
from src.shared.core.logger import get_full_log_path
from src.shared.monitoring.reports import load_track_list

if TYPE_CHECKING:
    import logging

    from src.shared.data.models import TrackDict
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
        track_summaries: list[TrackSummary] | None = None,
    ) -> list[TrackDict]:
        """Return the subset of tracks that require processing.

        Args:
            tracks: All tracks from the music library.
            last_run_time: Timestamp of the last successful incremental run.
            track_summaries: Optional track summaries for status change detection.

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

        # Check for tracks with changed status (e.g., prerelease -> subscription)
        status_changed_tracks: list[TrackDict] = []
        if track_summaries:
            status_changed_tracks = self._find_status_changed_tracks(tracks, track_summaries)

        seen: set[str] = set()
        combined: list[TrackDict] = []
        for candidate in itertools.chain(new_tracks, missing_genre_tracks, status_changed_tracks):
            track_id = str(candidate.get("id", ""))
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)
            combined.append(candidate)

        self.console_logger.info(
            "Found %d new tracks since %s; including %d with missing/unknown genre and %d with changed status (combined %d)",
            len(new_tracks),
            last_run_time.strftime("%Y-%m-%d %H:%M:%S"),
            len(missing_genre_tracks),
            len(status_changed_tracks),
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

    def _find_status_changed_tracks(
        self,
        tracks: list[TrackDict],
        summaries: list[TrackSummary],
    ) -> list[TrackDict]:
        """Find tracks that have changed status since last run."""
        try:
            # Load previous track state from CSV
            csv_path = get_full_log_path(self.config, "csv_output_file", "csv/track_list.csv")
            existing_tracks = load_track_list(csv_path)

            if not existing_tracks:
                return []

            # Compute delta
            delta = compute_track_delta(summaries, existing_tracks)

            if not delta.updated_ids:
                return []

            # Filter tracks that have updated status
            status_changed_tracks: list[TrackDict] = []
            tracks_by_id = {str(t.id): t for t in tracks}

            for track_id in delta.updated_ids:
                if track_id in tracks_by_id:
                    track = tracks_by_id[track_id]
                    status_changed_tracks.append(track)

            return status_changed_tracks

        except Exception as e:
            self.console_logger.warning("Failed to check status changes: %s", e)
            return []

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Return recorded dry-run actions."""
        return self._dry_run_actions
