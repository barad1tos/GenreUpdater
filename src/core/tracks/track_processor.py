"""Track processing functionality for Music Genre Updater.

This module handles fetching tracks from Music.app, caching,
and updating track properties.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from core.tracks.artist_renamer import ArtistRenamer
from core.tracks.batch_fetcher import BatchTrackFetcher
from core.tracks.cache_manager import TrackCacheManager
from core.tracks.update_executor import TrackUpdateExecutor
from core.utils.datetime_utils import datetime_to_applescript_timestamp
from services.cache.snapshot import LibrarySnapshotService
from core.models.metadata_utils import parse_tracks
from core.models.track_models import TrackDict
from services.apple.applescript_client import NO_TRACKS_FOUND
from core.models.validators import SecurityValidationError, SecurityValidator
from metrics import Analytics

if TYPE_CHECKING:
    from core.models.protocols import AppleScriptClientProtocol, CacheServiceProtocol


class TrackProcessor:
    """Handles track fetching and updating operations."""

    def __init__(
        self,
        ap_client: "AppleScriptClientProtocol",
        cache_service: "CacheServiceProtocol",
        *,
        library_snapshot_service: LibrarySnapshotService | None = None,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        analytics: Analytics,
        dry_run: bool = False,
        security_validator: SecurityValidator | None = None,
    ) -> None:
        """Initialize the TrackProcessor.

        Args:
            ap_client: AppleScript client for Music.app communication
            cache_service: Cache service for storing track data
            library_snapshot_service: Optional library snapshot service for cached snapshots
            console_logger: Logger for console output
            error_logger: Logger for error messages
            config: Configuration dictionary
            analytics: Analytics instance for tracking method calls
            dry_run: Whether to run in dry-run mode
            security_validator: Optional security validator for input sanitization

        """
        self.ap_client = ap_client
        self.cache_service = cache_service
        self.snapshot_service = library_snapshot_service
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.config = config
        self.analytics = analytics
        self.dry_run = dry_run
        self._dry_run_actions: list[dict[str, Any]] = []
        # Use the provided validator or create a default one for backward compatibility
        self.security_validator = security_validator or SecurityValidator(error_logger)
        # Dry run context from MusicUpdater
        self.dry_run_mode: str = ""
        self.dry_run_test_artists: set[str] = set()
        self.artist_renamer: ArtistRenamer | None = None

        # Initialize cache manager for snapshot/cache operations
        self.cache_manager = TrackCacheManager(
            cache_service=cache_service,
            snapshot_service=library_snapshot_service,
            console_logger=console_logger,
            current_time_func=self._current_time,
        )

        # Initialize update executor for track update operations
        self.update_executor: TrackUpdateExecutor = TrackUpdateExecutor(
            ap_client=ap_client,
            cache_service=cache_service,
            security_validator=self.security_validator,
            config=config,
            console_logger=console_logger,
            error_logger=error_logger,
            dry_run=dry_run,
        )

        # Initialize batch fetcher for large library processing
        self.batch_fetcher: BatchTrackFetcher = BatchTrackFetcher(
            ap_client=ap_client,
            cache_service=cache_service,
            console_logger=console_logger,
            error_logger=error_logger,
            config=config,
            track_validator=self._validate_tracks_security,
            artist_processor=self._apply_artist_renames,
            snapshot_loader=self._load_tracks_from_snapshot,
            snapshot_persister=self._update_snapshot,
            can_use_snapshot=self._can_use_snapshot,
            dry_run=dry_run,
            analytics=analytics,
        )

    def set_dry_run_context(self, mode: str, test_artists: set[str]) -> None:
        """Set dry run context for test mode filtering.

        Args:
            mode: Dry run mode ('test' or other)
            test_artists: Set of test artists for filtering

        """
        self.dry_run_mode = mode
        self.dry_run_test_artists = test_artists

    def set_artist_renamer(self, renamer: ArtistRenamer) -> None:
        """Attach artist renamer service for automatic post-fetch processing."""
        self.artist_renamer = renamer

    @staticmethod
    def _current_time() -> datetime:
        """Return naive UTC timestamp for cache metadata."""
        return datetime.now(UTC).replace(tzinfo=None)

    async def _process_test_artists(self, _force_refresh: bool) -> list[TrackDict]:
        """Process tracks for all configured test artists.

        Args:
            _force_refresh: Whether to force refresh

        Returns:
            List of tracks from all test artists

        """
        # Determine test artists source - prioritize dry run context over config
        if self.dry_run_test_artists and self.dry_run_mode == "test":
            test_artists_list = list(self.dry_run_test_artists)
            self.console_logger.info("Using test artist filter from dry run context: %s", test_artists_list)
        elif config_test_artists := self.config.get("development", {}).get("test_artists", []):
            test_artists_list = config_test_artists
            self.console_logger.info("Using test artist filter from config: %s", test_artists_list)
        else:
            return []

        # Fetch tracks for each test artist
        collected_tracks: list[TrackDict] = []
        for test_artist in test_artists_list:
            self.console_logger.info("Fetching tracks for test artist: %s", test_artist)
            artist_tracks = await self.fetch_tracks_async(test_artist, _force_refresh, ignore_test_filter=True)
            collected_tracks.extend(artist_tracks)
        return collected_tracks

    async def _apply_artist_renames(self, tracks: list[TrackDict]) -> None:
        """Apply artist rename rules if service is configured."""
        if self.artist_renamer is None or not tracks:
            return

        try:
            await self.artist_renamer.rename_tracks(tracks)
        except (OSError, ValueError, RuntimeError):
            self.error_logger.exception("Artist renamer failed")

    async def _get_cached_tracks(self, cache_key: str) -> Sequence[TrackDict] | None:
        """Retrieve tracks from the cache with type validation.

        Args:
            cache_key: Cache key to retrieve

        Returns:
            List of tracks if found and valid, None otherwise
        """
        return await self.cache_manager.get_cached_tracks(cache_key)

    async def _materialize_cached_tracks(self, cache_key: str, artist: str | None) -> list[TrackDict] | None:
        """Return validated cached tracks if available."""
        cached_tracks = await self._get_cached_tracks(cache_key)
        if cached_tracks is None:
            return None

        validated_cached = self._validate_tracks_security(list(cached_tracks))
        self.console_logger.info(
            "Using cached data for %s, validated %d/%d tracks",
            artist or "all artists",
            len(validated_cached),
            len(cached_tracks),
        )
        await self._apply_artist_renames(validated_cached)
        return validated_cached

    async def _try_fetch_test_tracks(self, force_refresh: bool, ignore_test_filter: bool, artist: str | None) -> list[TrackDict] | None:
        """Return test-mode tracks when applicable."""
        if artist is not None or ignore_test_filter:
            return None

        test_tracks = await self._process_test_artists(force_refresh)
        if test_tracks or self.config.get("development", {}).get("test_artists", []):
            return test_tracks
        return None

    async def _try_fetch_snapshot_tracks(self, cache_key: str, use_snapshot: bool, force_refresh: bool) -> list[TrackDict] | None:
        """Return snapshot tracks when snapshot caching is eligible."""
        if not use_snapshot or force_refresh:
            return None

        snapshot_tracks = await self._load_tracks_from_snapshot()
        if snapshot_tracks is None:
            return None

        await self.cache_service.set_async(cache_key, snapshot_tracks)
        self.console_logger.info("Serving tracks from snapshot cache (%d items)", len(snapshot_tracks))
        return snapshot_tracks

    def _can_use_snapshot(self, artist: str | None) -> bool:
        """Return True when snapshot caching can be used for this request."""
        return artist is None and self.cache_manager.can_use_snapshot()

    async def _load_tracks_from_snapshot(self) -> list[TrackDict] | None:
        """Attempt to serve tracks via snapshot and incremental delta updates."""
        service = self.snapshot_service
        if service is None:
            return None

        snapshot_tracks = await service.load_snapshot()
        if snapshot_tracks is None:
            self.console_logger.debug("Snapshot cache missing on disk")
            return None

        if await service.is_snapshot_valid():
            # Snapshot valid - logged in is_snapshot_valid()
            return snapshot_tracks

        # Snapshot is invalid
        if not service.is_delta_enabled():
            self.console_logger.warning("Snapshot stale and delta updates disabled; full rescan required")
            return None

        # Delta enabled - attempt incremental refresh
        self.console_logger.info(
            "Attempting delta update: %d cached tracks + new changes since last scan",
            len(snapshot_tracks),
        )
        merged_tracks = await self._refresh_snapshot_from_delta(snapshot_tracks, service)
        return merged_tracks if merged_tracks is not None else None

    async def _refresh_snapshot_from_delta(
        self,
        snapshot_tracks: list[TrackDict],
        snapshot_service: LibrarySnapshotService,
    ) -> list[TrackDict] | None:
        """Merge snapshot with delta updates when available."""
        metadata = await snapshot_service.get_snapshot_metadata()
        min_date = metadata.last_full_scan if metadata else None

        delta_cache = await snapshot_service.load_delta()
        if delta_cache and (candidates := [candidate for candidate in (min_date, delta_cache.last_run) if candidate is not None]):
            min_date = max(candidates)

        if min_date is None:
            self.console_logger.info("Unable to determine delta window; falling back to full scan")
            return None

        delta_tracks = await self._fetch_tracks_from_applescript(min_date_added=min_date)
        if not delta_tracks:
            self.console_logger.info("Delta fetch returned no tracks; full rescan will be scheduled")
            return None

        merged_tracks = self._merge_tracks(snapshot_tracks, delta_tracks)
        if not self.dry_run:
            await self._update_snapshot(merged_tracks, [track.id for track in delta_tracks])
        self.console_logger.info(
            "Updated snapshot from delta window starting %s (+%d tracks)",
            min_date.isoformat(),
            len(delta_tracks),
        )
        return merged_tracks

    async def _update_snapshot(self, tracks: list[TrackDict], processed_track_ids: Sequence[str] | None = None) -> None:
        """Persist the latest snapshot, metadata, and delta state."""
        await self.cache_manager.update_snapshot(tracks, processed_track_ids)

    @staticmethod
    def _merge_tracks(existing: list[TrackDict], updates: list[TrackDict]) -> list[TrackDict]:
        """Merge delta updates into the existing snapshot while preserving order."""
        return TrackCacheManager.merge_tracks(existing, updates)

    def _validate_tracks_security(self, tracks: list[TrackDict]) -> list[TrackDict]:
        """Validate tracks for security and convert to proper format.

        Args:
            tracks: List of parsed tracks to validate

        Returns:
            List of validated TrackDict instances
        """
        validated_tracks: list[TrackDict] = []
        for track in tracks:
            try:
                # Convert TrackDict to dict[str, Any] for validation
                track_dict: dict[str, Any] = {
                    "id": track.id,
                    "artist": track.artist,
                    "name": track.name,
                    "album": track.album,
                    "genre": track.genre,
                    "year": track.year,
                    "date_added": track.date_added,
                    "track_status": track.track_status,
                }
                # Preserve album_artist if present (extra field on TrackDict)
                aa = track.get("album_artist")
                if aa is not None:
                    track_dict["album_artist"] = aa
                validated_dict = self.security_validator.validate_track_data(track_dict)
                # ONLY TrackDict, not dict
                validated_track = TrackDict(**validated_dict)
                validated_tracks.append(validated_track)
            except SecurityValidationError as e:
                self.error_logger.warning(
                    "Security validation failed for track %s: %s",
                    track.get("id", "unknown"),
                    e,
                )
                # Skip this track due to security concerns
                continue
        return validated_tracks

    def _get_applescript_timeout(self, is_single_artist: bool) -> int:
        """Get appropriate timeout for AppleScript execution.

        Args:
            is_single_artist: Whether this is a single artist fetch or full library

        Returns:
            Timeout value in seconds
        """
        if is_single_artist:
            # Single artist fetch - shorter timeout (artist was explicitly provided)
            timeout_value = self.config.get("applescript_timeouts", {}).get("single_artist_fetch", 600)
            return int(timeout_value) if timeout_value is not None else 600
        # Full library fetch or test artist scenario - longer timeout
        timeout_value = self.config.get("applescript_timeouts", {}).get("full_library_fetch", 3600)
        return int(timeout_value) if timeout_value is not None else 3600

    async def _fetch_tracks_from_applescript(
        self,
        artist: str | None = None,
        min_date_added: datetime | None = None,
    ) -> list[TrackDict]:
        """Fetch tracks directly from Music.app via AppleScript."""
        try:
            # Remember if artist was originally provided by caller
            original_artist_provided = artist is not None

            args: list[str] = [artist or "", "", ""]
            if min_date_added:
                timestamp = datetime_to_applescript_timestamp(min_date_added)
                args.append(str(timestamp))

            self.console_logger.info(
                "Running AppleScript: fetch_tracks.scpt with args: %s",
                ", ".join(arg for arg in args if arg),
            )

            # Execute AppleScript with appropriate timeout based on operation type
            timeout = self._get_applescript_timeout(original_artist_provided)

            raw_output = await self.ap_client.run_script("fetch_tracks.scpt", args, timeout=timeout)

            # DEBUG: Log raw output details
            self.error_logger.info(f"DEBUG: AppleScript returned {len(raw_output) if raw_output else 0} characters")
            if raw_output:
                self.error_logger.info(f"DEBUG: First 200 chars: {raw_output[:200]}")
                field_sep_found = "\x1e" in raw_output
                line_sep_found = "\x1d" in raw_output
                self.error_logger.info(f"DEBUG: Raw output contains separators (\\x1E): {field_sep_found}, line (\\x1D): {line_sep_found}")

            if not raw_output:
                self.error_logger.error("AppleScript returned empty output")
                return []

            # Check for AppleScript status codes
            if raw_output.startswith("ERROR:"):
                self.error_logger.error(f"AppleScript error: {raw_output}")
                return []
            if raw_output == NO_TRACKS_FOUND:
                self.console_logger.info("No tracks found matching filter criteria")
                return []

            # Parse the raw output
            tracks = parse_tracks(raw_output, self.error_logger)

            # TEMPORARY DEBUG
            # Parsed tracks count (noise reduction)

            # Validate each track for security
            validated_tracks = self._validate_tracks_security(tracks)
            await self._apply_artist_renames(validated_tracks)

            self.console_logger.info(
                "AppleScript fetch_tracks.scpt executed successfully, got %d bytes, validated %d/%d tracks",
                len(raw_output),
                len(validated_tracks),
                len(tracks),
            )
        except (OSError, ValueError, RuntimeError):
            self.error_logger.exception("Error running fetch_tracks AppleScript")
            return []

        return validated_tracks

    @Analytics.track_instance_method("track_fetch_by_ids")
    async def fetch_tracks_by_ids(self, track_ids: list[str]) -> list[TrackDict]:
        """Fetch detailed track metadata for the provided track IDs."""

        if not track_ids:
            return []

        batch_size = int(self.config.get("batch_processing", {}).get("ids_batch_size", 200))
        batch_size = max(batch_size, 1)
        batch_size = min(batch_size, 1000)  # Enforce upper limit to prevent excessive memory/performance issues

        collected: list[TrackDict] = []
        total_batches = (len(track_ids) + batch_size - 1) // batch_size

        for i in range(0, len(track_ids), batch_size):
            batch = track_ids[i : i + batch_size]
            batch_num = i // batch_size + 1
            ids_param = ",".join(batch)

            raw_output = await self.ap_client.run_script(
                "fetch_tracks_by_ids.scpt",
                [ids_param],
                timeout=self._get_applescript_timeout(False),
                label=f"fetch_tracks_by_ids.scpt [{batch_num}/{total_batches}]",
            )

            if not raw_output:
                continue

            parsed_tracks = parse_tracks(raw_output, self.error_logger)
            validated_tracks = self._validate_tracks_security(parsed_tracks)
            await self._apply_artist_renames(validated_tracks)
            collected.extend(validated_tracks)

        return collected

    @Analytics.track_instance_method("track_fetch_all")
    async def fetch_tracks_async(
        self,
        artist: str | None = None,
        force_refresh: bool = False,
        dry_run_test_tracks: list[TrackDict] | None = None,
        ignore_test_filter: bool = False,
    ) -> list[TrackDict]:
        """Fetch tracks from cache or Music.app with caching.

        Args:
            artist: Optional artist filter
            force_refresh: Force refresh from Music.app
            dry_run_test_tracks: Test tracks for dry-run mode
            ignore_test_filter: Whether to ignore test_artists configuration

        Returns:
            List of track dictionaries

        """
        # Handle dry-run test mode
        if dry_run_test_tracks is not None:
            self.console_logger.info("DRY RUN: Using %d test tracks", len(dry_run_test_tracks))
            return dry_run_test_tracks

        # Generate cache key
        cache_key = f"tracks_{artist}" if artist else "tracks_all"

        use_snapshot = self._can_use_snapshot(artist)
        result: list[TrackDict] | None = None

        if not force_refresh:
            result = await self._materialize_cached_tracks(cache_key, artist)

        if result is None:
            result = await self._try_fetch_test_tracks(force_refresh, ignore_test_filter, artist)

        if result is None:
            result = await self._try_fetch_snapshot_tracks(cache_key, use_snapshot, force_refresh)

        if result is None:
            tracks = await self._fetch_tracks_from_applescript(artist=artist)

            if use_snapshot and tracks and not self.dry_run:
                await self._update_snapshot(tracks, [track.id for track in tracks])

            if tracks:
                await self.cache_service.set_async(cache_key, tracks)
                self.console_logger.info("Cached %d tracks for key: %s", len(tracks), cache_key)

            result = tracks

        if not result:
            self.console_logger.warning(
                "No tracks fetched for key: %s. This may indicate an empty library or an upstream issue.",
                cache_key,
            )

        return result or []

    @Analytics.track_instance_method("track_fetch_batches")
    async def fetch_tracks_in_batches(
        self,
        batch_size: int = 1000,
        *,
        skip_snapshot_check: bool = False,
    ) -> list[TrackDict]:
        """Fetch all tracks from Music.app in batches to avoid timeout.

        Delegates to BatchTrackFetcher for batch-based fetching with snapshot support.

        Args:
            batch_size: Number of tracks to fetch per batch
            skip_snapshot_check: Skip snapshot validation (used when already validated upstream)

        Returns:
            List of all track dictionaries
        """
        return await self.batch_fetcher.fetch_all_tracks(
            batch_size,
            skip_snapshot_check=skip_snapshot_check,
        )

    # ==================== Update Operations (delegated to TrackUpdateExecutor) ====================

    async def update_track_async(
        self,
        track_id: str,
        new_track_name: str | None = None,
        new_album_name: str | None = None,
        new_genre: str | None = None,
        new_year: str | None = None,
        track_status: str | None = None,
        original_artist: str | None = None,
        original_album: str | None = None,
        original_track: str | None = None,
    ) -> bool:
        """Update multiple properties of a track.

        Delegates to TrackUpdateExecutor.

        Args:
            track_id: ID of the track to update
            new_track_name: New track name (optional)
            new_album_name: New album name (optional)
            new_genre: New genre (optional)
            new_year: New year (optional)
            track_status: Track status to check for prerelease (optional)
            original_artist: Original artist name for contextual logging (optional)
            original_album: Original album name for contextual logging (optional)
            original_track: Original track name for contextual logging (optional)

        Returns:
            True if all updates are successful, False if any failed
        """
        result: bool = await self.update_executor.update_track_async(
            track_id=track_id,
            new_track_name=new_track_name,
            new_album_name=new_album_name,
            new_genre=new_genre,
            new_year=new_year,
            track_status=track_status,
            original_artist=original_artist,
            original_album=original_album,
            original_track=original_track,
        )
        return result

    async def update_artist_async(
        self,
        track: TrackDict,
        new_artist_name: str,
        *,
        original_artist: str | None = None,
        update_album_artist: bool = False,
    ) -> bool:
        """Update the artist name for a track.

        Delegates to TrackUpdateExecutor.

        Args:
            track: Track dictionary representing the target track
            new_artist_name: Artist name to apply
            original_artist: Original artist for logging context (optional)
            update_album_artist: If True, also update album_artist field

        Returns:
            True if update succeeded, False otherwise
        """
        result: bool = await self.update_executor.update_artist_async(
            track=track,
            new_artist_name=new_artist_name,
            original_artist=original_artist,
            update_album_artist=update_album_artist,
        )
        return result

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get the list of dry-run actions recorded.

        Delegates to TrackUpdateExecutor.

        Returns:
            List of dry-run action dictionaries
        """
        return self.update_executor.get_dry_run_actions()
