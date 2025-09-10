"""Dry Run Module.

This module provides a dry run simulation for cleaning and genre updates.
It defines classes that can be used by the main application to simulate
AppleScript interactions and processing logic without modifying the actual
music library.
"""

from __future__ import annotations

# Standard library imports
import asyncio
from typing import TYPE_CHECKING, Any

from src.utils.data.types import AppleScriptClientProtocol

if TYPE_CHECKING:
    import logging

DRY_RUN_SUCCESS_MESSAGE = "Success (dry run)"


class DryRunAppleScriptClient(AppleScriptClientProtocol):
    """AppleScript client that logs actions instead of modifying the library."""

    def __init__(
        self,
        real_client: AppleScriptClientProtocol,
        config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Initialize the DryRunAppleScriptClient with dependencies.

        Args:
            real_client: The real AppleScript client to delegate fetch operations to
            config: Configuration dictionary
            console_logger: Logger for console output
            error_logger: Logger for error output

        """
        self._real_client = real_client
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.config = config
        self.actions: list[dict[str, Any]] = []
        self.apple_scripts_dir: str = config.get("apple_scripts_dir", "")  # For type checking

    async def initialize(self) -> None:
        """Initialize the DryRunAppleScriptClient."""
        await self._real_client.initialize()

    async def run_script(
        self,
        script_name: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
        context_artist: str | None = None,
        context_album: str | None = None,
        context_track: str | None = None,
    ) -> str | None:
        """Run an AppleScript by name in dry run mode.

        For 'fetch' operations, delegates to the real client to get actual tracks.
        For update operations, log the action without making any changes.

        Args:
            script_name: Name of the AppleScript to run.
            arguments: List of arguments to pass to the script.
            timeout: Optional timeout in seconds.
            context_artist: Artist name for contextual logging (optional).
            context_album: Album name for contextual logging (optional).
            context_track: Track name for contextual logging (optional).

        Returns:
            str | None: The script output if this is a fetch operation, DRY_RUN_SUCCESS_MESSAGE otherwise.

        """
        if script_name.startswith("fetch"):
            # For fetch operations, we need REAL data from Music.app
            # Apply test_artists filter if configured
            test_artists = self.config.get("development", {}).get("test_artists", [])

            # If test_artists is configured, and we're fetching all tracks (no args),
            # this is handled by the higher-level music_updater logic
            # Don't override here - let the calling code handle multiple test artists
            if test_artists and (not arguments or not arguments[0]):
                self.console_logger.info(
                    "DRY-RUN: Test artists configured: %s (handled by caller)",
                    test_artists,
                )
                # Don't modify arguments - let caller handle test artist iteration

            # Delegate to real client to fetch actual tracks
            result = await self._real_client.run_script(
                script_name,
                arguments,
                timeout,
                context_artist=context_artist,
                context_album=context_album,
                context_track=context_track,
            )
            return str(result) if result is not None else None

        # Log the dry run action without actually executing it
        self.console_logger.info(
            "DRY-RUN: Would run %s with args: %s",
            script_name,
            arguments or [],
        )
        self.actions.append({"script": script_name, "args": arguments or []})
        return str(DRY_RUN_SUCCESS_MESSAGE)

    async def run_script_code(
        self,
        script_code: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
    ) -> str | None:
        """Run raw AppleScript code in dry run mode.

        Args:
            script_code: The AppleScript code to execute
            arguments: Optional list of arguments
            timeout: Optional timeout in seconds (deprecated, will be removed in future versions)

        Returns:
            str | None: DRY_RUN_SUCCESS_MESSAGE or None on error

        """

        async def _execute() -> str:
            await asyncio.sleep(0)
            self.console_logger.info("DRY-RUN: Would execute inline AppleScript")
            self.actions.append({"code": script_code, "args": arguments or []})
            return DRY_RUN_SUCCESS_MESSAGE

        try:
            if timeout is not None:
                # Using asyncio.timeout() context manager for Python 3.11+
                async with asyncio.timeout(timeout):
                    return await _execute()
            return await _execute()
        except TimeoutError:
            self.error_logger.exception(
                "Timeout after %s seconds while executing AppleScript",
                timeout,
            )
            return None
        except (OSError, RuntimeError, ValueError) as e:
            self.error_logger.exception(
                "Error executing AppleScript: %s",
                str(e),
            )
            return None

    def get_actions(self) -> list[dict[str, Any]]:
        """Get the list of actions performed during the dry run."""
        return self.actions
