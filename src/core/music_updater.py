"""Refactored Music Updater core class.

This is a streamlined version that uses the new modular components.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.utils.core.logger import get_full_log_path
from src.utils.data.metadata import clean_names, is_music_app_running
from src.utils.monitoring.reports import (
    save_changes_report,
    sync_track_list_with_current,
)

from .modules.processing.genre_manager import GenreManager
from .modules.processing.track_processor import TrackProcessor
from .modules.processing.year_retriever import YearRetriever
from .modules.verification.database_verifier import DatabaseVerifier

if TYPE_CHECKING:
    from src.services.dependencies_service import DependencyContainer
    from src.utils.data.models import TrackDict


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

    async def _process_all_tracks_for_cleaning(self, tracks: list["TrackDict"], artist: str) -> tuple[list[Any], list[dict[str, Any]]]:
        """Process all tracks for cleaning and return updated tracks and changes log.

        Args:
            tracks: List of tracks to process
            artist: Artist name for logging

        Returns:
            Tuple of (updated_tracks, changes_log)

        """
        updated_tracks: list[Any] = []
        changes_log: list[dict[str, Any]] = []

        for track in tracks:
            updated_track, change_entry = await self._process_single_track_cleaning(track, artist)
            if updated_track is not None:
                updated_tracks.append(updated_track)
            if change_entry is not None:
                changes_log.append(change_entry)

        return updated_tracks, changes_log

    async def _process_single_track_cleaning(self, track: "TrackDict", artist: str) -> tuple[Any | None, dict[str, Any] | None]:
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

    @staticmethod
    def _create_change_log_entry(
        artist: str,
        original_track_name: str,
        original_album_name: str,
        cleaned_track_name: str,
        cleaned_album_name: str,
    ) -> dict[str, Any]:
        """Create a change log entry for metadata cleaning.

        Args:
            artist: Artist name
            original_track_name: Original track name
            original_album_name: Original album name
            cleaned_track_name: Cleaned track name
            cleaned_album_name: Cleaned album name

        Returns:
            Change log entry dictionary

        """
        return {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "change_type": "metadata_cleaning",
            "artist": artist,
            "track_name": original_track_name,
            "album_name": original_album_name,
            "old_track_name": original_track_name,
            "new_track_name": cleaned_track_name,
            "old_album_name": original_album_name,
            "new_album_name": cleaned_album_name,
        }

    async def _save_clean_results(self, changes_log: list[dict[str, Any]]) -> None:
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

        # Fetch tracks
        tracks = await self.track_processor.fetch_tracks_async(artist=artist)
        if not tracks:
            self.console_logger.warning("No tracks found")
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

    async def run_main_pipeline(self, force: bool = False) -> None:
        """Run the main update pipeline: clean names, update genres, update years.

        Args:
            force: Force all operations

        """
        # Check if it can run incremental
        if not force:
            can_run = await self.database_verifier.can_run_incremental()
            if not can_run:
                return

        self.console_logger.info("Starting main update pipeline")

        # Fetch tracks based on mode (test or normal)
        tracks = await self._fetch_tracks_for_pipeline_mode()
        if not tracks:
            self.console_logger.warning("No tracks found in Music.app")
            return

        self.console_logger.info("Found %d tracks in Music.app", len(tracks))

        # Get last run time for incremental updates
        last_run_time = await self._get_last_run_time(force)

        # Execute the three main steps
        cleaned_tracks = await self._clean_all_tracks_metadata(tracks)
        updated_genre_tracks = await self._update_all_genres(cleaned_tracks, last_run_time, force)

        # Use filtered tracks from genre updates for year updates (incremental optimization)
        tracks_for_years = updated_genre_tracks if not force and last_run_time and updated_genre_tracks else tracks
        await self._update_all_years(tracks_for_years, force)

        # Save combined results
        await self._save_pipeline_results()

        # Update last run timestamp
        await self.database_verifier.update_last_incremental_run()

        self.console_logger.info("Main update pipeline completed successfully")

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
            return cast(list["TrackDict"], await self.track_processor.fetch_tracks_in_batches(batch_size=batch_size))

        # In test artist mode, fetch tracks only for test artists
        self.console_logger.info(
            "Test mode: fetching tracks only for test artists: %s",
            list(self.dry_run_test_artists),
        )
        tracks: list[TrackDict] = []
        for artist in self.dry_run_test_artists:
            artist_tracks = await self.track_processor.fetch_tracks_async(artist=artist)
            tracks.extend(artist_tracks)
        return tracks

    async def _get_last_run_time(self, force: bool) -> datetime | None:
        """Get the last run time for incremental updates.

        Args:
            force: If True, skip getting last run time

        Returns:
            Last run time or None if not available or force is True

        """
        if force:
            return None

        last_run_file = get_full_log_path(
            self.config,
            "last_incremental_run_file",
            "last_incremental_run.log",
        )
        try:
            last_run_path = Path(last_run_file)
            if last_run_path.exists():
                # Read the last run time using an async file operation
                loop = asyncio.get_event_loop()

                def _read_last_run() -> str:
                    with last_run_path.open(encoding="utf-8") as f:
                        return f.read().strip()

                last_run_str = await loop.run_in_executor(None, _read_last_run)
                return datetime.fromisoformat(last_run_str)
        except (OSError, ValueError) as e:
            self.error_logger.warning("Could not read last run time: %s", e)

        return None

    async def _clean_all_tracks_metadata(self, tracks: list["TrackDict"]) -> list["TrackDict"]:
        """Clean metadata for all tracks (Step 1 of pipeline).

        Args:
            tracks: List of tracks to process

        Returns:
            List of updated tracks

        """
        self.console_logger.info("Step 1/3: Cleaning metadata")
        cleaned_tracks: list[TrackDict] = []

        for track in tracks:
            cleaned_track = await self._process_single_track_for_pipeline_cleaning(track)
            if cleaned_track is not None:
                cleaned_tracks.append(cleaned_track)

        self.console_logger.info("Cleaned %d tracks", len(cleaned_tracks))
        return cleaned_tracks

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
        return updated_track

    async def _update_all_genres(self, tracks: list["TrackDict"], last_run_time: datetime | None, force: bool) -> list["TrackDict"]:
        """Update genres for all tracks (Step 2 of pipeline).

        Args:
            tracks: List of tracks to process
            last_run_time: Last run time for incremental updates
            force: Force all operations

        Returns:
            List of updated genre tracks

        """
        self.console_logger.info("Step 2/3: Updating genres")
        updated_genre_tracks, _ = await self.genre_manager.update_genres_by_artist_async(tracks, last_run_time=last_run_time, force=force)
        self.console_logger.info("Updated genres for %d tracks", len(updated_genre_tracks))
        return updated_genre_tracks

    async def _update_all_years(self, tracks: list["TrackDict"], force: bool) -> None:
        """Update years for all tracks (Step 3 of pipeline).

        Args:
            tracks: List of tracks to process
            force: Force all operations

        """
        self.console_logger.info("=== BEFORE Step 3/3: Updating album years ===")
        self.console_logger.info("Step 3/3: Updating album years")
        try:
            await self.year_retriever.process_album_years(tracks, force=force)
            self.console_logger.info("=== AFTER Step 3 completed successfully ===")
        except Exception:
            self.error_logger.exception("=== ERROR in Step 3 ===")
            raise

    async def _save_pipeline_results(self) -> None:
        """Save the combined results of the pipeline with full track synchronization."""
        # Skip full library sync when using test artists for performance
        if self.dry_run_test_artists:
            self.console_logger.info("Skipping full library sync (using test artists)")
            return

        # Fetch ALL current tracks from Music.app for complete synchronization
        # Respect test artist filter when in test mode
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
