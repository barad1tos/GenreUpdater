"""Track processing functionality for Music Genre Updater.

This module handles fetching tracks from Music.app, caching,
and updating track properties.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from src.utils.data.metadata import parse_tracks
from src.utils.data.models import TrackDict
from src.utils.data.validators import SecurityValidationError, SecurityValidator, is_valid_track_item
from src.utils.monitoring import Analytics

if TYPE_CHECKING:
    from src.utils.data.protocols import AppleScriptClientProtocol, CacheServiceProtocol


class TrackProcessor:
    """Handles track fetching and updating operations."""

    def __init__(
        self,
        ap_client: "AppleScriptClientProtocol",
        cache_service: "CacheServiceProtocol",
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        analytics: "Analytics",
        dry_run: bool = False,
        security_validator: SecurityValidator | None = None,
    ) -> None:
        """Initialize the TrackProcessor.

        Args:
            ap_client: AppleScript client for Music.app communication
            cache_service: Cache service for storing track data
            console_logger: Logger for console output
            error_logger: Logger for error messages
            config: Configuration dictionary
            analytics: Analytics instance for tracking method calls
            dry_run: Whether to run in dry-run mode
            security_validator: Optional security validator for input sanitization

        """
        self.ap_client = ap_client
        self.cache_service = cache_service
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

    def set_dry_run_context(self, mode: str, test_artists: set[str]) -> None:
        """Set dry run context for test mode filtering.

        Args:
            mode: Dry run mode ('test' or other)
            test_artists: Set of test artists for filtering

        """
        self.dry_run_mode = mode
        self.dry_run_test_artists = test_artists

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

    async def _get_cached_tracks(self, cache_key: str) -> Sequence[TrackDict] | None:
        """Retrieve tracks from the cache with type validation.

        Args:
            cache_key: Cache key to retrieve

        Returns:
            List of tracks if found and valid, None otherwise

        """
        cached_value = await self.cache_service.get_async(cache_key)
        if cached_value is None:
            return None

        # With improved overloads, cached_value is guaranteed to be list[TrackDict] for string keys
        cached_list = cached_value

        # Build validated track list with proper typing
        validated_tracks: list[TrackDict] = []

        # Validate each item in the cached list
        for i, item in enumerate(cached_list):
            # Since we typed cached_list as list[dict[str, Any]], each item should be dict[str, Any]
            # But we still need runtime validation for data integrity from cache
            if not is_valid_track_item(item):
                self.console_logger.warning(
                    "Cached data for %s contains invalid track dict at index %d. Ignoring cache.",
                    cache_key,
                    i,
                )
                return None

            # The item is valid after type guard check, add it to the result
            validated_tracks.append(item)

        return validated_tracks

    async def _fetch_tracks_from_applescript(
        self, artist: str | None = None, _force_refresh: bool = False, ignore_test_filter: bool = False
    ) -> list[TrackDict]:
        """Fetch tracks directly from Music.app via AppleScript.

        Args:
            artist: Optional artist filter
            _force_refresh: Whether to force refresh (currently unused)
            ignore_test_filter: Whether to ignore test_artists configuration

        Returns:
            List of track dictionaries

        """
        try:
            # Remember if artist was originally provided by caller
            original_artist_provided = artist is not None

            # Handle test artists if no specific artist and not ignoring filter
            if not ignore_test_filter and not artist:
                test_result = await self._process_test_artists(_force_refresh)
                # If test_artists is empty, proceed with full library fetch
                if test_result or self.config.get("development", {}).get("test_artists", []):
                    return test_result

            # Build arguments for AppleScript
            args: list[str] = []
            if artist:
                args.append(artist)

            self.console_logger.info(
                "Running AppleScript: fetch_tracks.scpt with args: %s",
                artist or "",
            )

            # Execute AppleScript with appropriate timeout based on operation type
            if original_artist_provided:
                # Single artist fetch - shorter timeout (artist was explicitly provided)
                timeout = self.config.get("applescript_timeouts", {}).get("single_artist_fetch", 600)
            else:
                # Full library fetch or test artist scenario - longer timeout
                timeout = self.config.get("applescript_timeouts", {}).get("full_library_fetch", 3600)

            raw_output = await self.ap_client.run_script("fetch_tracks.scpt", args, timeout=timeout)

            # DEBUG: Log raw output details
            self.error_logger.info(f"DEBUG: AppleScript returned {len(raw_output) if raw_output else 0} characters")
            if raw_output:
                self.error_logger.info(f"DEBUG: First 200 chars: {raw_output[:200]}")
                self.error_logger.info(f"DEBUG: Raw output contains separators - field (\\x1E): {'\x1E' in raw_output}, line (\\x1D): {'\x1D' in raw_output}")

            if not raw_output:
                self.error_logger.error("AppleScript returned empty output")
                return []

            # Parse the raw output
            tracks = parse_tracks(raw_output, self.error_logger)

            # TEMPORARY DEBUG
            # Parsed tracks count (noise reduction)

            # Validate each track for security
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

        # Try cache first unless force refresh
        if not force_refresh:
            cached_tracks = await self._get_cached_tracks(cache_key)
            if cached_tracks is not None:
                # Validate cached tracks for security
                validated_cached: list[TrackDict] = []
                for track in cached_tracks:
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
                        validated_dict = self.security_validator.validate_track_data(track_dict)
                        # ONLY TrackDict, not dict
                        validated_cached.append(TrackDict(**validated_dict))
                    except SecurityValidationError as e:
                        self.error_logger.warning(
                            "Security validation failed for cached track %s: %s",
                            track.get("id", "unknown"),
                            e,
                        )
                        # Skip this track due to security concerns
                        continue

                self.console_logger.info(
                    "Using cached data for %s, validated %d/%d tracks",
                    artist or "all artists",
                    len(validated_cached),
                    len(cached_tracks),
                )
                return validated_cached

        # Fetch from Music.app
        tracks = await self._fetch_tracks_from_applescript(artist, force_refresh, ignore_test_filter)

        # Cache the results
        if tracks:
            await self.cache_service.set_async(cache_key, tracks)
            self.console_logger.info("Cached %d tracks for key: %s", len(tracks), cache_key)

        return tracks

    @Analytics.track_instance_method("track_fetch_batches")
    async def fetch_tracks_in_batches(self, batch_size: int = 1000) -> list[TrackDict]:
        """Fetch all tracks from Music.app in batches to avoid timeout.

        Args:
            batch_size: Number of tracks to fetch per batch

        Returns:
            List of all track dictionaries

        """
        all_tracks: list[TrackDict] = []
        offset = 1  # AppleScript indices start from 1
        batch_count = 0

        self.console_logger.info("Starting batch processing with batch_size=%d", batch_size)

        while True:
            batch_count += 1
            self.console_logger.info("Fetching batch %d (offset=%d, limit=%d)...", batch_count, offset, batch_size)

            try:
                # Call AppleScript with batch parameters
                args = ["", str(offset), str(batch_size)]  # empty artist, offset, limit

                raw_output = await self.ap_client.run_script(
                    "fetch_tracks.scpt",
                    args,
                    timeout=300,  # 5 minutes per batch should be enough for 1000 tracks
                )

                if not raw_output:
                    self.console_logger.info("Batch %d returned empty result, assuming end of tracks", batch_count)
                    break

                # Parse the batch
                batch_tracks = parse_tracks(raw_output, self.error_logger)

                if not batch_tracks:
                    self.console_logger.info("Batch %d contained no valid tracks, assuming end", batch_count)
                    break

                # Validate each track for security
                validated_tracks: list[TrackDict] = []
                for track in batch_tracks:
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
                        validated_dict = self.security_validator.validate_track_data(track_dict)
                        validated_track = TrackDict(**validated_dict)
                        validated_tracks.append(validated_track)
                    except SecurityValidationError as e:
                        self.error_logger.warning(
                            "Security validation failed for track %s: %s",
                            track.get("id", "unknown"),
                            e,
                        )
                        continue

                all_tracks.extend(validated_tracks)

                self.console_logger.info(
                    "Batch %d: fetched %d tracks, validated %d/%d, total so far: %d",
                    batch_count,
                    len(batch_tracks),
                    len(validated_tracks),
                    len(batch_tracks),
                    len(all_tracks),
                )

                # Move to the next batch
                offset += batch_size

                # Safety check - only stop if we got 0 tracks (actual end of library)
                # Note: AppleScript may return fewer tracks due to filtering, not end of library
                if len(batch_tracks) == 0:
                    self.console_logger.info("Batch %d returned 0 tracks, reached actual end of library", batch_count)
                    break
                if len(batch_tracks) < batch_size:
                    self.console_logger.info(
                        "Batch %d returned %d < %d tracks (some tracks filtered by AppleScript), continuing...",
                        batch_count,
                        len(batch_tracks),
                        batch_size,
                    )

            except (OSError, ValueError, RuntimeError) as e:
                self.error_logger.exception("Error in batch %d (offset=%d): %s", batch_count, offset, e)
                break

        self.console_logger.info("Batch processing completed: %d batches processed, %d total tracks fetched", batch_count, len(all_tracks))

        return all_tracks

    async def _update_property(
        self,
        track_id: str,
        property_name: str,
        property_value: str | int,
        artist: str | None = None,
        album: str | None = None,
        track_name: str | None = None,
    ) -> tuple[bool, bool]:
        """Update a single property of a track via AppleScript.

        Args:
            track_id: ID of the track to update
            property_name: Name of the property to update
            property_value: New value for the property
            artist: Artist name for contextual logging (optional)
            album: Album name for contextual logging (optional)
            track_name: Track name for contextual logging (optional)

        Returns:
            Tuple of (success, changed) where:
            - success: True if operation completed successfully
            - changed: True if actual change was made to track metadata

        """
        try:
            # Convert value to string for AppleScript
            value_str = str(property_value)

            # Execute update with contextual information
            result = await self.ap_client.run_script(
                "update_property.applescript",
                [track_id, property_name, value_str],
                timeout=30,
                context_artist=artist,
                context_album=album,
                context_track=track_name,
            )

            # Check result - distinguish between actual changes and no-ops
            if result:
                if "Success" in result:
                    self.console_logger.debug(
                        "Updated %s for track %s: %s",
                        property_name,
                        track_id,
                        result,
                    )
                    return True, True  # Success and actual change made
                if "No Change" in result:
                    self.console_logger.debug(
                        "No change needed for %s track %s: %s",
                        property_name,
                        track_id,
                        result,
                    )
                    return True, False  # Success but no change needed
                self.error_logger.warning(
                    "Failed to update %s for track %s: %s",
                    property_name,
                    track_id,
                    result,
                )
            else:
                self.error_logger.warning(
                    "No response when updating %s for track %s",
                    property_name,
                    track_id,
                )
        except (OSError, ValueError, RuntimeError):
            self.error_logger.exception(
                "Error updating property %s for track %s",
                property_name,
                track_id,
            )
        return False, False  # Failed operation

    def _validate_and_sanitize_update_parameters(
        self,
        track_id: str,
        new_track_name: str | None,
        new_album_name: str | None,
        new_genre: str | None,
        new_year: str | None,
    ) -> tuple[str, str | None, str | None, str | None, str | None] | None:
        """Validate and sanitize all update parameters.

        Args:
            track_id: ID of the track to update
            new_track_name: New track name (optional)
            new_album_name: New album name (optional)
            new_genre: New genre (optional)
            new_year: New year (optional)

        Returns:
            Tuple of sanitized parameters or None if validation fails

        """
        try:
            # Validate track ID
            sanitized_track_id = self.security_validator.sanitize_string(track_id, "track_id")

            # Validate optional parameters if provided
            sanitized_track_name = None
            if new_track_name is not None:
                sanitized_track_name = self.security_validator.sanitize_string(new_track_name, "track_name")

            sanitized_album_name = None
            if new_album_name is not None:
                sanitized_album_name = self.security_validator.sanitize_string(new_album_name, "album_name")

            sanitized_genre = None
            if new_genre is not None:
                sanitized_genre = self.security_validator.sanitize_string(new_genre, "genre")

            sanitized_year = None
            if new_year is not None:
                sanitized_year = self.security_validator.sanitize_string(new_year, "year")

        except SecurityValidationError:
            self.error_logger.exception("Security validation failed for track update %s", track_id)
            return None

        return (
            sanitized_track_id,
            sanitized_track_name,
            sanitized_album_name,
            sanitized_genre,
            sanitized_year,
        )

    def _handle_dry_run_update(
        self,
        sanitized_track_id: str,
        sanitized_track_name: str | None,
        sanitized_album_name: str | None,
        sanitized_genre: str | None,
        sanitized_year: str | None,
    ) -> bool:
        """Handle dry run update recording.

        Args:
            sanitized_track_id: Sanitized track ID
            sanitized_track_name: Sanitized track name (optional)
            sanitized_album_name: Sanitized album name (optional)
            sanitized_genre: Sanitized genre (optional)
            sanitized_year: Sanitized year (optional)

        Returns:
            True (dry run always succeeds)

        """
        # Record dry-run action with sanitized values
        updates: dict[str, str] = {}
        if sanitized_track_name:
            updates["name"] = sanitized_track_name
        if sanitized_album_name:
            updates["album"] = sanitized_album_name
        if sanitized_genre:
            updates["genre"] = sanitized_genre
        if sanitized_year:
            updates["year"] = sanitized_year

        action: dict[str, Any] = {
            "action": "update_track",
            "track_id": sanitized_track_id,
            "updates": updates,
        }

        self._dry_run_actions.append(action)
        self.console_logger.info("DRY RUN: Would update track %s", sanitized_track_id)
        return True

    async def _update_single_property(
        self,
        sanitized_track_id: str,
        property_name: str,
        property_value: str,
        original_artist: str | None = None,
        original_album: str | None = None,
        original_track: str | None = None,
    ) -> bool:
        """Update a single property with logging.

        Args:
            sanitized_track_id: Sanitized track ID
            property_name: Name of the property to update
            property_value: Value to set for the property
            original_artist: Original artist name for contextual logging (optional)
            original_album: Original album name for contextual logging (optional)
            original_track: Original track name for contextual logging (optional)

        Returns:
            True if successful, False otherwise

        """
        success, changed = await self._update_property(
            sanitized_track_id, property_name, property_value, original_artist, original_album, original_track
        )
        if success:
            if changed:
                # Only log when actual change was made
                self.console_logger.info(
                    "✅ Updated %s for %s to %s",
                    property_name,
                    sanitized_track_id,
                    property_value,
                )
            else:
                # No change needed - log at debug level
                self.console_logger.debug(
                    "No change needed: %s for %s already set to %s",
                    property_name,
                    sanitized_track_id,
                    property_value,
                )
        else:
            self.console_logger.warning("❌ Failed to update %s for %s", property_name, sanitized_track_id)
        return success

    async def _perform_property_updates(
        self,
        sanitized_track_id: str,
        sanitized_track_name: str | None,
        sanitized_album_name: str | None,
        sanitized_genre: str | None,
        sanitized_year: str | None,
        original_artist: str | None = None,
        original_album: str | None = None,
        original_track: str | None = None,
    ) -> bool:
        """Perform all property updates.

        Args:
            sanitized_track_id: Sanitized track ID
            sanitized_track_name: Sanitized track name (optional)
            sanitized_album_name: Sanitized album name (optional)
            sanitized_genre: Sanitized genre (optional)
            sanitized_year: Sanitized year (optional)
            original_artist: Original artist name for contextual logging (optional)
            original_album: Original album name for contextual logging (optional)
            original_track: Original track name for contextual logging (optional)

        Returns:
            True if all updates are successful, False if any failed

        """
        # Create property updates configuration
        updates: list[tuple[str, str]] = []
        if sanitized_track_name is not None:
            updates.append(("name", sanitized_track_name))
        if sanitized_album_name is not None:
            updates.append(("album", sanitized_album_name))
        if sanitized_genre is not None:
            updates.append(("genre", sanitized_genre))
        if sanitized_year is not None:
            updates.append(("year", sanitized_year))

        # Perform all updates
        all_success = True
        for property_name, property_value in updates:
            success = await self._update_single_property(
                sanitized_track_id, property_name, property_value, original_artist, original_album, original_track
            )
            all_success = all_success and success

        return all_success

    @Analytics.track_instance_method("track_update")
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
        # Check if the track is prerelease (read-only) - prevent update attempts
        if track_status and track_status.lower() == "prerelease":
            self.console_logger.info("Skipping update for prerelease track %s (read-only status)", track_id)
            return False

        # Validate and sanitize all input parameters
        validated_params = self._validate_and_sanitize_update_parameters(track_id, new_track_name, new_album_name, new_genre, new_year)
        if validated_params is None:
            return False

        (
            sanitized_track_id,
            sanitized_track_name,
            sanitized_album_name,
            sanitized_genre,
            sanitized_year,
        ) = validated_params

        # Handle dry run mode
        if self.dry_run:
            return self._handle_dry_run_update(
                sanitized_track_id,
                sanitized_track_name,
                sanitized_album_name,
                sanitized_genre,
                sanitized_year,
            )

        # Perform actual updates
        return await self._perform_property_updates(
            sanitized_track_id,
            sanitized_track_name,
            sanitized_album_name,
            sanitized_genre,
            sanitized_year,
            original_artist,
            original_album,
            original_track,
        )

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get the list of dry-run actions recorded.

        Returns:
            List of dry-run action dictionaries

        """
        return self._dry_run_actions
