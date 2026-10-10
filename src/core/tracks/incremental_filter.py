"""Incremental filtering service for Music Genre Updater.

Handles selection of tracks for incremental pipeline runs, keeping the
responsibility separate from genre-specific logic.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any

from core.logger import get_full_log_path
from core.tracks.track_base import BaseProcessor
from core.tracks.track_delta import compute_track_delta
from core.tracks.track_utils import is_missing_or_unknown_genre, parse_track_date_added

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable
    from datetime import datetime

    from core.models.protocols import AnalyticsProtocol
    from core.models.track_models import AppConfig, TrackDict


class IncrementalFilterService(BaseProcessor):
    """Service for filtering tracks in incremental update mode.

    Args:
        console_logger: Logger for user-facing messages
        error_logger: Logger for error details
        analytics: Analytics tracking service
        config: Typed application configuration
        dry_run: If True, record actions without applying changes
        track_list_loader: Callable that loads tracks from a CSV path.
            Injected to avoid core/ depending on metrics/.
    """

    def __init__(
        self,
        *,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics: AnalyticsProtocol,
        config: AppConfig,
        dry_run: bool = False,
        track_list_loader: Callable[[str], dict[str, TrackDict]] | None = None,
    ) -> None:
        super().__init__(console_logger, error_logger, analytics, config, dry_run)
        self._track_list_loader = track_list_loader

    def filter_tracks_for_incremental_update(
        self,
        tracks: list[TrackDict],
        last_run_time: datetime | None,
    ) -> list[TrackDict]:
        """Return the subset of tracks that require processing.

        Args:
            tracks: All tracks from the music library.
            last_run_time: Timestamp of the last successful incremental run.

        Returns:
            Deduplicated tracks that are new, missing genre, or have a changed status.

        """
        if last_run_time is None:
            self.console_logger.info("No last run time found, processing all %d tracks", len(tracks))
            return tracks

        new_tracks: list[TrackDict] = []
        missing_genre_tracks: list[TrackDict] = []

        for track in tracks:
            if is_missing_or_unknown_genre(track):
                missing_genre_tracks.append(track)

            date_added = parse_track_date_added(track)
            if date_added and date_added > last_run_time:
                new_tracks.append(track)

        # Tracks the track list does not know yet, or whose status, genre or year changed (e.g., prerelease -> subscription).
        # A track that arrives through library sync keeps the date it was added elsewhere, so only its new id marks it
        listed_changes = self._find_tracks_changed_since_list(tracks)

        seen: set[str] = set()
        combined: list[TrackDict] = []
        for candidate in itertools.chain(new_tracks, missing_genre_tracks, listed_changes):
            track_id = str(candidate.get("id", ""))
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)
            combined.append(candidate)

        self.console_logger.info(
            "Found %d new tracks since %s; including %d with missing/unknown genre and %d new or changed since the track list (combined %d)",
            len(new_tracks),
            last_run_time.strftime("%Y-%m-%d %H:%M:%S"),
            len(missing_genre_tracks),
            len(listed_changes),
            len(combined),
        )
        return combined

    def _find_tracks_changed_since_list(
        self,
        tracks: list[TrackDict],
    ) -> list[TrackDict]:
        """Find tracks missing from the saved track list or changed against it.

        Uses TrackDict objects directly, eliminating need for separate TrackSummary fetch.
        """
        if self._track_list_loader is None:
            self.console_logger.debug("No track list loader configured; skipping status change detection")
            return []

        try:
            existing_tracks = self._track_list_loader(get_full_log_path(self.config, "csv_output_file", "csv/track_list.csv"))
            if not existing_tracks:
                return []

            # Compute delta using TrackDict objects directly
            delta = compute_track_delta(tracks, existing_tracks)
            return self._collect_updated_tracks(tracks, [*delta.new_ids, *delta.updated_ids])

        except (OSError, ValueError) as e:
            self.console_logger.warning("Failed to check status changes: %s", e)
            return []

    @staticmethod
    def _collect_updated_tracks(tracks: list[TrackDict], updated_ids: list[str]) -> list[TrackDict]:
        """Pick the current tracks whose ids appear in the given list."""
        tracks_by_id = {str(t.get("id", "")): t for t in tracks if t.get("id")}
        return [tracks_by_id[track_id] for track_id in updated_ids if track_id in tracks_by_id]

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Return recorded dry-run actions."""
        return self._dry_run_actions
