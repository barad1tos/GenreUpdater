"""Run tracking utilities for incremental processing.

Provides shared functionality for tracking last run timestamps
and determining if incremental processing should occur.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.logger import get_full_log_path


# noinspection PyTypeChecker
class IncrementalRunTracker:
    """Utility class for tracking incremental run timestamps."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the run tracker.

        Args:
            config: Application configuration

        """
        self.config = config

    def get_last_run_file_path(self) -> str:
        """Get the path to the last run file.

        Returns:
            Path to the last incremental run log file

        """
        return get_full_log_path(
            self.config,
            "last_incremental_run_file",
            "last_incremental_run.log",
        )

    async def update_last_run_timestamp(self) -> None:
        """Update the timestamp of the last incremental run."""
        last_run_file = self.get_last_run_file_path()
        last_run_path = Path(last_run_file)

        try:
            # Ensure directory exists
            last_run_path.parent.mkdir(parents=True, exist_ok=True)

            # Write current timestamp asynchronously
            def _write_timestamp() -> None:
                with last_run_path.open("w", encoding="utf-8") as f:
                    f.write(datetime.now(UTC).isoformat())

            await asyncio.get_running_loop().run_in_executor(None, _write_timestamp)

        except OSError as e:
            # Log error but don't fail the operation
            logger = logging.getLogger(__name__)
            logger.warning("Failed to update last run timestamp for file '%s': %s", last_run_path, e)

    async def get_last_run_timestamp(self) -> datetime | None:
        """Get the timestamp of the last incremental run.

        Returns:
            Last run timestamp or None if no previous run found

        """
        last_run_file = self.get_last_run_file_path()
        last_run_path = Path(last_run_file)

        try:
            if not last_run_path.exists():
                return None

            # Read the last run time using an async file operation
            def _read_timestamp() -> str:
                with last_run_path.open(encoding="utf-8") as f:
                    return f.read().strip()

            timestamp_str = await asyncio.get_running_loop().run_in_executor(None, _read_timestamp)
            dt = datetime.fromisoformat(timestamp_str)
            if dt.tzinfo is None:
                # Normalize naive datetime to UTC for backward compatibility with legacy files
                dt = dt.replace(tzinfo=UTC)
            return dt

        except (OSError, ValueError) as e:
            # Log error but return None to indicate no valid timestamp
            logger = logging.getLogger(__name__)
            logger.warning("Failed to read last run timestamp from file '%s': %s", last_run_path, e)
            return None
