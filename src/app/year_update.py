"""Year Update Service.

Handles year update operations for tracks including revert, update, and pipeline steps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.core.logger import get_full_log_path
from src.core.models import repair as repair_utils
from src.core.models.track import ChangeLogEntry
from src.core.tracks.year_retriever import YearRetriever
from src.metrics.change_reports import save_changes_report

if TYPE_CHECKING:
    import logging
    from typing import Any

    from src.app.pipeline_snapshot import PipelineSnapshotManager
    from src.core.models.track import TrackDict
    from src.core.tracks.track_processor import TrackProcessor


class YearUpdateService:
    """Service for year update operations.

    Handles year updates, reverts, and pipeline steps for track metadata.
    """

    def __init__(
        self,
        track_processor: TrackProcessor,
        year_retriever: YearRetriever,
        snapshot_manager: PipelineSnapshotManager,
        config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Initialize the year update service.

        Args:
            track_processor: Processor for fetching and updating tracks.
            year_retriever: Retriever for year operations.
            snapshot_manager: Manager for pipeline snapshots.
            config: Application configuration.
            console_logger: Logger for console output.
            error_logger: Logger for error output.
        """
        self._track_processor = track_processor
        self._year_retriever = year_retriever
        self._snapshot_manager = snapshot_manager
        self._config = config
        self._console_logger = console_logger
        self._error_logger = error_logger

    @staticmethod
    def filter_tracks_for_artist(all_tracks: list[TrackDict], artist: str) -> list[TrackDict]:
        """Filter tracks to include main artist and collaborations.

        Args:
            all_tracks: All tracks to filter.
            artist: Artist name to filter by.

        Returns:
            List of tracks matching the artist.
        """
        filtered_tracks = []
        for track in all_tracks:
            track_artist = str(track.get("artist", ""))
            normalized_artist = YearRetriever.normalize_collaboration_artist(track_artist)
            if normalized_artist == artist:
                filtered_tracks.append(track)
        return filtered_tracks

    async def get_tracks_for_year_update(self, artist: str | None) -> list[TrackDict] | None:
        """Get tracks for year update based on artist filter.

        Args:
            artist: Optional artist filter.

        Returns:
            List of tracks or None if not found.
        """
        if artist:
            # Get all tracks (no AppleScript filter)
            all_tracks = await self._track_processor.fetch_tracks_async()
            if not all_tracks:
                self._console_logger.warning("No tracks found")
                return None

            # Filter tracks to include main artist and collaborations
            filtered_tracks = self.filter_tracks_for_artist(all_tracks, artist)
            if not filtered_tracks:
                self._console_logger.warning(
                    "No tracks found for artist: %s (including collaborations)", artist
                )
                return None

            self._console_logger.info(
                "Found %d tracks for artist '%s' (including collaborations)",
                len(filtered_tracks),
                artist,
            )
            return filtered_tracks

        # Fetch all tracks normally
        fetched_tracks: list[TrackDict] = await self._track_processor.fetch_tracks_async(artist=artist)
        if not fetched_tracks:
            self._console_logger.warning("No tracks found")
            return None
        return fetched_tracks

    async def run_update_years(self, artist: str | None, force: bool) -> None:
        """Update album years for all or specific artist.

        Args:
            artist: Optional artist filter.
            force: Force update even if year exists.
        """
        self._console_logger.info(
            "Starting year update operation%s",
            f" for artist: {artist}" if artist else " for all artists",
        )

        tracks = await self.get_tracks_for_year_update(artist)
        if not tracks:
            return

        # Process album years
        success = await self._year_retriever.process_album_years(tracks, force=force)

        if success:
            self._console_logger.info("Year update operation completed successfully")
        else:
            self._error_logger.error("Year update operation failed")

    async def run_revert_years(
        self, artist: str, album: str | None, backup_csv: str | None = None
    ) -> None:
        """Revert year updates for an artist (optionally per album).

        Uses backup CSV if provided; otherwise uses the latest changes_report.csv.

        Args:
            artist: Artist name.
            album: Optional album name filter.
            backup_csv: Optional path to backup CSV file.
        """
        self._console_logger.info(
            "Starting revert of year changes for '%s'%s",
            artist,
            f" - {album}" if album else " (all albums)",
        )

        targets = repair_utils.build_revert_targets(
            config=self._config,
            artist=artist,
            album=album,
            backup_csv_path=backup_csv,
        )

        if not targets:
            self._console_logger.warning(
                "No revert targets found for '%s'%s",
                artist,
                f" - {album}" if album else "",
            )
            return

        updated, missing, changes_log = await repair_utils.apply_year_reverts(
            track_processor=self._track_processor,
            artist=artist,
            targets=targets,
        )

        self._console_logger.info("Revert complete: %d tracks updated, %d not found", updated, missing)

        if changes_log:
            revert_path = get_full_log_path(self._config, "changes_report_file", "csv/changes_revert.csv")
            save_changes_report(
                changes=changes_log,
                file_path=revert_path,
                console_logger=self._console_logger,
                error_logger=self._error_logger,
                compact_mode=True,
            )
            self._console_logger.info("Revert changes report saved to %s", revert_path)

    async def update_all_years(self, tracks: list[TrackDict], force: bool) -> None:
        """Update years for all tracks (Step 4 of pipeline).

        Args:
            tracks: List of tracks to process.
            force: Force all operations.
        """
        self._console_logger.info("=== BEFORE Step 4/4: Updating album years ===")
        self._console_logger.info("Step 4/4: Updating album years")
        try:
            await self._year_retriever.process_album_years(tracks, force=force)
            self._snapshot_manager.update_tracks(self._year_retriever.get_last_updated_tracks())
            self._console_logger.info("=== AFTER Step 4 completed successfully ===")
        except Exception:
            self._error_logger.exception("=== ERROR in Step 4 ===")
            raise

    async def update_all_years_with_logs(
        self, tracks: list[TrackDict], _force: bool
    ) -> list[ChangeLogEntry]:
        """Update years for all tracks and return change logs (Step 3 of pipeline).

        Args:
            tracks: List of tracks to process.
            _force: Force all operations (unused, kept for API compatibility).

        Returns:
            List of change log entries.
        """
        self._console_logger.info("=== BEFORE Step 4/4: Updating album years ===")
        self._console_logger.info("Step 4/4: Updating album years")
        changes_log: list[ChangeLogEntry] = []

        try:
            updated_tracks: list[TrackDict] = []
            year_changes: list[ChangeLogEntry] = []

            if hasattr(self._year_retriever, "get_album_years_with_logs"):
                updated_tracks, year_changes = await self._year_retriever.get_album_years_with_logs(tracks)
                if hasattr(self._year_retriever, "set_last_updated_tracks"):
                    self._year_retriever.set_last_updated_tracks(updated_tracks)
            else:
                await self._year_retriever.process_album_years(tracks, force=_force)
                if hasattr(self._year_retriever, "get_last_updated_tracks"):
                    updated_tracks = self._year_retriever.get_last_updated_tracks()

            self._snapshot_manager.update_tracks(updated_tracks)
            changes_log = year_changes
            self._console_logger.info(
                "=== AFTER Step 3 completed successfully with %d changes ===", len(changes_log)
            )
        except Exception as e:
            self._error_logger.exception("=== ERROR in Step 3 ===")
            # Add error marker to ensure data consistency
            now = datetime.now(UTC)
            changes_log.append(
                ChangeLogEntry(
                    timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
                    change_type="year_update_error",
                    track_id="",
                    artist="ERROR",
                    album_name=f"Year update failed: {type(e).__name__}",
                    track_name=str(e)[:100],
                    old_year="",
                    new_year="",
                )
            )

        return changes_log
