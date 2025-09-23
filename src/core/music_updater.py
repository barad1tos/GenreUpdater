"""Refactored Music Updater core class.

This is a streamlined version that uses the new modular components.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from src.services.track_delta_service import (
    TrackDelta,
    TrackSummary,
    apply_track_delta_to_map,
    compute_track_delta,
)
from src.utils.core.logger import get_full_log_path
from src.utils.core.run_tracking import IncrementalRunTracker
from src.utils.data.metadata import clean_names, is_music_app_running
from src.utils.data.models import TrackDict
from src.utils.monitoring.reports import (
    load_track_list,
    save_changes_report,
    save_track_map_to_csv,
    sync_track_list_with_current,
)

from .modules.processing.genre_manager import GenreManager
from .modules.processing.track_processor import TrackProcessor
from .modules.processing.year_retriever import YearRetriever
from .modules.verification.database_verifier import DatabaseVerifier

TRACK_LIST_REL_PATH = "csv/track_list.csv"

if TYPE_CHECKING:
    from src.services.dependencies_service import DependencyContainer


@dataclass(slots=True)
class IncrementalPreparationResult:
    """Container for incremental processing data."""

    csv_map: dict[str, "TrackDict"]
    summary_map: dict[str, TrackSummary]
    delta: TrackDelta
    tracks_for_processing: list["TrackDict"]
    affected_artists: set[str]
    changed_tracks: list["TrackDict"]
    fallback_required: bool = False


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

    async def run_revert_years(self, artist: str, album: str | None, backup_csv: str | None = None) -> None:
        """Revert year updates for an artist (optionally per album).

        Uses backup CSV if provided; otherwise uses the latest changes_report.csv.
        """
        from src.utils.data import repair as repair_utils  # noqa: PLC0415

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

        # Skip full library sync in incremental mode for performance
        # Full sync will happen at the end of main pipeline
        if getattr(self, "_incremental_mode", False):
            self.console_logger.info("Skipping full library sync in _save_clean_results (incremental mode)")
            return

        # Sync with the database
        csv_path = self._get_track_list_path()
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

        if force:
            await self._run_full_pipeline()
            return

        prep = await self._prepare_incremental_plan()
        if prep.fallback_required:
            self.console_logger.warning("Falling back to full pipeline run")
            await self._run_full_pipeline()
            return

        self.console_logger.info(
            "Incremental delta: %d new, %d updated, %d removed", len(prep.delta.new_ids), len(prep.delta.updated_ids), len(prep.delta.removed_ids)
        )

        if not prep.delta.has_updates() and prep.delta.has_removals():
            await self._finalize_incremental_without_pipeline(prep)
            return

        if prep.delta.is_empty():
            await self._finalize_incremental_without_pipeline(prep)
            return

        tracks = prep.tracks_for_processing
        if not tracks:
            self.console_logger.warning("Incremental preparation produced no tracks; running full pipeline")
            await self._run_full_pipeline()
            return

        self.console_logger.info(
            "Processing %d tracks across %d artists (incremental)",
            len(tracks),
            len(prep.affected_artists),
        )

        last_run_time = await self._get_last_run_time(force=False)
        await self._clean_all_tracks_metadata(tracks)
        await self._update_all_genres(tracks, last_run_time, force=False)
        await self._update_all_years(tracks, force=False)
        await self._save_incremental_results(prep)
        await self.database_verifier.update_last_incremental_run()

        self.console_logger.info("Main update pipeline completed successfully (incremental)")

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
            batch_tracks = cast(list["TrackDict"], await self.track_processor.fetch_tracks_in_batches(batch_size=batch_size))

            # Store the freshly fetched library in cache so later sync operations can reuse it
            if batch_tracks:
                cache_ttl = int(self.config.get("caching", {}).get("library_cache_ttl_seconds", 7200))
                await self.track_processor.cache_service.set_async("tracks_all", batch_tracks, ttl=cache_ttl)

            return batch_tracks

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

        tracker = IncrementalRunTracker(self.config)
        return await tracker.get_last_run_timestamp()

    def _get_track_list_path(self) -> str:
        """Return the absolute path to the track list CSV."""

        return get_full_log_path(self.config, "csv_output_file", TRACK_LIST_REL_PATH)

    async def _run_full_pipeline(self) -> None:
        """Execute the full pipeline across the entire library."""

        tracks = await self._fetch_tracks_for_pipeline_mode()
        if not tracks:
            self.console_logger.warning("No tracks found in Music.app")
            return

        self.console_logger.info("Found %d tracks in Music.app (full run)", len(tracks))

        last_run_time = await self._get_last_run_time(force=True)
        await self._clean_all_tracks_metadata(tracks)
        await self._update_all_genres(tracks, last_run_time, True)
        await self._update_all_years(tracks, True)
        await self._save_pipeline_results()
        await self.database_verifier.update_last_incremental_run()
        self.console_logger.info("Main update pipeline completed successfully (full run)")

    async def _prepare_incremental_plan(self) -> IncrementalPreparationResult:
        """Prepare incremental processing context by comparing CSV snapshot with Music.app."""

        csv_path = self._get_track_list_path()
        csv_map = load_track_list(csv_path)

        summaries = await self.track_processor.fetch_track_summaries()
        if not summaries:
            self.console_logger.warning("Track summary retrieval failed; falling back to full run")
            return IncrementalPreparationResult(
                csv_map=csv_map,
                summary_map={},
                delta=TrackDelta([], [], []),
                tracks_for_processing=[],
                affected_artists=set(),
                changed_tracks=[],
                fallback_required=True,
            )

        summary_map = {summary.track_id: summary for summary in summaries}
        delta = compute_track_delta(summaries, csv_map)

        if not delta.has_updates():
            return IncrementalPreparationResult(
                csv_map=csv_map,
                summary_map=summary_map,
                delta=delta,
                tracks_for_processing=[],
                affected_artists=set(),
                changed_tracks=[],
                fallback_required=False,
            )

        changed_tracks, fallback_required = await self._fetch_changed_track_details(delta)
        if fallback_required:
            return IncrementalPreparationResult(
                csv_map=csv_map,
                summary_map=summary_map,
                delta=delta,
                tracks_for_processing=[],
                affected_artists=set(),
                changed_tracks=[],
                fallback_required=True,
            )

        affected_artists = self._extract_affected_artists(changed_tracks)
        track_scope = await self._collect_tracks_for_artists(changed_tracks, affected_artists)

        return IncrementalPreparationResult(
            csv_map=csv_map,
            summary_map=summary_map,
            delta=delta,
            tracks_for_processing=list(track_scope.values()),
            affected_artists=affected_artists,
            changed_tracks=changed_tracks,
        )

    async def _fetch_changed_track_details(self, delta: TrackDelta) -> tuple[list[TrackDict], bool]:
        """Return detailed metadata for changed tracks and whether a fallback is required."""

        if not delta.has_updates():
            return [], False

        changed_ids = delta.new_ids + delta.updated_ids
        changed_tracks = await self.track_processor.fetch_tracks_by_ids(changed_ids)
        if not changed_tracks or len(changed_tracks) < len(changed_ids):
            return [], True
        return changed_tracks, False

    @staticmethod
    def _extract_affected_artists(tracks: list[TrackDict]) -> set[str]:
        """Collect artists impacted by the incremental change set."""

        return {str(getattr(track, "artist", "")) for track in tracks if getattr(track, "artist", "")}

    async def _collect_tracks_for_artists(
        self,
        base_tracks: list[TrackDict],
        artists: set[str],
    ) -> dict[str, TrackDict]:
        """Collect track metadata for changed tracks and their artists."""

        track_map: dict[str, TrackDict] = {track.id: track for track in base_tracks if track.id}
        for artist in artists:
            if not artist:
                continue
            artist_tracks = await self.track_processor.fetch_tracks_async(
                artist=artist,
                force_refresh=True,
            )
            for artist_track in artist_tracks:
                if artist_track.id:
                    track_map[artist_track.id] = artist_track
        return track_map

    async def _build_summary_lookup(self, fallback: dict[str, TrackSummary]) -> dict[str, TrackSummary]:
        """Build a summary lookup, falling back when latest summaries are unavailable."""

        latest = await self.track_processor.fetch_track_summaries()
        if latest:
            return {summary.track_id: summary for summary in latest}
        return fallback

    @staticmethod
    def _update_snapshot_dates(
        track_map: dict[str, TrackDict],
        summary_lookup: dict[str, TrackSummary],
    ) -> None:
        """Update date fields in the CSV snapshot using latest summaries."""

        for track_id, summary in summary_lookup.items():
            track = track_map.get(track_id)
            if track is None:
                continue
            track.last_modified = summary.last_modified or None
            if summary.date_added:
                track.date_added = summary.date_added

    async def _finalize_incremental_without_pipeline(self, prep: IncrementalPreparationResult) -> None:
        """Handle incremental execution when no pipeline work is required."""

        if not prep.delta.has_removals():
            self.console_logger.info("No library changes detected; incremental pipeline skipped")
            await self.database_verifier.update_last_incremental_run()
            return

        csv_path = self._get_track_list_path()
        apply_track_delta_to_map(
            prep.csv_map,
            [],
            prep.summary_map,
            prep.delta.removed_ids,
        )
        self._update_snapshot_dates(prep.csv_map, prep.summary_map)
        save_track_map_to_csv(prep.csv_map, csv_path, self.console_logger, self.error_logger)
        self.console_logger.info("Incremental removal sync complete: %d removed", len(prep.delta.removed_ids))
        await self.database_verifier.update_last_incremental_run()

    async def _save_incremental_results(self, prep: IncrementalPreparationResult) -> None:
        """Persist results of an incremental pipeline run."""

        csv_path = self._get_track_list_path()
        final_tracks = await self._collect_tracks_for_artists(prep.changed_tracks, prep.affected_artists)
        summary_lookup = await self._build_summary_lookup(prep.summary_map)

        apply_track_delta_to_map(
            prep.csv_map,
            final_tracks.values(),
            summary_lookup,
            prep.delta.removed_ids,
        )
        self._update_snapshot_dates(prep.csv_map, summary_lookup)
        save_track_map_to_csv(prep.csv_map, csv_path, self.console_logger, self.error_logger)
        self.console_logger.info("Sync complete: %d tracks updated, %d removed", len(final_tracks), len(prep.delta.removed_ids))

    async def _clean_all_tracks_metadata(self, tracks: list[TrackDict]) -> list[TrackDict]:
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
        track.name = cleaned_track_name
        track.album = cleaned_album_name
        updated_track = track.copy()
        updated_track.name = cleaned_track_name
        updated_track.album = cleaned_album_name
        return updated_track

    async def _update_all_genres(self, tracks: list["TrackDict"], last_run_time: datetime | None, force: bool) -> list["TrackDict"]:
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
        if self.dry_run_test_artists:
            self.console_logger.info("Skipping full library sync (using test artists)")
            return

        all_current_tracks = await self.track_processor.fetch_tracks_async()
        if not all_current_tracks:
            return

        await self._update_tracks_with_summaries(all_current_tracks)
        await self._sync_tracks_to_csv(all_current_tracks)

    async def _update_tracks_with_summaries(self, tracks: list["TrackDict"]) -> None:
        """Update tracks with their summary information."""
        summaries = await self.track_processor.fetch_track_summaries()
        if not summaries:
            return

        summary_lookup = {summary.track_id: summary for summary in summaries}
        for track in tracks:
            self._apply_summary_to_track(track, summary_lookup)

    @staticmethod
    def _apply_summary_to_track(track: "TrackDict", summary_lookup: dict[str, Any]) -> None:
        """Apply summary data to a single track."""
        if not track.id:
            return

        summary = summary_lookup.get(track.id)
        if summary is None:
            return

        track.last_modified = summary.last_modified or None
        if summary.date_added:
            track.date_added = summary.date_added

    async def _sync_tracks_to_csv(self, tracks: list["TrackDict"]) -> None:
        """Synchronize tracks to CSV file."""
        csv_path = self._get_track_list_path()
        await sync_track_list_with_current(
            tracks,
            csv_path,
            self.deps.cache_service,
            self.console_logger,
            self.error_logger,
            partial_sync=True,  # Incremental sync - only process new/changed tracks
        )
