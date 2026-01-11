"""Track update execution module.

This module handles track update operations including:
- Single and batch property updates
- Dry run recording
- Security validation
- Cache invalidation after updates
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.models.track_status import can_edit_metadata
from core.models.track_models import TrackDict
from core.models.validators import SecurityValidationError, SecurityValidator
from metrics import Analytics

if TYPE_CHECKING:
    import logging

    from core.models.protocols import AppleScriptClientProtocol, CacheServiceProtocol


class TrackUpdateExecutor:
    """Executes track metadata updates via AppleScript.

    This class handles:
    - Individual and batch property updates
    - Security validation of parameters
    - Dry run mode recording
    - Cache invalidation after successful updates
    """

    def __init__(
        self,
        ap_client: AppleScriptClientProtocol,
        cache_service: CacheServiceProtocol,
        security_validator: SecurityValidator,
        config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        *,
        dry_run: bool = False,
    ) -> None:
        """Initialize the update executor.

        Args:
            ap_client: AppleScript client for executing updates
            cache_service: Cache service for invalidation
            security_validator: Validator for sanitizing inputs
            config: Configuration dictionary
            console_logger: Logger for info/debug messages
            error_logger: Logger for error messages
            dry_run: If True, record actions without executing
        """
        self.ap_client = ap_client
        self.cache_service = cache_service
        self.security_validator = security_validator
        self.config = config
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.dry_run = dry_run
        self._dry_run_actions: list[dict[str, Any]] = []

    def set_dry_run(self, dry_run: bool) -> None:
        """Update dry run mode.

        Args:
            dry_run: New dry run state
        """
        self.dry_run = dry_run
        if not dry_run:
            self._dry_run_actions.clear()

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get the list of dry-run actions recorded.

        Returns:
            List of dry-run action dictionaries
        """
        return self._dry_run_actions

    def _is_read_only_track(self, track_status: str | None, track_id: str | None = None) -> bool:
        """Return True when metadata cannot be edited for the given track status."""
        if can_edit_metadata(track_status):
            return False

        self.console_logger.info(
            "Skipping update for read-only track %s (status: %s)",
            track_id or "<unknown>",
            track_status or "unknown",
        )
        return True

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
            return self._process_update_result(result, property_name, track_id)
        except (OSError, ValueError, RuntimeError, KeyError):
            self.error_logger.exception(
                "Error updating property %s for track %s",
                property_name,
                track_id,
            )
        return False, False  # Failed operation

    def _process_update_result(self, result: str | None, property_name: str, track_id: str) -> tuple[bool, bool]:
        """Process the result of an AppleScript update operation.

        Args:
            result: Result string from AppleScript execution
            property_name: Name of the property that was updated
            track_id: ID of the track that was updated

        Returns:
            Tuple of (success, changed) where:
            - success: True if operation completed successfully
            - changed: True if actual change was made to track metadata
        """
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
        *,
        sanitized_artist_name: str | None = None,
    ) -> bool:
        """Handle dry run update recording.

        Args:
            sanitized_track_id: Sanitized track ID
            sanitized_track_name: Sanitized track name (optional)
            sanitized_album_name: Sanitized album name (optional)
            sanitized_genre: Sanitized genre (optional)
            sanitized_year: Sanitized year (optional)
            sanitized_artist_name: Sanitized artist name (optional)

        Returns:
            True (dry run always succeeds)

        """
        # Record dry-run action with sanitized values
        updates: dict[str, str] = {}
        if sanitized_artist_name:
            updates["artist"] = sanitized_artist_name
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
                # Only log when actual change was made - prefer artist/track names over ID
                if original_artist:
                    entity_label = f"'{original_artist}'"
                    if original_track:
                        entity_label += f" - '{original_track}'"
                else:
                    entity_label = sanitized_track_id
                self.console_logger.info(
                    "\u2705 Updated %s for %s to %s",
                    property_name,
                    entity_label,
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
        elif original_artist and original_track:
            self.console_logger.warning(
                "❌ Failed to update %s for '%s' - '%s' (check error log for details)",
                property_name,
                original_artist,
                original_track,
            )
        else:
            self.console_logger.warning(
                "❌ Failed to update %s for %s (check error log for details)",
                property_name,
                sanitized_track_id,
            )
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

        return await self._apply_track_updates(
            sanitized_track_id,
            updates,
            original_artist,
            original_album,
            original_track,
        )

    @staticmethod
    def _resolve_updated_artist(
        updates: list[tuple[str, Any]],
        original_artist: str | None,
    ) -> str | None:
        """Determine the latest artist value after updates."""
        for property_name, property_value in updates:
            if property_name == "artist":
                return str(property_value)
        return original_artist

    async def _notify_track_cache_invalidation(
        self,
        track_id: str,
        artist: str | None,
        album: str | None,
        track_name: str | None,
        original_artist: str | None = None,
    ) -> None:
        """Notify cache services that a track metadata change occurred."""
        primary_artist = (artist or original_artist or "").strip()
        payload = TrackDict(
            id=track_id,
            name=(track_name or "").strip(),
            artist=primary_artist,
            album=(album or "").strip(),
            genre=None,
            year=None,
            date_added=None,
        )

        if original_artist:
            payload.original_artist = original_artist.strip()

        try:
            await self.cache_service.invalidate_for_track(payload)
        except Exception as exc:
            self.error_logger.warning("Failed to invalidate cache for track %s: %s", track_id, exc)

    async def _try_batch_update(
        self,
        track_id: str,
        updates: list[tuple[str, Any]],
        artist: str | None = None,
        album: str | None = None,
        track: str | None = None,
    ) -> bool:
        """Attempt batch update using batch_update_tracks.applescript.

        This is experimental and may fail. Caller should handle exceptions
        and fall back to individual updates.

        Args:
            track_id: Sanitized track ID
            updates: List of (property_name, value) tuples
            artist: Artist name for logging
            album: Album name for logging
            track: Track name for logging

        Returns:
            True if batch update succeeded

        Raises:
            Exception: If batch update fails for any reason
        """
        # Build batch command string: "trackID:property:value;trackID:property:value"
        commands = []
        for property_name, property_value in updates:
            value_str = str(property_value)
            commands.append(f"{track_id}:{property_name}:{value_str}")

        batch_command = ";".join(commands)

        # Determine timeout from configuration with sensible fallbacks
        timeouts_config = self.config.get("applescript_timeouts", {}) if isinstance(self.config, dict) else {}
        timeout_value = timeouts_config.get("batch_update")
        if timeout_value is None:
            timeout_value = self.config.get("applescript_timeout_seconds", 3600)
        try:
            batch_timeout = float(timeout_value)
        except (TypeError, ValueError):
            self.console_logger.warning(
                "Invalid 'applescript_timeouts.batch_update' value '%s'; falling back to 60.0 seconds",
                timeout_value,
            )
            batch_timeout = 60.0
        if batch_timeout <= 0:
            self.console_logger.error(
                "Non-positive 'applescript_timeouts.batch_update' value '%s'; this is a misconfiguration.",
                timeout_value,
            )
            msg = f"Non-positive 'applescript_timeouts.batch_update' value '{timeout_value}'; please check your configuration."
            raise ValueError(msg)

        # Execute batch update with configured timeout (defaults to 60s)
        result = await self.ap_client.run_script(
            "batch_update_tracks.applescript",
            [batch_command],
            timeout=batch_timeout,
            context_artist=artist,
            context_album=album,
            context_track=track,
        )

        # Check if batch operation succeeded
        if result and "Success" in result:
            self.console_logger.debug("\u2705 Batch updated %d properties for track %s", len(updates), track_id)
            return True

        error_msg = f"Batch update script returned: {result}"
        raise RuntimeError(error_msg)

    async def _apply_track_updates(
        self,
        track_id: str,
        updates: list[tuple[str, Any]],
        original_artist: str | None = None,
        album: str | None = None,
        track: str | None = None,
    ) -> bool:
        """Apply multiple property updates to a track with batch fallback.

        First attempts batch update for efficiency. If batch fails or is disabled,
        falls back to individual property updates to maintain reliability.

        Args:
            track_id: Sanitized track ID
            updates: List of (property_name, value) tuples to apply
            original_artist: Artist name for contextual logging
            album: Album name for contextual logging
            track: Track name for contextual logging

        Returns:
            True if all updates succeeded, False otherwise
        """
        if not updates:
            return True

        # Check if batch updates are enabled (default: disabled for safety)
        batch_enabled = self.config.get("experimental", {}).get("batch_updates_enabled", False)
        max_batch_size = self.config.get("experimental", {}).get("max_batch_size", 5)

        # Only try batch for multiple updates and if explicitly enabled
        updates_count = len(updates)
        if batch_enabled and 1 < updates_count <= max_batch_size:
            try:
                batch_success = await self._try_batch_update(track_id, updates, original_artist, album, track)
                if batch_success:
                    primary_artist = self._resolve_updated_artist(updates, original_artist)
                    await self._notify_track_cache_invalidation(track_id, primary_artist, album, track, original_artist)
                return batch_success
            except Exception as e:
                self.console_logger.warning("Batch update failed for track %s, falling back to individual updates: %s", track_id, str(e))
                # Fall through to individual updates

        # Individual updates (current reliable method)
        all_success = True
        any_success = False
        for property_name, property_value in updates:
            success = await self._update_single_property(track_id, property_name, property_value, original_artist, album, track)
            any_success = any_success or success
            all_success = all_success and success

        if any_success:
            primary_artist = self._resolve_updated_artist(updates, original_artist)
            await self._notify_track_cache_invalidation(track_id, primary_artist, album, track, original_artist)

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
        if self._is_read_only_track(track_status, track_id):
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

    def _prepare_artist_update(
        self,
        track: TrackDict,
        new_artist_name: str,
    ) -> tuple[str, str, str] | None:
        """Prepare and validate artist update parameters.

        Args:
            track: Track dictionary representing the target track
            new_artist_name: Artist name to apply

        Returns:
            Tuple of (sanitized_track_id, sanitized_artist, current_artist) or None if validation fails
        """
        track_id = track.id
        if not track_id:
            self.error_logger.warning("Cannot update artist for track without ID: %s", track)
            return None

        current_artist = (track.artist or "").strip()
        target_artist = (new_artist_name or "").strip()

        if not target_artist:
            self.error_logger.warning("New artist name is empty for track %s", track_id)
            return None

        if target_artist == current_artist:
            self.console_logger.debug("Artist name already up to date for track %s: %s", track_id, target_artist)
            return None

        if self._is_read_only_track(track.track_status, track_id):
            return None

        try:
            sanitized_track_id = self.security_validator.sanitize_string(track_id, "track_id")
            sanitized_artist = self.security_validator.sanitize_string(target_artist, "artist")
        except SecurityValidationError as exc:
            self.error_logger.warning(
                "Security validation failed when renaming artist for track %s: %s",
                track_id,
                exc,
            )
            return None

        return sanitized_track_id, sanitized_artist, current_artist

    @Analytics.track_instance_method("track_artist_update")
    async def update_artist_async(
        self,
        track: TrackDict,
        new_artist_name: str,
        *,
        original_artist: str | None = None,
        update_album_artist: bool = False,
    ) -> bool:
        """Update the artist name for a track.

        Args:
            track: Track dictionary representing the target track
            new_artist_name: Artist name to apply
            original_artist: Original artist for logging context (optional)
            update_album_artist: If True, also update album_artist field when it matches
                                 the old or new artist name from configuration mapping

        Returns:
            True if update succeeded, False otherwise
        """
        prepared = self._prepare_artist_update(track, new_artist_name)
        if prepared is None:
            return False

        sanitized_track_id, sanitized_artist, current_artist = prepared

        if self.dry_run:
            return self._handle_dry_run_update(
                sanitized_track_id,
                None,
                None,
                None,
                None,
                sanitized_artist_name=sanitized_artist,
            )

        # Prepare updates list: always update artist
        updates: list[tuple[str, str]] = [("artist", sanitized_artist)]

        # Update album_artist only if explicitly requested (e.g., from artist rename configuration)
        # This ensures we only sync album_artist when both fields are from the same mapping
        if update_album_artist:
            album_artist = getattr(track, "album_artist", None)
            if isinstance(album_artist, str):
                normalized_album_artist = album_artist.strip()
                # Check if album_artist matches either old or new name from configuration
                if normalized_album_artist in (current_artist, sanitized_artist):
                    updates.append(("album_artist", sanitized_artist))

        success = await self._apply_track_updates(
            sanitized_track_id,
            updates,
            original_artist=original_artist or current_artist,
            album=track.album,
            track=track.name,
        )

        if success:
            track.artist = sanitized_artist
            # Update album_artist in TrackDict if it was updated in Music.app
            if len(updates) > 1:
                track.album_artist = sanitized_artist

        return success
