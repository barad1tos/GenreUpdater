"""Year Update Service.

Handles year update operations for tracks including revert, update, and pipeline steps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.logger import get_full_log_path
from core.models import year_repair as repair_utils
from core.models.track_models import ChangeLogEntry
from metrics.change_reports import save_changes_report

if TYPE_CHECKING:
    import logging
    from typing import Any

    from app.pipeline_snapshot import PipelineSnapshotManager
    from core.models.track_models import TrackDict
    from core.tracks.track_processor import TrackProcessor
    from core.tracks.year_retriever import YearRetriever


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

    async def get_tracks_for_year_update(self, artist: str | None) -> list[TrackDict] | None:
        """Get tracks for year update based on artist filter.

        Args:
            artist: Optional artist filter.

        Returns:
            List of tracks or None if not found.
        """
        # For full library (no artist filter), use batch fetcher to avoid AppleScript timeout
        # For specific artist, use direct fetch which is more efficient
        fetched_tracks: list[TrackDict]
        if artist is None:
            fetched_tracks = await self._track_processor.fetch_tracks_in_batches()
        else:
            fetched_tracks = await self._track_processor.fetch_tracks_async(artist=artist)
        if not fetched_tracks:
            self._console_logger.warning("No tracks found")
            return None
        return fetched_tracks

    async def run_update_years(self, artist: str | None, force: bool, fresh: bool = False) -> None:
        """Update album years for all or specific artist.

        Args:
            artist: Optional artist filter.
            force: Force update even if year exists.
            fresh: Fresh mode - invalidate cache before processing, implies force.
        """
        self._console_logger.info(
            "Starting year update operation%s",
            f" for artist: {artist}" if artist else " for all artists",
        )

        tracks = await self.get_tracks_for_year_update(artist)
        if not tracks:
            return

        # Process album years
        success = await self._year_retriever.process_album_years(tracks, force=force, fresh=fresh)

        if success:
            self._console_logger.info("Year update operation completed successfully")
        else:
            self._error_logger.error("Year update operation failed")

    async def run_revert_years(self, artist: str, album: str | None, backup_csv: str | None = None) -> None:
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
            save_changes_report(changes=changes_log, file_path=revert_path, console_logger=self._console_logger, error_logger=self._error_logger)
            self._console_logger.info("Revert changes report saved to %s", revert_path)

    async def update_all_years(self, tracks: list[TrackDict], force: bool, fresh: bool = False) -> None:
        """Update years for all tracks (Step 4 of pipeline).

        Args:
            tracks: List of tracks to process.
            force: Force all operations.
            fresh: Fresh mode - invalidate cache before processing, implies force.
        """
        self._console_logger.info("=== BEFORE Step 4/4: Updating album years ===")
        self._console_logger.info("Step 4/4: Updating album years")
        try:
            await self._year_retriever.process_album_years(tracks, force=force, fresh=fresh)
            self._snapshot_manager.update_tracks(self._year_retriever.get_last_updated_tracks())
            self._console_logger.info("=== AFTER Step 4 completed successfully ===")
        except Exception:
            self._error_logger.exception("=== ERROR in Step 4 ===")
            raise

    async def update_all_years_with_logs(self, tracks: list[TrackDict], force: bool, fresh: bool = False) -> list[ChangeLogEntry]:
        """Update years for all tracks and return change logs (Step 4 of pipeline).

        Args:
            tracks: List of tracks to process.
            force: Force update - bypass cache/skip checks and re-query API for all albums.
            fresh: Fresh mode - invalidate cache before processing, implies force.

        Returns:
            List of change log entries.
        """
        self._console_logger.info("=== BEFORE Step 4/4: Updating album years ===")
        self._console_logger.info("Step 4/4: Updating album years")
        changes_log: list[ChangeLogEntry] = []

        # fresh implies force
        if fresh:
            force = True
            self._console_logger.info("Fresh mode: invalidating album years cache")
            await self._track_processor.cache_service.invalidate_all_albums()

        try:
            updated_tracks, year_changes = await self._year_retriever.get_album_years_with_logs(tracks, force=force)
            self._year_retriever.set_last_updated_tracks(updated_tracks)

            self._snapshot_manager.update_tracks(updated_tracks)
            changes_log = year_changes
            self._console_logger.info("=== AFTER Step 4 completed successfully with %d changes ===", len(changes_log))
        except Exception as e:
            self._error_logger.exception("=== ERROR in Step 4 ===")
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
