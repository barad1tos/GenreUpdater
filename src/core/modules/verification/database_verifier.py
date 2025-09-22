"""Database verification functionality for Music Genre Updater.

This module handles verifying the track database against Music.app
and managing incremental run timestamps.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.utils.core.logger import get_full_log_path
from src.utils.core.run_tracking import IncrementalRunTracker
from src.utils.data.types import TrackDict
from src.utils.monitoring.reports import load_track_list, save_to_csv

if TYPE_CHECKING:
    from src.utils.data.protocols import AppleScriptClientProtocol
    from src.utils.monitoring import Analytics


# noinspection PyTypeChecker
class DatabaseVerifier:
    """Manages database verification and incremental run tracking."""

    def __init__(
        self,
        ap_client: "AppleScriptClientProtocol",
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        *,
        analytics: "Analytics",
        config: dict[str, Any],
        dry_run: bool = False,
    ) -> None:
        """Initialize the DatabaseVerifier.

        Args:
            ap_client: AppleScript client for Music.app communication
            console_logger: Logger for console output
            error_logger: Logger for error messages
            analytics: Analytics instance for tracking
            config: Configuration dictionary
            dry_run: Whether to run in dry-run mode

        """
        self.ap_client = ap_client
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.analytics = analytics
        self.config = config
        self.dry_run = dry_run
        self._dry_run_actions: list[dict[str, Any]] = []

    async def can_run_incremental(self, force_run: bool = False) -> bool:
        """Check if enough time has passed since the last incremental run.

        Args:
            force_run: If True, skip the time check

        Returns:
            True if incremental run should proceed, False otherwise

        """
        if force_run:
            self.console_logger.info("Force run requested, skipping interval check")
            return True

        # Get configuration values
        interval_minutes = self.config.get("incremental_interval_minutes", 1440)
        last_run_file = get_full_log_path(
            self.config,
            "last_incremental_run_file",
            "last_incremental_run.log",
        )

        # Check if the last run file exists
        last_run_path = Path(last_run_file)
        if not last_run_path.exists():
            self.console_logger.info(
                "No previous incremental run found, proceeding with run",
            )
            return True

        try:
            # Read last run time using async file operation
            loop = asyncio.get_event_loop()

            def _read_file() -> str:
                with last_run_path.open(encoding="utf-8") as f:
                    return f.read().strip()

            last_run_str = await loop.run_in_executor(None, _read_file)

            # Try multiple datetime formats for compatibility
            try:
                last_run_time = datetime.fromisoformat(last_run_str)
                # Ensure timezone awareness - if naive, assume UTC
                if last_run_time.tzinfo is None:
                    last_run_time = last_run_time.replace(tzinfo=UTC)
            except ValueError:
                # Handle legacy format: YYYY-MM-DD HH:MM:SS
                try:
                    last_run_time = datetime.strptime(last_run_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                except ValueError:
                    # Handle date-only format: YYYY-MM-DD
                    last_run_time = datetime.strptime(last_run_str, "%Y-%m-%d").replace(tzinfo=UTC)

            # Handle future timestamps (corrupted/invalid files)
            now = datetime.now(tz=UTC)
            if last_run_time > now:
                self.console_logger.warning(
                    "Last run timestamp is in the future (%s). Treating as if no previous run exists.",
                    last_run_time.strftime("%Y-%m-%d %H:%M"),
                )
                return True

            # Check if enough time has passed
            time_since_last = now - last_run_time
            required_interval = timedelta(minutes=interval_minutes)

            if time_since_last >= required_interval:
                self.console_logger.info(
                    "Last run: %s. Sufficient time has passed, proceeding.",
                    last_run_time.strftime("%Y-%m-%d %H:%M"),
                )
                return True

            # Not enough time has passed
            remaining = required_interval - time_since_last
            remaining_minutes = int(remaining.total_seconds() / 60)
            self.console_logger.info(
                "Last run: %s. Next run in %d minutes. Skipping.",
                last_run_time.strftime("%Y-%m-%d %H:%M"),
                remaining_minutes,
            )

        except (ValueError, OSError):
            self.error_logger.exception("Error reading last incremental run time")
            # On error, allow the run to proceed
            return True

        # Execution completed successfully - not enough time has passed
        return False

    async def update_last_incremental_run(self) -> None:
        """Update the timestamp of the last incremental run."""
        tracker = IncrementalRunTracker(self.config)
        await tracker.update_last_run_timestamp()

        self.console_logger.info(
            "Updated last incremental run timestamp in %s",
            tracker.get_last_run_file_path(),
        )

    async def _verify_track_exists(self, track_id: str) -> bool:
        """Verify if a track exists in Music.app.

        Args:
            track_id: ID of the track to verify

        Returns:
            True if the track exists, False otherwise

        """
        script = f"""
        tell application "Music"
            try
                # Efficiently check existence by trying to get a property
                # Using 'properties' to get a dictionary is slightly more robust than just 'id'
                get properties of track id {track_id} of library playlist 1
                return "exists"
            on error errMsg number errNum
                if errNum is -1728 then
                    # Error code for "item not found"
                    return "not_found"
                else
                    # Log other errors but treat as potentially existing
                    log "Error verifying track {track_id}: " & errNum & " " & errMsg
                    return "error_assume_exists"
                end if
            end try
        end tell
        """

        try:
            # Use injected ap_client with proper validation
            script_result: Any = await self.ap_client.run_script_code(script)

            # Handle None results
            if script_result is None:
                self.error_logger.warning(
                    "AppleScript verification for track ID %s returned None. Assuming exists.",
                    track_id,
                )
                return True

            # Validate and convert to string for consistent processing
            if not isinstance(script_result, str):
                self.error_logger.warning(
                    "AppleScript returned non-string result: %s. Converting to string.",
                    type(script_result).__name__,
                )

            script_result_str = str(script_result)

            # Process script result
            if script_result_str == "exists":
                return True
            if script_result_str == "not_found":
                self.console_logger.debug("Track ID %s not found in Music.app.", track_id)
                return False
            # Includes "error_assume_exists" case and other unexpected results
            self.error_logger.warning(
                "AppleScript verification for track ID %s returned unexpected result: '%s'. Assuming exists.",
                track_id,
                script_result_str,
            )

        except (ValueError, OSError):
            self.error_logger.exception(
                "Exception during AppleScript execution for track %s",
                track_id,
            )
            return True  # Assume exists on error to prevent accidental deletion

        return True  # Default fallback for unexpected results

    async def _should_skip_verification(self, force: bool, csv_path: str, auto_verify_days: int) -> bool:
        """Check if verification should be skipped based on the last verification date.

        Args:
            force: If True, never skip verification
            csv_path: Path to the CSV file
            auto_verify_days: Number of days threshold for verification

        Returns:
            True if verification should be skipped, False otherwise

        """
        if force:
            return False

        last_verify_file = csv_path.replace(".csv", "_last_verify.txt")
        last_verify_path = Path(last_verify_file)

        if not last_verify_path.exists():
            return False

        try:
            # Read last verification time using async file operation
            loop = asyncio.get_event_loop()

            def _read_last_verify() -> str:
                with last_verify_path.open(encoding="utf-8") as f:
                    return f.read().strip()

            last_verify_str = await loop.run_in_executor(None, _read_last_verify)
            last_verify = datetime.fromisoformat(last_verify_str)

            # Ensure timezone awareness for comparison
            if last_verify.tzinfo is None:
                last_verify = last_verify.replace(tzinfo=UTC)

            days_since_verify = (datetime.now(tz=UTC) - last_verify).days
            if days_since_verify < auto_verify_days:
                self.console_logger.info(
                    "Database verified %d days ago, skipping (threshold: %d days)",
                    days_since_verify,
                    auto_verify_days,
                )
                return True

        except (OSError, ValueError, RuntimeError) as e:
            self.error_logger.warning(
                "Error reading last verification date: %s",
                e,
            )

        return False

    def _get_tracks_to_verify(self, existing_tracks: list[TrackDict], apply_test_filter: bool) -> list[TrackDict]:
        """Get the list of tracks to verify, applying test filter if requested.

        Args:
            existing_tracks: All existing tracks in the database
            apply_test_filter: Whether to apply test artist filter

        Returns:
            List of tracks to verify

        """
        if apply_test_filter and self.dry_run and (test_artists := set(self.config.get("test_artists", []))):
            tracks: list[TrackDict] = [t for t in existing_tracks if t.get("artist") in test_artists]
            self.console_logger.info(
                "DRY RUN: Filtering to %d tracks from test artists",
                len(tracks),
            )
            return tracks
        return existing_tracks

    async def _verify_tracks_in_batches(
        self, tracks_to_verify: list[TrackDict], verify_config: dict[str, Any]
    ) -> list[str]:
        """Verify tracks in batches and return a list of invalid track IDs.

        Args:
            tracks_to_verify: List of tracks to verify
            verify_config: Verification configuration settings

        Returns:
            List of invalid track IDs

        """
        batch_size: int = verify_config.get("batch_size", 20)
        pause_seconds: float = verify_config.get("pause_seconds", 0.2)
        invalid_tracks: list[str] = []

        for i in range(0, len(tracks_to_verify), batch_size):
            batch: list[TrackDict] = tracks_to_verify[i : i + batch_size]

            # Create verification tasks
            tasks: list[tuple[str, Any]] = []
            for track in batch:
                if track_id := str(track.get("id", "")):
                    task = self._verify_track_exists(track_id)
                    tasks.append((track_id, task))

            # Execute batch
            for track_id, task in tasks:
                exists: bool = await task
                if not exists:
                    invalid_tracks.append(track_id)
                    self.console_logger.info(
                        "Found invalid track ID: %s",
                        track_id,
                    )

            # Progress logging
            self.console_logger.debug(
                "Verified batch %d/%d",
                min(i + batch_size, len(tracks_to_verify)),
                len(tracks_to_verify),
            )

            # Pause between batches
            if i + batch_size < len(tracks_to_verify):
                await asyncio.sleep(pause_seconds)

        return invalid_tracks

    def _handle_invalid_tracks(
        self, invalid_tracks: list[str], existing_tracks: list[TrackDict], csv_path: str
    ) -> None:
        """Handle removal or logging of invalid tracks.

        Args:
            invalid_tracks: List of invalid track IDs
            existing_tracks: All existing tracks in the database
            csv_path: The path to CSV file

        """
        if not invalid_tracks:
            self.console_logger.info("All tracks in database are valid")
            return

        self.console_logger.info(
            "Found %d tracks that no longer exist in Music.app",
            len(invalid_tracks),
        )

        if not self.dry_run:
            # Filter out invalid tracks
            valid_tracks: list[TrackDict] = [t for t in existing_tracks if t.get("id") not in invalid_tracks]

            # Save updated database
            save_to_csv(
                valid_tracks,
                csv_path,
                error_logger=self.error_logger,
            )

            self.console_logger.info(
                "Removed %d invalid tracks from database",
                len(invalid_tracks),
            )
        else:
            self.console_logger.info(
                "DRY RUN: Would remove %d invalid tracks",
                len(invalid_tracks),
            )
            self._dry_run_actions.append(
                {
                    "action": "remove_invalid_tracks",
                    "count": len(invalid_tracks),
                    "track_ids": invalid_tracks,
                }
            )

    async def _update_verification_timestamp(self, csv_path: str) -> None:
        """Update the last verification timestamp file.

        Args:
            csv_path: The path to the CSV file (used to derive the timestamp file path)

        """
        if self.dry_run:
            return

        last_verify_file = csv_path.replace(".csv", "_last_verify.txt")
        last_verify_path = Path(last_verify_file)

        try:
            # Write last verification time using async file operation
            loop = asyncio.get_event_loop()

            def _write_last_verify() -> None:
                with last_verify_path.open("w", encoding="utf-8") as f:
                    f.write(datetime.now(tz=UTC).isoformat())

            await loop.run_in_executor(None, _write_last_verify)
        except (OSError, ValueError, RuntimeError) as e:
            self.error_logger.warning(
                "Error updating last verification date: %s",
                e,
            )

    async def verify_and_clean_track_database(
        self,
        force: bool = False,
        apply_test_filter: bool = False,
    ) -> int:
        """Verify the track database against Music.app and remove invalid entries.

        Args:
            force: Force verification even if recently done
            apply_test_filter: Apply test artist filter in dry-run mode

        Returns:
            Number of invalid tracks removed

        """
        # Load configuration and database
        verify_config = self.config.get("verify_database", {})
        auto_verify_days = verify_config.get("auto_verify_days", 7)

        csv_path = get_full_log_path(
            self.config,
            "csv_output_file",
            "csv/track_list.csv",
        )
        track_dict = load_track_list(csv_path)
        existing_tracks = list(track_dict.values())

        if not existing_tracks:
            self.console_logger.info("No existing track database to verify")
            return 0

        # Check if verification should be skipped
        if await self._should_skip_verification(force, csv_path, auto_verify_days):
            return 0

        # Get tracks to verify (with optional test filter)
        tracks_to_verify = self._get_tracks_to_verify(existing_tracks, apply_test_filter)

        self.console_logger.info(
            "Verifying %d tracks in database against Music.app",
            len(tracks_to_verify),
        )

        # Verify tracks in batches
        invalid_tracks = await self._verify_tracks_in_batches(tracks_to_verify, verify_config)

        # Handle invalid tracks (removal or dry-run logging)
        self._handle_invalid_tracks(invalid_tracks, existing_tracks, csv_path)

        # Update last verification timestamp
        await self._update_verification_timestamp(csv_path)

        return len(invalid_tracks)

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get the list of dry-run actions recorded.

        Returns:
            List of dry-run action dictionaries

        """
        return self._dry_run_actions
