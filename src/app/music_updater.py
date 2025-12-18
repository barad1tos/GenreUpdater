"""Refactored Music Updater core class.

This is a streamlined version that uses the new modular components.
"""

import contextlib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.features.verify.database_verifier import DatabaseVerifier
from app.pipeline_snapshot import PipelineSnapshotManager
from app.track_cleaning import TrackCleaningService
from app.year_update import YearUpdateService
from core.tracks.artist_renamer import ArtistRenamer
from core.tracks.genre_manager import GenreManager
from core.tracks.incremental_filter import IncrementalFilterService
from core.tracks.track_processor import TrackProcessor
from core.tracks.year_retriever import YearRetriever
from core.logger import LogFormat, get_full_log_path
from core.run_tracking import IncrementalRunTracker
from core.models.metadata_utils import is_music_app_running
from core.models.track_models import ChangeLogEntry, TrackDict
from metrics.change_reports import (
    save_changes_report,
    sync_track_list_with_current,
)

if TYPE_CHECKING:
    from services.dependency_container import DependencyContainer
    from services.pending_verification import PendingAlbumEntry


# noinspection PyArgumentEqualDefault,PyTypeChecker
class MusicUpdater:
    """Orchestrates music library updates using modular components."""

    def __init__(self, deps: "DependencyContainer") -> None:
        """Initialize MusicUpdater with dependency injection.

        Args:
            deps: Dependency container with all required services

        """
        self.deps = deps
        self.config = deps.config
        self.console_logger = deps.console_logger
        self.error_logger = deps.error_logger
        self.analytics = deps.analytics

        # Initialize components
        self.track_processor = TrackProcessor(
            ap_client=deps.ap_client,
            cache_service=deps.cache_service,
            library_snapshot_service=deps.library_snapshot_service,
            console_logger=deps.console_logger,
            error_logger=deps.error_logger,
            config=deps.config,
            analytics=deps.analytics,
            dry_run=deps.dry_run,
        )

        rename_config_path = self._resolve_artist_rename_config_path(deps)
        self.artist_renamer = ArtistRenamer(
            track_processor=self.track_processor,
            console_logger=deps.console_logger,
            error_logger=deps.error_logger,
            config_path=rename_config_path,
        )
        self.track_processor.set_artist_renamer(self.artist_renamer)

        self.genre_manager = GenreManager(
            track_processor=self.track_processor,
            console_logger=deps.console_logger,
            error_logger=deps.error_logger,
            analytics=deps.analytics,
            config=deps.config,
            dry_run=deps.dry_run,
        )

        self.year_retriever = YearRetriever(
            track_processor=self.track_processor,
            cache_service=deps.cache_service,
            external_api=deps.external_api_service,
            pending_verification=deps.pending_verification_service,
            retry_handler=deps.retry_handler,
            console_logger=deps.console_logger,
            error_logger=deps.error_logger,
            analytics=deps.analytics,
            config=deps.config,
            dry_run=deps.dry_run,
        )

        self.database_verifier = DatabaseVerifier(
            ap_client=deps.ap_client,
            console_logger=deps.console_logger,
            error_logger=deps.error_logger,
            db_verify_logger=deps.db_verify_logger,
            analytics=deps.analytics,
            config=deps.config,
            dry_run=deps.dry_run,
        )

        self.incremental_filter = IncrementalFilterService(
            console_logger=deps.console_logger,
            error_logger=deps.error_logger,
            analytics=deps.analytics,
            config=deps.config,
            dry_run=deps.dry_run,
        )

        # Pipeline snapshot manager
        self.snapshot_manager = PipelineSnapshotManager(
            track_processor=self.track_processor,
            console_logger=deps.console_logger,
        )

        # Track cleaning service
        self.cleaning_service = TrackCleaningService(
            track_processor=self.track_processor,
            config=deps.config,
            console_logger=deps.console_logger,
            error_logger=deps.error_logger,
        )

        # Year update service
        self.year_service = YearUpdateService(
            track_processor=self.track_processor,
            year_retriever=self.year_retriever,
            snapshot_manager=self.snapshot_manager,
            config=deps.config,
            console_logger=deps.console_logger,
            error_logger=deps.error_logger,
        )

        # Dry run context
        self.dry_run_mode = ""
        self.dry_run_test_artists: set[str] = set()

    def set_dry_run_context(self, mode: str, test_artists: set[str]) -> None:
        """Set the dry-run context for the updater.

        Args:
            mode: The dry-run mode
            test_artists: Set of test artists for filtering

        """
        self.dry_run_mode = mode
        self.dry_run_test_artists = test_artists
        # Also set context on track_processor
        self.track_processor.set_dry_run_context(mode, test_artists)

    def _resolve_artist_rename_config_path(self, deps: "DependencyContainer") -> Path:
        """Resolve absolute path to artist rename configuration file."""
        config_entry = self.config.get("artist_renamer", {}).get("config_path", "artist-renames.yaml")
        candidate = Path(config_entry)
        if candidate.is_absolute():
            return candidate
        base_path = getattr(deps, "config_path", None)
        if isinstance(base_path, Path):
            config_root = base_path.parent
        elif isinstance(base_path, str):
            config_root = Path(base_path).expanduser().resolve().parent
        else:
            config_root = Path.cwd()
        return config_root / candidate

    async def run_clean_artist(self, artist: str) -> None:
        """Clean track names for a specific artist.

        Args:
            artist: Artist name to process

        """
        self.console_logger.info("Starting clean operation for artist: %s", artist)

        # Check if Music app is running
        if not is_music_app_running(self.error_logger):
            self.error_logger.error("Music app is not running! Please start Music.app before running this script.")
            return

        # Fetch tracks for artist
        tracks = await self.track_processor.fetch_tracks_async(artist=artist)
        if not tracks:
            self.console_logger.warning("No tracks found for artist: %s", artist)
            return

        self.console_logger.info("Found %d tracks for artist %s", len(tracks), artist)

        # Process tracks and collect results
        updated_tracks, changes_log = await self.cleaning_service.process_all_tracks(tracks, artist)

        # Save results if any tracks were updated
        if updated_tracks:
            await self._save_clean_results(changes_log)

        self.console_logger.info(
            "Clean operation complete. Updated %d tracks for artist %s",
            len(updated_tracks),
            artist,
        )

    async def run_revert_years(self, artist: str, album: str | None, backup_csv: str | None = None) -> None:
        """Revert year updates for an artist (optionally per album).

        Uses backup CSV if provided; otherwise uses the latest changes_report.csv.
        """
        await self.year_service.run_revert_years(artist, album, backup_csv)

    @staticmethod
    def _create_change_log_entry(
        artist: str,
        original_track_name: str,
        original_album_name: str,
        cleaned_track_name: str,
        cleaned_album_name: str,
    ) -> ChangeLogEntry:
        """Create a change log entry for metadata cleaning.

        Args:
            artist: Artist name
            original_track_name: Original track name
            original_album_name: Original album name
            cleaned_track_name: Cleaned track name
            cleaned_album_name: Cleaned album name

        Returns:
            Change log entry object

        """
        return ChangeLogEntry(
            timestamp=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            change_type="metadata_cleaning",
            track_id="",  # Not available in cleaning context
            artist=artist,
            track_name=original_track_name,
            album_name=original_album_name,
            old_track_name=original_track_name,
            new_track_name=cleaned_track_name,
            old_album_name=original_album_name,
            new_album_name=cleaned_album_name,
        )

    async def _save_clean_results(self, changes_log: list[ChangeLogEntry]) -> None:
        """Save cleaning results to CSV and changes report.

        Args:
            changes_log: List of change log entries

        """
        # Sync with the database
        csv_path = get_full_log_path(self.config, "csv_output_file", "csv/track_list.csv")
        # Fetch ALL current tracks for complete synchronization
        all_current_tracks = await self.track_processor.fetch_tracks_async()

        await sync_track_list_with_current(
            all_current_tracks,
            csv_path,
            self.deps.cache_service,
            self.console_logger,
            self.error_logger,
            partial_sync=True,  # Incremental sync - only process new/changed tracks
        )

        # Save changes report
        if changes_log:
            changes_path = get_full_log_path(self.config, "changes_report_file", "csv/changes_report.csv")
            save_changes_report(
                changes_log,
                changes_path,
                self.console_logger,
                self.error_logger,
                compact_mode=self.config.get("reporting", {}).get("change_display_mode", "compact") == "compact",
            )

    async def run_update_years(self, artist: str | None, force: bool) -> None:
        """Update album years for all or specific artist.

        Args:
            artist: Optional artist filter
            force: Force update even if year exists
        """
        await self.year_service.run_update_years(artist, force)

    async def _verify_single_pending_album(self, artist: str, album: str, year: str) -> bool:
        """Verify and update a single pending album.

        Args:
            artist: Artist name
            album: Album name
            year: Year string found from API

        Returns:
            True if verification succeeded, False otherwise

        """
        tracks = await self.track_processor.fetch_tracks_async(artist=artist)
        album_tracks = [t for t in tracks if t.get("album", "") == album]
        if not album_tracks:
            return False

        successful, _ = await self.year_retriever.update_album_tracks_bulk_async(
            tracks=album_tracks,
            year=year,
            artist=artist,
            album=album,
        )
        if successful <= 0:
            return False

        await self.deps.pending_verification_service.remove_from_pending(artist, album)
        self.console_logger.debug(
            "  %s %s - %s",
            LogFormat.success(year),
            artist,
            album,
        )
        return True

    async def run_verify_pending(self, _force: bool = False) -> None:
        """Re-verify albums that are pending year verification.

        Args:
            _force: Force verification even if recently done (currently unused)

        """
        start_time = time.time()
        pending_albums = await self.deps.pending_verification_service.get_all_pending_albums()

        if not pending_albums:
            self.console_logger.info(
                "%s %s | no albums pending",
                LogFormat.label("PENDING"),
                LogFormat.dim("SKIP"),
            )
            return

        # Filter albums that need verification (interval elapsed)
        albums_to_verify: list[PendingAlbumEntry] = [
            entry for entry in pending_albums if await self.deps.pending_verification_service.is_verification_needed(entry.artist, entry.album)
        ]
        skipped_count = len(pending_albums) - len(albums_to_verify)

        if not albums_to_verify:
            self.console_logger.info(
                "%s %s | %s pending, none due yet",
                LogFormat.label("PENDING"),
                LogFormat.dim("SKIP"),
                LogFormat.number(len(pending_albums)),
            )
            return

        self.console_logger.info(
            "%s %s | due: %s (skipped: %s)",
            LogFormat.label("PENDING"),
            LogFormat.success("START"),
            LogFormat.number(len(albums_to_verify)),
            LogFormat.dim(str(skipped_count)),
        )

        verified_count = 0
        failed_count = 0
        for entry in albums_to_verify:
            year_str, _, _, _ = await self.deps.external_api_service.get_album_year(entry.artist, entry.album)
            if not year_str:
                failed_count += 1
                continue

            if await self._verify_single_pending_album(entry.artist, entry.album, year_str):
                verified_count += 1
            else:
                failed_count += 1

        duration = time.time() - start_time
        if verified_count > 0:
            self.console_logger.info(
                "%s %s | verified: %s failed: %s %s",
                LogFormat.label("PENDING"),
                LogFormat.success("DONE"),
                LogFormat.success(str(verified_count)),
                LogFormat.dim(str(failed_count)),
                LogFormat.duration(duration),
            )
        else:
            self.console_logger.info(
                "%s %s | no years found %s",
                LogFormat.label("PENDING"),
                LogFormat.warning("DONE"),
                LogFormat.duration(duration),
            )

    async def run_verify_database(self, force: bool = False) -> None:
        """Verify track database against Music.app.

        Args:
            force: Force verification even if recently done

        """
        self.console_logger.info("Starting database verification")

        removed_count = await self.database_verifier.verify_and_clean_track_database(
            force=force,
            apply_test_filter=self._should_apply_test_filter(),
        )

        self.console_logger.info(
            "Database verification complete. Removed %d invalid tracks",
            removed_count,
        )

    # noinspection PyUnusedLocal
    async def run_main_pipeline(self, force: bool = False) -> None:
        """Run the main update pipeline: clean names, update genres, update years.

        Args:
            force: Force all operations

        """
        self.console_logger.info("Starting main update pipeline")
        self.snapshot_manager.reset()

        # Fetch tracks based on mode (test or normal)
        tracks = await self._fetch_tracks_for_pipeline_mode(force=force)
        if not tracks:
            self.console_logger.warning("No tracks found in Music.app")
            return

        self.console_logger.info("Found %d tracks in Music.app", len(tracks))

        # Compute incremental scope - filter tracks that need processing
        incremental_tracks, should_skip_pipeline = await self._compute_incremental_scope(tracks, force)

        if should_skip_pipeline:
            self.console_logger.info("No new tracks to process, skipping pipeline")
            return

        self.console_logger.info("Processing %d tracks (%s mode)", len(incremental_tracks), "full" if force else "incremental")

        # Get last run time for incremental updates
        last_run_time = await self._get_last_run_time(force)

        # Execute the main steps with incremental scope and collect changes
        all_changes: list[ChangeLogEntry] = []

        # Step 1: Clean metadata
        cleaning_changes = await self.cleaning_service.clean_all_metadata_with_logs(incremental_tracks)
        all_changes.extend(cleaning_changes)

        # Step 2: Rename artists (if configured)
        if self.artist_renamer.has_mapping:
            self.console_logger.info("Step 2/4: Renaming artists based on configuration")
            renamed_tracks = await self.artist_renamer.rename_tracks(incremental_tracks)
            if renamed_tracks:
                self.console_logger.info("Renamed artists for %d tracks", len(renamed_tracks))
                # Artist renames create their own change log entries via track_processor
        else:
            self.console_logger.debug("No artist rename mappings configured, skipping rename step")

        # Step 3: Update genres (use ALL tracks - GenreManager handles incremental logic internally)
        genre_changes = await self._update_all_genres(tracks, last_run_time, force)
        all_changes.extend(genre_changes)

        # Step 4: Update years (use ALL tracks - YearBatchProcessor handles internal skip logic)
        year_changes = await self._update_all_years_with_logs(tracks, force)
        all_changes.extend(year_changes)

        # Save combined results including all changes
        await self._save_pipeline_results(all_changes)

        # Update last run timestamp if pipeline completed successfully
        if self._should_update_run_timestamp(force, incremental_tracks):
            await self.database_verifier.update_last_incremental_run()

        # Persist updated snapshot to disk (prevents stale data on next run)
        if not self.deps.dry_run:
            await self.snapshot_manager.persist_to_disk()

        self.snapshot_manager.clear()
        self.console_logger.info("Main update pipeline completed successfully")

    @staticmethod
    def _should_update_run_timestamp(force: bool, incremental_tracks: list["TrackDict"]) -> bool:
        """Determine whether to update the last run timestamp.

        Args:
            force: Whether the pipeline ran in force mode
            incremental_tracks: List of tracks processed in incremental mode

        Returns:
            True if timestamp should be updated

        Logic:
            - Force mode: Always update timestamp because the full pipeline ran
            - Incremental mode: Only update if tracks were actually processed
            - This prevents marking empty runs as successful in incremental mode
        """
        return force or bool(incremental_tracks)

    def _should_apply_test_filter(self) -> bool:
        """Check if the test artist filter should be applied."""
        return bool(self.dry_run_test_artists) and self.deps.dry_run

    # noinspection PyUnusedLocal
    async def _try_smart_delta_fetch(self, force: bool = False) -> list["TrackDict"] | None:
        """Attempt to use Smart Delta to fetch only changed tracks."""
        snapshot_service = self.deps.library_snapshot_service
        ap_client = self.deps.ap_client

        if not snapshot_service or not snapshot_service.is_enabled():
            self.console_logger.debug("Snapshot service not enabled, skipping Smart Delta")
            return None

        if not await snapshot_service.is_snapshot_valid():
            self.console_logger.info("Snapshot invalid or expired, skipping Smart Delta")
            return None

        self.console_logger.info("Attempting Smart Delta approach...")

        result: list[TrackDict] | None = None
        try:
            delta = await snapshot_service.compute_smart_delta(
                ap_client,
                force=force,
            )
            if delta is None:
                self.console_logger.warning("Smart Delta returned None, falling back to batch scan")
                return None

            snapshot_tracks = await snapshot_service.load_snapshot()
            if snapshot_tracks is None:
                self.console_logger.warning("Smart Delta snapshot unavailable, falling back to batch scan")
                return None

            if delta.is_empty():
                self.console_logger.info("Smart Delta: No changes detected, reusing snapshot")
                self.console_logger.info("Loaded %d tracks from snapshot", len(snapshot_tracks))
                result = snapshot_tracks
            else:
                self.console_logger.info(
                    "Smart Delta detected: %d new, %d updated, %d removed",
                    len(delta.new_ids),
                    len(delta.updated_ids),
                    len(delta.removed_ids),
                )
                result = await self.snapshot_manager.merge_smart_delta(snapshot_tracks, delta)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            # Broad catch intentional: Smart Delta is an optimization, not critical path.
            # Any failure should gracefully fall back to full batch scan.
            self.console_logger.exception("Smart Delta failed: %s", exc)
            self.error_logger.exception("Smart Delta error")
            result = None

        return result

    async def _fetch_tracks_for_pipeline_mode(self, force: bool = False) -> list["TrackDict"]:
        """Fetch tracks based on the current mode (test or normal).

        Args:
            force: Force full metadata scan in Smart Delta

        Returns:
            List of tracks to process

        """
        # Capture library_mtime BEFORE any fetch operations to prevent race conditions.
        # If tracks are added to the library during fetch, they won't be in our snapshot,
        # but without this fix the metadata would show library_mtime as including those
        # changes, causing the next run to incorrectly skip them.
        pre_fetch_library_mtime: datetime | None = None
        snapshot_service = self.deps.library_snapshot_service
        if snapshot_service and snapshot_service.is_enabled():
            with contextlib.suppress(FileNotFoundError):
                pre_fetch_library_mtime = await snapshot_service.get_library_mtime()
        # Fetch all tracks if not in test mode
        if not self.dry_run_test_artists:
            # Try Smart Delta first
            smart_delta_tracks = await self._try_smart_delta_fetch(force=force)
            if smart_delta_tracks is not None:
                self.snapshot_manager.set_snapshot(
                    smart_delta_tracks,
                    library_mtime=pre_fetch_library_mtime,
                )
                return smart_delta_tracks

            # Fall back to batch processing for full library
            # Skip snapshot check since Smart Delta already validated it
            self.console_logger.info("Using batch processing for full library fetch")
            batch_size = self.config.get("batch_processing", {}).get("batch_size", 1000)
            tracks: list[TrackDict] = await self.track_processor.fetch_tracks_in_batches(
                batch_size=batch_size,
                skip_snapshot_check=True,  # Already validated in Smart Delta
            )
            self.snapshot_manager.set_snapshot(tracks, library_mtime=pre_fetch_library_mtime)
            return tracks

        # In test artist mode, fetch tracks only for test artists
        self.console_logger.info(
            "Test mode: fetching tracks only for test artists: %s",
            list(self.dry_run_test_artists),
        )
        # Use dict to deduplicate by track ID (handles collaborations appearing for multiple artists)
        unique_tracks: dict[str, TrackDict] = {}
        for artist in self.dry_run_test_artists:
            artist_tracks = await self.track_processor.fetch_tracks_async(artist=artist)
            for track in artist_tracks:
                if track_id := track.get("id"):
                    unique_tracks[str(track_id)] = track
        collected_tracks = list(unique_tracks.values())
        self.snapshot_manager.set_snapshot(collected_tracks, library_mtime=pre_fetch_library_mtime)
        return collected_tracks

    async def _get_last_run_time(self, force: bool) -> datetime | None:
        """Get the last run time for incremental updates.

        Args:
            force: If True, skip getting last run time

        Returns:
            Last run time or None if not available or force is True

        """
        if force:
            return None

        tracker = IncrementalRunTracker(self.config)
        return await tracker.get_last_run_timestamp()

    async def _update_all_genres(self, tracks: list["TrackDict"], last_run_time: datetime | None, force: bool) -> list[ChangeLogEntry]:
        """Update genres for all tracks (Step 2 of pipeline).

        Note: This method receives ALL tracks, not just incremental ones.
        This is required for correct dominant genre calculation which needs
        the full discography of each artist. GenreManager handles internal
        filtering to determine which tracks actually need updating.

        Args:
            tracks: List of ALL tracks (for accurate genre calculation)
            last_run_time: Last run time for incremental updates
            force: Force all operations

        Returns:
            List of genre change log entries

        """
        self.console_logger.info("Step 3/4: Updating genres")
        updated_genre_tracks, genre_changes = await self.genre_manager.update_genres_by_artist_async(tracks, last_run_time=last_run_time, force=force)
        self.snapshot_manager.update_tracks(updated_genre_tracks)
        self.console_logger.info("Updated genres for %d tracks (%d changes)", len(updated_genre_tracks), len(genre_changes))
        return genre_changes

    async def _update_all_years(self, tracks: list["TrackDict"], force: bool) -> None:
        """Update years for all tracks (Step 4 of pipeline).

        Args:
            tracks: List of tracks to process
            force: Force all operations
        """
        await self.year_service.update_all_years(tracks, force)

    async def _update_all_years_with_logs(self, tracks: list["TrackDict"], force: bool) -> list[ChangeLogEntry]:
        """Update years for all tracks and return change logs (Step 4 of pipeline).

        Args:
            tracks: List of tracks to process
            force: Force update - bypass cache/skip checks and re-query API for all albums

        Returns:
            List of change log entries
        """
        return await self.year_service.update_all_years_with_logs(tracks, force)

    async def _save_pipeline_results(self, changes: list[ChangeLogEntry]) -> None:
        """Save the combined results of the pipeline with full track synchronization and changes report.

        Args:
            changes: List of all changes collected during pipeline execution
        """
        # Always display changes report (shows "No changes" message if empty)
        changes_report_path = get_full_log_path(self.config, "changes_report_file", "csv/changes_report.csv")

        save_changes_report(
            changes=changes,
            file_path=changes_report_path if changes else None,  # Only save CSV if there are changes
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            compact_mode=self.config.get("reporting", {}).get("change_display_mode", "compact") == "compact",
        )

        if changes:
            self.console_logger.info("Saved %d changes to report", len(changes))

            # Validation: log change breakdown by type
            change_types: dict[str, int] = {}
            for change in changes:
                change_type = change.change_type
                change_types[change_type] = change_types.get(change_type, 0) + 1

            self.console_logger.info("Change breakdown: %s", ", ".join(f"{k}: {v}" for k, v in sorted(change_types.items())))

        # Use cached snapshot when available to avoid a second AppleScript fetch
        snapshot_tracks = self.snapshot_manager.get_snapshot()
        if snapshot_tracks is not None:
            all_current_tracks = snapshot_tracks
        else:
            # Fetch ALL current tracks from Music.app for complete synchronization
            all_current_tracks = await self.track_processor.fetch_tracks_async()

        if all_current_tracks:
            csv_path = get_full_log_path(self.config, "csv_output_file", "csv/track_list.csv")
            # Only sync CSV in non-dry-run mode to prevent divergence between CSV and Apple Music
            if not self.deps.dry_run:
                # Use sync function instead of save_to_csv for bidirectional sync
                # In test mode: syncs only test artist tracks (partial_sync handles this)
                # In normal mode: syncs all tracks
                await sync_track_list_with_current(
                    all_current_tracks,
                    csv_path,
                    self.deps.cache_service,
                    self.console_logger,
                    self.error_logger,
                    partial_sync=True,  # Incremental sync - only process new/changed tracks
                )

    async def _compute_incremental_scope(self, tracks: list["TrackDict"], force: bool) -> tuple[list["TrackDict"], bool]:
        """Compute which tracks need processing in incremental mode.

        Args:
            tracks: All tracks from Music.app
            force: If True, process all tracks

        Returns:
            Tuple of (filtered_tracks, should_skip_pipeline)
            - filtered_tracks: Tracks that need processing
            - should_skip_pipeline: True if pipeline should be skipped (no work needed)

        """
        if force:
            # Force mode - process all tracks
            return tracks, False

        # Check if enough time has passed for any processing
        can_run = await self.database_verifier.can_run_incremental()
        if not can_run:
            return [], True

        # Get last run time for filtering
        last_run_time = await self._get_last_run_time(force=False)

        # Use dedicated incremental filter service
        incremental_tracks = self.incremental_filter.filter_tracks_for_incremental_update(tracks, last_run_time)

        # Early exit if no tracks need processing
        if not incremental_tracks:
            self.console_logger.info("No new tracks since last run, skipping pipeline")
            return [], True

        return incremental_tracks, False
