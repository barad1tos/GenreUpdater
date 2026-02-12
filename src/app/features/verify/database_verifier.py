"""Database verification functionality for Music Genre Updater.

This module handles verifying the track database against Music.app
and managing incremental run timestamps.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.logger import LogFormat, get_full_log_path

from core.run_tracking import IncrementalRunTracker
from metrics.change_reports import load_track_list, save_to_csv

# Constants
LAST_VERIFY_SUFFIX = "_last_verify.txt"

if TYPE_CHECKING:
    from core.models.types import TrackDict
    import logging
    from core.models.protocols import AppleScriptClientProtocol
    from metrics import Analytics


# noinspection PyTypeChecker
class DatabaseVerifier:
    """Manages database verification and incremental run tracking."""

    def __init__(
        self,
        ap_client: AppleScriptClientProtocol,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        db_verify_logger: logging.Logger,
        *,
        analytics: Analytics,
        config: dict[str, Any],
        dry_run: bool = False,
    ) -> None:
        """Initialize the DatabaseVerifier.

        Args:
            ap_client: AppleScript client for Music.app communication
            console_logger: Logger for console output
            error_logger: Logger for error messages
            db_verify_logger: Logger for verification log file
            analytics: Analytics instance for tracking
            config: Configuration dictionary
            dry_run: Whether to run in dry-run mode

        """
        self.ap_client = ap_client
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.db_verify_logger = db_verify_logger
        self.analytics = analytics
        self.config = config
        self.dry_run = dry_run
        self._dry_run_actions: list[dict[str, Any]] = []
        self._verify_start_time: float = 0.0

    # -------------------------------------------------------------------------
    # Compact Logging Methods (db_verify_logger + console with IDE-like highlighting)
    # -------------------------------------------------------------------------

    def _log_verify_start(self, track_count: int) -> None:
        """Log verification start with IDE-like highlighting."""
        self._verify_start_time = time.time()
        # File log (plain)
        self.db_verify_logger.info("VERIFY START | tracks=%d", track_count)
        # Console log (highlighted)
        self.console_logger.info(
            "%s %s | tracks: %s",
            LogFormat.label("VERIFY"),
            LogFormat.success("START"),
            LogFormat.number(track_count),
        )

    def _log_verify_complete(self, total: int, invalid: int, removed: int) -> None:
        """Log verification completion with IDE-like highlighting."""
        duration = time.time() - self._verify_start_time
        # File log (plain)
        self.db_verify_logger.info(
            "VERIFY DONE | total=%d invalid=%d removed=%d duration=%.1fs",
            total,
            invalid,
            removed,
            duration,
        )
        # Console log (highlighted)
        if invalid == 0:
            self.console_logger.info(
                "%s %s | %s tracks verified %s",
                LogFormat.label("VERIFY"),
                LogFormat.success("DONE"),
                LogFormat.number(total),
                LogFormat.duration(duration),
            )
        else:
            self.console_logger.info(
                "%s %s | %s invalid of %s removed %s",
                LogFormat.label("VERIFY"),
                LogFormat.warning("DONE"),
                LogFormat.error(str(invalid)),
                LogFormat.number(total),
                LogFormat.duration(duration),
            )

    # -------------------------------------------------------------------------
    # Auto-Verify Check (for startup integration)
    # -------------------------------------------------------------------------

    async def should_auto_verify(self) -> bool:
        """Check if automatic database verification should run.

        Returns True if:
        - auto_verify_days has passed since last verification
        - No previous verification exists

        Returns:
            True if auto-verify should run, False otherwise

        """
        verify_config = self.config.get("database_verification", {})
        auto_verify_days = verify_config.get("auto_verify_days", 7)

        if auto_verify_days <= 0:
            return False

        csv_path = get_full_log_path(
            self.config,
            "csv_output_file",
            "csv/track_list.csv",
        )
        last_verify_file = csv_path.replace(".csv", LAST_VERIFY_SUFFIX)
        last_verify_path = Path(last_verify_file)

        if not last_verify_path.exists():
            self.console_logger.debug("No previous verification found, auto-verify needed")
            return True

        try:
            loop = asyncio.get_running_loop()

            def _read_last_verify() -> str:
                with last_verify_path.open(encoding="utf-8") as f:
                    return f.read().strip()

            last_verify_str = await loop.run_in_executor(None, _read_last_verify)
            last_verify = datetime.fromisoformat(last_verify_str)

            if last_verify.tzinfo is None:
                last_verify = last_verify.replace(tzinfo=UTC)

            days_since = (datetime.now(tz=UTC) - last_verify).days

            if days_since >= auto_verify_days:
                self.console_logger.info(
                    "%s needed: %s days since last check %s",
                    LogFormat.label("AUTO-VERIFY"),
                    LogFormat.number(days_since),
                    LogFormat.dim(f"(threshold: {auto_verify_days})"),
                )
                return True

            self.console_logger.debug(
                "Auto-verify not needed: %d days since last check (threshold: %d)",
                days_since,
                auto_verify_days,
            )
            return False

        except (OSError, ValueError, RuntimeError) as e:
            self.error_logger.warning("Error checking auto-verify status: %s", e)
            return True  # Run verification if we can't determine last run

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
            loop = asyncio.get_running_loop()

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

    async def _verify_tracks_bulk(self, tracks_to_verify: list[TrackDict]) -> list[str]:
        """Verify tracks using bulk ID comparison (~2 min vs ~10+ hours).

        Instead of checking each track individually via AppleScript (O(n) calls),
        this method fetches all track IDs from Music.app in one call and uses
        set difference to find tracks that no longer exist.

        Args:
            tracks_to_verify: List of tracks to verify

        Returns:
            List of track IDs that are no longer in Music.app

        """
        csv_ids: set[str] = set()
        for t in tracks_to_verify:
            track_id = t.get("id")
            if track_id is not None:
                csv_ids.add(str(track_id))
        if not csv_ids:
            return []

        # Fetch all track IDs from Music.app (AppleScriptClient handles logging/errors)
        try:
            track_ids = await self.ap_client.fetch_all_track_ids()
            music_ids = set(track_ids)
        except OSError as e:
            # Safety: don't delete anything if fetch failed
            self.error_logger.warning("Skipping verification - fetch failed: %s", e)
            return []

        if not music_ids:
            self.error_logger.warning("Skipping verification - no track IDs returned from Music.app")
            return []

        invalid_ids = csv_ids - music_ids

        if invalid_ids:
            self.console_logger.info(
                "%s %d tracks no longer in Music.app",
                LogFormat.warning("VERIFY"),
                len(invalid_ids),
            )
        else:
            self.console_logger.info(
                "%s All %d tracks valid",
                LogFormat.success("VERIFY"),
                len(csv_ids),
            )

        return sorted(invalid_ids)

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

        last_verify_file = csv_path.replace(".csv", LAST_VERIFY_SUFFIX)
        last_verify_path = Path(last_verify_file)

        if not last_verify_path.exists():
            return False

        try:
            # Read last verification time using async file operation
            loop = asyncio.get_running_loop()

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
        if apply_test_filter and self.dry_run and (test_artists := set(self.config.get("development", {}).get("test_artists", []))):
            tracks: list[TrackDict] = [t for t in existing_tracks if t.get("artist") in test_artists]
            self.console_logger.info(
                "DRY RUN: Filtering to %d tracks from test artists",
                len(tracks),
            )
            return tracks
        return existing_tracks

    def _handle_invalid_tracks(self, invalid_tracks: list[str], existing_tracks: list[TrackDict], csv_path: str) -> None:
        """Handle removal or logging of invalid tracks.

        Args:
            invalid_tracks: List of invalid track IDs
            existing_tracks: All existing tracks in the database
            csv_path: The path to CSV file

        """
        # Log to file for audit trail
        self.db_verify_logger.info(
            "INVALID_TRACKS | count=%d ids=%s",
            len(invalid_tracks),
            ",".join(invalid_tracks[:10]) + ("..." if len(invalid_tracks) > 10 else ""),
        )

        if not invalid_tracks:
            return

        if not self.dry_run:
            # Filter out invalid tracks
            valid_tracks: list[TrackDict] = [t for t in existing_tracks if t.get("id") not in invalid_tracks]

            # Save updated database
            save_to_csv(
                valid_tracks,
                csv_path,
                error_logger=self.error_logger,
            )
        else:
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

        last_verify_file = csv_path.replace(".csv", LAST_VERIFY_SUFFIX)
        last_verify_path = Path(last_verify_file)

        try:
            # Write last verification time using async file operation
            loop = asyncio.get_running_loop()

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
        verify_config = self.config.get("database_verification", {})
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

        # Log verification start
        self._log_verify_start(len(tracks_to_verify))

        # Verify tracks using bulk ID comparison (single AppleScript call)
        invalid_tracks = await self._verify_tracks_bulk(tracks_to_verify)

        # Handle invalid tracks (removal or dry-run logging)
        self._handle_invalid_tracks(invalid_tracks, existing_tracks, csv_path)

        # Update last verification timestamp
        await self._update_verification_timestamp(csv_path)

        # Log verification complete
        removed_count = 0 if self.dry_run else len(invalid_tracks)
        self._log_verify_complete(len(tracks_to_verify), len(invalid_tracks), removed_count)

        return len(invalid_tracks)

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get the list of dry-run actions recorded.

        Returns:
            List of dry-run action dictionaries

        """
        return self._dry_run_actions
