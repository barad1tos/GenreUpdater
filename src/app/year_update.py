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

    from app.pipeline_snapshot import PipelineSnapshotManager
    from app.track_cleaning import TrackCleaningService
    from core.models.track_models import AppConfig, TrackDict
    from core.tracks.artist_renamer import ArtistRenamer
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
        config: AppConfig,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        cleaning_service: TrackCleaningService | None = None,
        artist_renamer: ArtistRenamer | None = None,
    ) -> None:
        """Initialize the year update service.

        Args:
            track_processor: Processor for fetching and updating tracks.
            year_retriever: Retriever for year operations.
            snapshot_manager: Manager for pipeline snapshots.
            config: Typed application configuration.
            console_logger: Logger for console output.
            error_logger: Logger for error output.
            cleaning_service: Optional service for cleaning track metadata.
            artist_renamer: Optional service for renaming artists.
        """
        self._track_processor = track_processor
        self._year_retriever = year_retriever
        self._snapshot_manager = snapshot_manager
        self._config = config
        self._console_logger = console_logger
        self._error_logger = error_logger
        self._cleaning_service = cleaning_service
        self._artist_renamer = artist_renamer
        self._test_artists: set[str] | None = None

    def set_test_artists(self, test_artists: set[str] | None) -> None:
        """Set test artists for filtering.

        Args:
            test_artists: Set of artist names to filter to, or None to process all.
        """
        self._test_artists = test_artists

    async def get_tracks_for_year_update(self, artist: str | None) -> list[TrackDict] | None:
        """Get tracks for year update based on artist filter.

        Args:
            artist: Optional artist filter.

        Returns:
            List of tracks or None if not found.
        """
        # For full library (no artist filter), use batch fetcher to avoid AppleScript timeout
        # For specific artist, use direct fetch which is more efficient
        fetched_tracks: list[TrackDict] | None
        if artist is None:
            fetched_tracks = await self._track_processor.fetch_tracks_in_batches()
        else:
            fetched_tracks = await self._track_processor.fetch_tracks_async(artist=artist)

        # Filter by test_artists if in test mode
        if self._test_artists and fetched_tracks:
            fetched_tracks = [t for t in fetched_tracks if t.get("artist") in self._test_artists]
            self._console_logger.info(
                "Test mode: filtered to %d tracks for %d test artists",
                len(fetched_tracks),
                len(self._test_artists),
            )

        if not fetched_tracks:
            self._console_logger.warning(
                "No tracks found for year update (artist=%s, test_mode=%s)",
                artist or "all",
                bool(self._test_artists),
            )
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

        # Preprocessing - clean metadata first
        if self._cleaning_service:
            self._console_logger.info("Preprocessing: Cleaning metadata...")
            await self._cleaning_service.clean_all_metadata_with_logs(tracks)

        # Preprocessing - rename artists
        if self._artist_renamer and self._artist_renamer.has_mapping:
            self._console_logger.info("Preprocessing: Renaming artists...")
            await self._artist_renamer.rename_tracks(tracks)

        # Process album years
        success = await self._year_retriever.process_album_years(tracks, force=force, fresh=fresh)

        if success:
            self._console_logger.info("Year update operation completed successfully")
        else:
            self._error_logger.error(
                "Year update operation failed (artist=%s, force=%s, fresh=%s, tracks_count=%d)",
                artist or "all",
                force,
                fresh,
                len(tracks) if tracks else 0,
            )

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

    @staticmethod
    def _format_restore_target(artist: str | None, album: str | None) -> str:
        """Format the target description for restore logging."""
        if not artist:
            return " for all artists"
        return f" for '{artist}' - {album}" if album else f" for '{artist}'"

    @staticmethod
    def _should_restore_track(
        track: TrackDict,
        threshold: int,
    ) -> tuple[bool, str | None]:
        """Check if track needs year restoration.

        Returns:
            Tuple of (should_restore, release_year) where release_year is None if shouldn't restore.

        """
        release_year = track.get("release_year")
        if not release_year:
            return False, None

        track_year = track.get("year")
        if track_year and release_year:
            try:
                diff = abs(int(track_year) - int(release_year))
                if diff <= threshold:
                    return False, None
            except (ValueError, TypeError):
                return False, None

        return True, str(release_year)

    def _find_albums_needing_restoration(
        self,
        tracks: list[TrackDict],
        threshold: int,
    ) -> dict[tuple[str, str], list[tuple[TrackDict, str]]]:
        """Find albums where year differs dramatically from release_year."""
        albums_to_restore: dict[tuple[str, str], list[tuple[TrackDict, str]]] = {}

        for track in tracks:
            should_restore, release_year = self._should_restore_track(track, threshold)
            if not should_restore or release_year is None:
                continue

            artist_name = str(track.get("artist", ""))
            album_name = str(track.get("album", ""))
            key = (artist_name, album_name)
            if key not in albums_to_restore:
                albums_to_restore[key] = []
            albums_to_restore[key].append((track, release_year))

        return albums_to_restore

    async def _update_single_track_year(
        self,
        track: TrackDict,
        consensus_release_year: str,
        artist: str,
        album: str,
    ) -> ChangeLogEntry | None:
        """Update a single track's year and return change log entry if successful."""
        track_id = str(track.id)
        old_year = str(track.get("year", ""))
        track_name = str(track.get("name", ""))

        if old_year == consensus_release_year:
            return None

        success = await self._track_processor.update_track_async(
            track_id=track_id,
            new_year=consensus_release_year,
            original_artist=artist,
            original_album=album,
            original_track=track_name,
        )

        if not success:
            return None

        now = datetime.now(UTC)
        return ChangeLogEntry(
            timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
            change_type="year_restored_from_release_year",
            track_id=track_id,
            artist=artist,
            album_name=album,
            track_name=track_name,
            year_before_mgu=old_year,
            year_set_by_mgu=consensus_release_year,
        )

    async def run_restore_release_years(
        self,
        artist: str | None = None,
        album: str | None = None,
        threshold: int = 5,
    ) -> None:
        """Restore year from Apple Music's release_year field.

        Finds albums where 'year' differs dramatically from 'release_year'
        and updates year to match release_year.

        Args:
            artist: Optional artist name filter.
            album: Optional album name filter (requires artist).
            threshold: Year difference threshold (default: 5 years).

        """
        target_msg = self._format_restore_target(artist, album)
        self._console_logger.info(
            "Starting restore_release_years%s (threshold: %d years)",
            target_msg,
            threshold,
        )

        tracks = await self._get_filtered_tracks_for_restore(artist, album)
        if not tracks:
            return

        albums_to_restore = self._find_albums_needing_restoration(tracks, threshold)
        if not albums_to_restore:
            self._console_logger.info("No albums found needing year restoration")
            return

        self._console_logger.info("Found %d albums needing year restoration:", len(albums_to_restore))

        changes_log, updated_count, failed_count = await self._process_album_restorations(albums_to_restore)

        self._console_logger.info(
            "Restore complete: %d tracks updated, %d failed",
            updated_count,
            failed_count,
        )

        self._save_restore_changes_report(changes_log)

    async def _get_filtered_tracks_for_restore(
        self,
        artist: str | None,
        album: str | None,
    ) -> list[TrackDict] | None:
        """Fetch and filter tracks for restoration."""
        tracks = await self.get_tracks_for_year_update(artist)
        if not tracks:
            return None

        if album:
            tracks = [t for t in tracks if t.get("album") == album]
            if not tracks:
                self._console_logger.warning("No tracks found for album '%s'", album)
                return None

        return tracks

    async def _process_album_restorations(
        self,
        albums_to_restore: dict[tuple[str, str], list[tuple[TrackDict, str]]],
    ) -> tuple[list[ChangeLogEntry], int, int]:
        """Process all album restorations and return results."""
        changes_log: list[ChangeLogEntry] = []
        updated_count = 0
        failed_count = 0

        for (art, alb), track_data in albums_to_restore.items():
            consensus_release_year = self._get_consensus_year(track_data)
            if not consensus_release_year:
                continue

            self._log_album_restoration(art, alb, track_data, consensus_release_year)

            for track, _ in track_data:
                change_entry = await self._update_single_track_year(track, consensus_release_year, art, alb)
                if change_entry:
                    changes_log.append(change_entry)
                    updated_count += 1
                elif change_entry is None and str(track.get("year", "")) != consensus_release_year:
                    failed_count += 1

        return changes_log, updated_count, failed_count

    @staticmethod
    def _get_consensus_year(
        track_data: list[tuple[TrackDict, str]],
    ) -> str | None:
        """Get the most common release_year from track data."""
        if release_years := [ry for _, ry in track_data if ry]:
            return max(set(release_years), key=release_years.count)
        return None

    def _log_album_restoration(
        self,
        artist: str,
        album: str,
        track_data: list[tuple[TrackDict, str]],
        consensus_release_year: str,
    ) -> None:
        """Log album restoration details."""
        current_years = [str(t.get("year", "")) for t, _ in track_data]
        current_year = max(set(current_years), key=current_years.count) if current_years else ""

        self._console_logger.info(
            "  • %s - %s: year=%s → release_year=%s (%d tracks)",
            artist,
            album,
            current_year or "(empty)",
            consensus_release_year,
            len(track_data),
        )

    def _save_restore_changes_report(self, changes_log: list[ChangeLogEntry]) -> None:
        """Save changes report if there are changes."""
        if changes_log:
            restore_path = get_full_log_path(self._config, "changes_report_file", "csv/changes_restore.csv")
            save_changes_report(
                changes=changes_log,
                file_path=restore_path,
                console_logger=self._console_logger,
                error_logger=self._error_logger,
            )
            self._console_logger.info("Restore changes report saved to %s", restore_path)

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
            self._error_logger.exception(
                "=== ERROR in Step 4 (year retrieval): %s ===",
                type(e).__name__,
            )
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
                    year_before_mgu="",
                    year_set_by_mgu="",
                )
            )

        return changes_log
