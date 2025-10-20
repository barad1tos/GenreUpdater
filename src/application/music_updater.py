"""Refactored Music Updater core class.

This is a streamlined version that uses the new modular components.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.application.features.verification.database_verifier import DatabaseVerifier
from src.domain.tracks.artist_renamer import ArtistRenamer
from src.domain.tracks.genre_manager import GenreManager
from src.domain.tracks.incremental_filter import IncrementalFilterService
from src.domain.tracks.track_processor import TrackProcessor
from src.domain.tracks.year_retriever import YearRetriever
from src.shared.core.logger import get_full_log_path
from src.shared.core.run_tracking import IncrementalRunTracker
from src.shared.data.metadata import clean_names, is_music_app_running
from src.shared.data.models import ChangeLogEntry
from src.shared.monitoring.reports import (
    save_changes_report,
    sync_track_list_with_current,
)

if TYPE_CHECKING:
    from src.infrastructure.dependencies_service import DependencyContainer
    from src.shared.data.models import TrackDict


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

        # Dry run context
        self.dry_run_mode = ""
        self.dry_run_test_artists: set[str] = set()
        self._pipeline_tracks_snapshot: list[TrackDict] | None = None
        self._pipeline_tracks_index: dict[str, TrackDict] = {}

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

    def _reset_pipeline_snapshot(self) -> None:
        """Reset cached pipeline tracks before a fresh run."""
        self._pipeline_tracks_snapshot = None
        self._pipeline_tracks_index = {}

    def _resolve_artist_rename_config_path(self, deps: "DependencyContainer") -> Path:
        """Resolve absolute path to artist rename configuration file."""
        config_entry = self.config.get("artist_renamer", {}).get("config_path", "artist-renames.yaml")
        candidate = Path(config_entry)
        if candidate.is_absolute():
            return candidate
        return deps.config_path.parent / candidate

    def _set_pipeline_snapshot(self, tracks: list["TrackDict"]) -> None:
        """Store the current pipeline track snapshot for downstream reuse."""
        self._pipeline_tracks_snapshot = tracks
        self._pipeline_tracks_index = {}
        for track in tracks:
            if track_id := str(track.get("id", "")):
                self._pipeline_tracks_index[track_id] = track

    def _update_snapshot_tracks(self, updated_tracks: Iterable["TrackDict"]) -> None:
        """Apply field updates from processed tracks to the cached snapshot."""
        if not self._pipeline_tracks_index:
            return

        for updated in updated_tracks:
            track_id = str(updated.get("id", ""))
            if not track_id:
                continue

            current_track = self._pipeline_tracks_index.get(track_id)
            if current_track is None:
                continue

            for field, value in updated.model_dump().items():
                try:
                    setattr(current_track, field, value)
                except (AttributeError, TypeError, ValueError):
                    current_track.__dict__[field] = value

    def _get_pipeline_snapshot(self) -> list["TrackDict"] | None:
        """Return the currently cached pipeline track snapshot."""
        return self._pipeline_tracks_snapshot

    def _clear_pipeline_snapshot(self) -> None:
        """Release cached pipeline track data after finishing the run."""
        self._pipeline_tracks_snapshot = None
        self._pipeline_tracks_index = {}

    async def run_clean_artist(self, artist: str, _force: bool) -> None:
        """Clean track names for a specific artist.

        Args:
            artist: Artist name to process
            _force: Force processing even if recently done (currently unused)

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
        updated_tracks, changes_log = await self._process_all_tracks_for_cleaning(tracks, artist)

        # Save results if any tracks were updated
        if updated_tracks:
            await self._save_clean_results(changes_log)

        self.console_logger.info(
            "Clean operation complete. Updated %d tracks for artist %s",
            len(updated_tracks),
            artist,
        )

    async def _process_all_tracks_for_cleaning(self, tracks: list["TrackDict"], artist: str) -> tuple[list[Any], list[ChangeLogEntry]]:
        """Process all tracks for cleaning and return updated tracks and changes log.

        Args:
            tracks: List of tracks to process
            artist: Artist name for logging

        Returns:
            Tuple of (updated_tracks, changes_log)

        """
        updated_tracks: list[Any] = []
        changes_log: list[ChangeLogEntry] = []

        for track in tracks:
            updated_track, change_entry = await self._process_single_track_cleaning(track, artist)
            if updated_track is not None:
                updated_tracks.append(updated_track)
            if change_entry is not None:
                changes_log.append(change_entry)

        return updated_tracks, changes_log

    async def _process_single_track_cleaning(self, track: "TrackDict", artist: str) -> tuple[Any | None, ChangeLogEntry | None]:
        """Process a single track for cleaning.

        Args:
            track: Track data to process
            artist: Artist name for logging

        Returns:
            Tuple of (updated_track, change_entry) or (None, None) if no update needed

        """
        # Extract track metadata
        artist_name = str(track.get("artist", ""))
        track_name_value = track.get("name", "")
        album_name_value = track.get("album", "")
        track_id = track.get("id", "")

        if not track_id:
            return None, None

        # Clean names using the existing utility
        cleaned_track_name, cleaned_album_name = clean_names(
            artist=artist_name,
            track_name=str(track_name_value),
            album_name=str(album_name_value),
            config=self.config,
            console_logger=self.console_logger,
            error_logger=self.error_logger,
        )

        # Check if update needed
        track_name = track.get("name", "")
        album_name = track.get("album", "")
        if cleaned_track_name == track_name and cleaned_album_name == album_name:
            return None, None

        # Update track
        success = await self.track_processor.update_track_async(
            track_id=track_id,
            new_track_name=(cleaned_track_name if cleaned_track_name != track_name else None),
            new_album_name=(cleaned_album_name if cleaned_album_name != album_name else None),
            original_artist=artist_name,
            original_album=album_name,
            original_track=track_name,
        )

        if not success:
            return None, None

        # Create updated track record by copying the model and updating fields
        updated_track = track.copy()
        updated_track.name = cleaned_track_name
        updated_track.album = cleaned_album_name

        # Create change log entry with safe type conversion
        change_entry = self._create_change_log_entry(
            artist=artist,
            original_track_name=str(track_name) if track_name is not None else "",
            original_album_name=str(album_name) if album_name is not None else "",
            cleaned_track_name=cleaned_track_name,
            cleaned_album_name=cleaned_album_name,
        )

        return updated_track, change_entry

    async def run_revert_years(self, artist: str, album: str | None, backup_csv: str | None = None) -> None:
        """Revert year updates for an artist (optionally per album).

        Uses backup CSV if provided; otherwise uses the latest changes_report.csv.
        """
        from src.shared.data import repair as repair_utils  # noqa: PLC0415

        self.console_logger.info(
            "Starting revert of year changes for '%s'%s",
            artist,
            f" - {album}" if album else " (all albums)",
        )

        targets = repair_utils.build_revert_targets(
            config=self.config,
            artist=artist,
            album=album,
            backup_csv_path=backup_csv,
        )

        if not targets:
            self.console_logger.warning(
                "No revert targets found for '%s'%s",
                artist,
                f" - {album}" if album else "",
            )
            return

        updated, missing, changes_log = await repair_utils.apply_year_reverts(
            track_processor=self.track_processor,
            artist=artist,
            targets=targets,
        )

        self.console_logger.info("Revert complete: %d tracks updated, %d not found", updated, missing)

        if changes_log:
            revert_path = get_full_log_path(self.config, "changes_report_file", "csv/changes_revert.csv")
            save_changes_report(
                changes=changes_log,
                file_path=revert_path,
                console_logger=self.console_logger,
                error_logger=self.error_logger,
                compact_mode=True,
            )
            self.console_logger.info("Revert changes report saved to %s", revert_path)

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
        # Skip full library sync when using test artists for performance
        if self.dry_run_test_artists:
            self.console_logger.info("Skipping full library sync in _save_clean_results (using test artists)")
            return

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

    @staticmethod
    async def _filter_tracks_for_artist(all_tracks: list["TrackDict"], artist: str) -> list["TrackDict"]:
        """Filter tracks to include main artist and collaborations."""
        filtered_tracks = []
        for track in all_tracks:
            track_artist = str(track.get("artist", ""))
            normalized_artist = YearRetriever.normalize_collaboration_artist(track_artist)
            if normalized_artist == artist:
                filtered_tracks.append(track)
        return filtered_tracks

    # Otep-specific album repair removed. Use generic tools in utils.data.repair if needed.

    async def _get_tracks_for_year_update(self, artist: str | None) -> list["TrackDict"] | None:
        """Get tracks for year update based on artist filter."""
        if artist:
            # Get all tracks (no AppleScript filter)
            all_tracks = await self.track_processor.fetch_tracks_async()
            if not all_tracks:
                self.console_logger.warning("No tracks found")
                return None

            # Filter tracks to include main artist and collaborations
            filtered_tracks = await self._filter_tracks_for_artist(all_tracks, artist)
            if not filtered_tracks:
                self.console_logger.warning(f"No tracks found for artist: {artist} (including collaborations)")
                return None

            self.console_logger.info(f"Found {len(filtered_tracks)} tracks for artist '{artist}' (including collaborations)")
            return filtered_tracks
        # Fetch all tracks normally
        fetched_tracks: list[TrackDict] = await self.track_processor.fetch_tracks_async(artist=artist)
        if not fetched_tracks:
            self.console_logger.warning("No tracks found")
            return None
        return fetched_tracks

    async def run_update_years(self, artist: str | None, force: bool) -> None:
        """Update album years for all or specific artist.

        Args:
            artist: Optional artist filter
            force: Force update even if year exists

        """
        self.console_logger.info(
            "Starting year update operation%s",
            f" for artist: {artist}" if artist else " for all artists",
        )

        tracks = await self._get_tracks_for_year_update(artist)
        if not tracks:
            return

        # Process album years
        success = await self.year_retriever.process_album_years(tracks, force=force)

        if success:
            self.console_logger.info("Year update operation completed successfully")
        else:
            self.error_logger.error("Year update operation failed")

    async def run_verify_pending(self, _force: bool = False) -> None:
        """Re-verify albums that are pending year verification.

        Args:
            _force: Force verification even if recently done (currently unused)

        """
        self.console_logger.info("Starting pending year verification")

        # Get pending albums
        pending_albums = await self.deps.pending_verification_service.get_all_pending_albums()

        if not pending_albums:
            self.console_logger.info("No albums pending verification")
            return

        self.console_logger.info("Found %d albums pending year verification", len(pending_albums))

        # Process each pending album
        verified_count = 0
        for pending_tuple in pending_albums:
            # Unpack the tuple (timestamp, artist, album, reason, metadata)
            _, artist_str, album_str, _, _ = pending_tuple

            # Try to get year again
            year = await self.deps.external_api_service.get_album_year(artist_str, album_str)

            if year:
                # Year found! Update tracks
                tracks = await self.track_processor.fetch_tracks_async(artist=artist_str)
                if album_tracks := [t for t in tracks if t.get("album", "") == album_str]:
                    track_ids = [t.get("id", "") for t in album_tracks if t.get("id")]
                    successful, _ = await self.year_retriever.update_album_tracks_bulk_async(track_ids, str(year))

                    if successful > 0:
                        # Remove from pending
                        await self.deps.pending_verification_service.remove_from_pending(artist_str, album_str)
                        verified_count += 1
                        self.console_logger.info(
                            "Verified year %s for '%s - %s'",
                            year,
                            artist_str,
                            album_str,
                        )

        self.console_logger.info(
            "Pending verification complete. Verified %d/%d albums",
            verified_count,
            len(pending_albums),
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
        self._reset_pipeline_snapshot()

        # Fetch tracks based on mode (test or normal)
        tracks = await self._fetch_tracks_for_pipeline_mode()
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
        cleaning_changes = await self._clean_all_tracks_metadata_with_logs(incremental_tracks)
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

        # Step 4: Update years (use incremental tracks - the year retriever handles album-level logic)
        year_changes = await self._update_all_years_with_logs(incremental_tracks, force)
        all_changes.extend(year_changes)

        # Save combined results including all changes
        await self._save_pipeline_results(all_changes)

        # Update last run timestamp if pipeline completed successfully
        if self._should_update_run_timestamp(force, incremental_tracks):
            await self.database_verifier.update_last_incremental_run()

        self._clear_pipeline_snapshot()
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

    async def _fetch_tracks_for_pipeline_mode(self) -> list["TrackDict"]:
        """Fetch tracks based on the current mode (test or normal).

        Returns:
            List of tracks to process

        """
        # Fetch all tracks if not in test mode
        if not self.dry_run_test_artists:
            # Use batch processing for full library to avoid timeout
            self.console_logger.info("Using batch processing for full library fetch")
            batch_size = self.config.get("batch_processing", {}).get("batch_size", 1000)
            tracks = cast(list["TrackDict"], await self.track_processor.fetch_tracks_in_batches(batch_size=batch_size))
            self._set_pipeline_snapshot(tracks)
            return tracks

        # In test artist mode, fetch tracks only for test artists
        self.console_logger.info(
            "Test mode: fetching tracks only for test artists: %s",
            list(self.dry_run_test_artists),
        )
        collected_tracks: list[TrackDict] = []
        for artist in self.dry_run_test_artists:
            artist_tracks = await self.track_processor.fetch_tracks_async(artist=artist)
            collected_tracks.extend(artist_tracks)
        self._set_pipeline_snapshot(collected_tracks)
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

    async def _clean_all_tracks_metadata(self, tracks: list["TrackDict"]) -> list["TrackDict"]:
        """Clean metadata for all tracks (Step 1 of pipeline).

        Args:
            tracks: List of tracks to process

        Returns:
            List of updated tracks

        """
        self.console_logger.info("Step 1/4: Cleaning metadata")
        cleaned_tracks: list[TrackDict] = []

        for track in tracks:
            cleaned_track = await self._process_single_track_for_pipeline_cleaning(track)
            if cleaned_track is not None:
                cleaned_tracks.append(cleaned_track)

        self.console_logger.info("Cleaned %d tracks", len(cleaned_tracks))
        return cleaned_tracks

    async def _clean_all_tracks_metadata_with_logs(self, tracks: list["TrackDict"]) -> list[ChangeLogEntry]:
        """Clean metadata for all tracks and return change logs (Step 1 of pipeline).

        Args:
            tracks: List of tracks to process

        Returns:
            List of change log entries

        """
        self.console_logger.info("Step 1/4: Cleaning metadata")
        changes_log: list[ChangeLogEntry] = []

        for track in tracks:
            cleaned_track, change_entry = await self._process_single_track_cleaning_with_log(track)
            if cleaned_track is not None and change_entry is not None:
                self._update_snapshot_tracks([cleaned_track])
                changes_log.append(change_entry)

        self.console_logger.info("Cleaned %d tracks with %d changes", len(changes_log), len(changes_log))
        return changes_log

    async def _process_single_track_cleaning_with_log(self, track: "TrackDict") -> tuple["TrackDict | None", ChangeLogEntry | None]:
        """Process a single track for pipeline cleaning with change logging.

        Args:
            track: Track data to process

        Returns:
            Tuple of (updated_track, change_entry) or (None, None) if no update needed

        """
        artist_name = str(track.get("artist", ""))
        track_name = track.get("name", "")
        album_name = track.get("album", "")
        track_id = track.get("id", "")

        if not track_id:
            return None, None

        # Clean names
        cleaned_track_name, cleaned_album_name = clean_names(
            artist=artist_name,
            track_name=str(track_name),
            album_name=str(album_name),
            config=self.config,
            console_logger=self.console_logger,
            error_logger=self.error_logger,
        )

        # Check if update needed
        if cleaned_track_name == track_name and cleaned_album_name == album_name:
            return None, None

        # Update track
        success = await self.track_processor.update_track_async(
            track_id=track_id,
            new_track_name=(cleaned_track_name if cleaned_track_name != track_name else None),
            new_album_name=(cleaned_album_name if cleaned_album_name != album_name else None),
            original_artist=artist_name,
            original_album=str(album_name),
            original_track=str(track_name),
        )

        if not success:
            return None, None

        # Create updated track
        updated_track = track.copy()
        updated_track.name = cleaned_track_name
        updated_track.album = cleaned_album_name

        # Create change entry
        change_entry = self._create_change_log_entry(
            artist=artist_name,
            original_track_name=str(track_name),
            original_album_name=str(album_name),
            cleaned_track_name=cleaned_track_name,
            cleaned_album_name=cleaned_album_name,
        )

        return updated_track, change_entry

    async def _process_single_track_for_pipeline_cleaning(self, track: "TrackDict") -> "TrackDict | None":
        """Process a single track for pipeline cleaning.

        Args:
            track: Track data to process

        Returns:
            Updated track or None if no update needed

        """
        cleaned_track_name, cleaned_album_name = clean_names(
            artist=str(track.get("artist", "")),
            track_name=str(track.get("name", "")),
            album_name=str(track.get("album", "")),
            config=self.config,
            console_logger=self.console_logger,
            error_logger=self.error_logger,
        )

        track_name = track.get("name", "")
        album_name = track.get("album", "")
        if cleaned_track_name == track_name and cleaned_album_name == album_name:
            return None

        track_id = track.get("id", "")
        if not track_id:
            return None

        success = await self.track_processor.update_track_async(
            track_id=track_id,
            new_track_name=(cleaned_track_name if cleaned_track_name != track_name else None),
            new_album_name=(cleaned_album_name if cleaned_album_name != album_name else None),
            original_artist=str(track.get("artist", "")),
            original_album=album_name,
            original_track=track_name,
        )

        if not success:
            return None

        # Create updated track record by copying the model and updating fields
        updated_track = track.copy()
        updated_track.name = cleaned_track_name
        updated_track.album = cleaned_album_name
        self._update_snapshot_tracks([updated_track])
        return updated_track

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
        self._update_snapshot_tracks(updated_genre_tracks)
        self.console_logger.info("Updated genres for %d tracks (%d changes)", len(updated_genre_tracks), len(genre_changes))
        return genre_changes

    async def _update_all_years(self, tracks: list["TrackDict"], force: bool) -> None:
        """Update years for all tracks (Step 4 of pipeline).

        Args:
            tracks: List of tracks to process
            force: Force all operations

        """
        self.console_logger.info("=== BEFORE Step 4/4: Updating album years ===")
        self.console_logger.info("Step 4/4: Updating album years")
        try:
            await self.year_retriever.process_album_years(tracks, force=force)
            self._update_snapshot_tracks(self.year_retriever.get_last_updated_tracks())
            self.console_logger.info("=== AFTER Step 4 completed successfully ===")
        except Exception:
            self.error_logger.exception("=== ERROR in Step 4 ===")
            raise

    async def _update_all_years_with_logs(self, tracks: list["TrackDict"], _force: bool) -> list[ChangeLogEntry]:
        """Update years for all tracks and return change logs (Step 3 of pipeline).

        Args:
            tracks: List of tracks to process
            _force: Force all operations (unused, kept for API compatibility)

        Returns:
            List of change log entries

        """
        self.console_logger.info("=== BEFORE Step 4/4: Updating album years ===")
        self.console_logger.info("Step 4/4: Updating album years")
        changes_log: list[ChangeLogEntry] = []

        try:
            # Call the public API that returns changes
            updated_tracks, year_changes = await self.year_retriever.get_album_years_with_logs(tracks)
            # Store updated tracks for snapshot tracking
            self.year_retriever._last_updated_tracks = updated_tracks  # noqa: SLF001
            self._update_snapshot_tracks(updated_tracks)
            changes_log = year_changes
            self.console_logger.info("=== AFTER Step 3 completed successfully with %d changes ===", len(changes_log))
        except Exception as e:
            self.error_logger.exception("=== ERROR in Step 3 ===")
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
            self.console_logger.info("✅ Saved %d changes to changes report", len(changes))

            # Validation: log change breakdown by type
            change_types: dict[str, int] = {}
            for change in changes:
                change_type = change.change_type
                change_types[change_type] = change_types.get(change_type, 0) + 1

            self.console_logger.info("Change breakdown: %s", ", ".join(f"{k}: {v}" for k, v in sorted(change_types.items())))

        # Skip full library sync when using test artists for performance
        if self.dry_run_test_artists:
            self.console_logger.info("Skipping full library sync (using test artists)")
            return

        # Use cached snapshot when available to avoid a second AppleScript fetch
        snapshot_tracks = self._get_pipeline_snapshot()
        if snapshot_tracks is not None:
            all_current_tracks = snapshot_tracks
        else:
            # Fetch ALL current tracks from Music.app for complete synchronization
            all_current_tracks = await self.track_processor.fetch_tracks_async()

        if all_current_tracks:
            csv_path = get_full_log_path(self.config, "csv_output_file", "csv/track_list.csv")
            # Use sync function instead of save_to_csv for bidirectional sync
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
