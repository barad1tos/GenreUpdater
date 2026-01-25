"""Swift Helper Bridge Module.

This module provides a Python bridge to the Swift Music Helper daemon.
It uses Unix Domain Sockets with length-prefixed JSON protocol for high-performance
IPC, replacing slow AppleScript-based operations with native ScriptingBridge calls.

Expected performance improvements:
- fetch_all_track_ids: 95s → ~1.5s (63x faster).
- fetch_tracks: 120s → ~3s (40x faster).
- batch_update: 50s → ~0.5s (100x faster).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import struct
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.logger import LogFormat
from core.models.protocols import AppleScriptClientProtocol

if TYPE_CHECKING:
    from metrics import Analytics
    from services.apple.applescript_client import AppleScriptClient

# Protocol constants
SOCKET_TIMEOUT = 300.0  # 5 minutes max for large operations
LENGTH_PREFIX_SIZE = 4  # 4 bytes, big-endian
MAX_MESSAGE_SIZE = 100 * 1024 * 1024  # 100MB max message

ScriptHandler = Callable[
    [str, list[str] | None, float | None, str | None, str | None, str | None, str | None],
    Awaitable[str | None],
]

# Error code to errno mapping (matches Swift ErrorCodes.swift)
ERROR_CODE_TO_ERRNO: dict[int, int] = {
    1000: 61,  # musicAppNotRunning → ECONNREFUSED
    1001: 61,  # libraryNotAccessible → ECONNREFUSED
    1002: 2,  # trackNotFound → ENOENT
    1003: 2,  # playlistNotFound → ENOENT
    1004: 22,  # propertyNotSupported → EINVAL
    1005: 22,  # valueInvalid → EINVAL
    1006: 22,  # yearOutOfRange → EINVAL
    1010: 22,  # malformedRequest → EINVAL
    1011: 22,  # unknownMethod → EINVAL
    1099: 5,  # internalError → EIO
}

# Retryable error codes
RETRYABLE_ERROR_CODES: frozenset[int] = frozenset({1000, 1001})


@dataclass
class SwiftHelperConfig:
    """Configuration for Swift Helper daemon."""

    enabled: bool = True
    binary_path: str | None = None
    socket_timeout_seconds: float = SOCKET_TIMEOUT
    auto_start: bool = True
    max_retries: int = 3
    retry_delay_seconds: float = 1.0


class SwiftBridgeError(OSError):
    """Base exception for Swift bridge errors."""

    def __init__(self, message: str, errno: int = 5, retryable: bool = False) -> None:
        """Initialize SwiftBridgeError.

        Args:
            message: Error message
            errno: Unix errno value (default: 5 = EIO)
            retryable: Whether the error is retryable

        """
        super().__init__(errno, message)
        self.errno = errno
        self.retryable = retryable


class SwiftDaemonNotRunningError(SwiftBridgeError):
    """Raised when Swift daemon is not running."""

    def __init__(self, message: str = "Swift daemon is not running") -> None:
        """Initialize SwiftDaemonNotRunningError."""
        super().__init__(message, errno=61, retryable=True)


class MusicAppNotRunningError(SwiftBridgeError):
    """Raised when Music.app is not running."""

    def __init__(self, message: str = "Music.app is not running") -> None:
        """Initialize MusicAppNotRunningError."""
        super().__init__(message, errno=61, retryable=True)


class SwiftBridge(AppleScriptClientProtocol):
    """Bridge to Swift Music Helper daemon via Unix Domain Socket.

    This class implements AppleScriptClientProtocol for compatibility with
    existing code while using the Swift daemon for actual operations.

    Attributes:
        config: Application configuration dictionary
        helper_config: Swift helper specific configuration
        analytics: Analytics instance for tracking
        console_logger: Logger for console output
        error_logger: Logger for error output
        apple_scripts_dir: Directory containing AppleScript files (for compatibility)

    """

    def __init__(
        self,
        config: dict[str, Any],
        analytics: Analytics,
        console_logger: logging.Logger | None = None,
        error_logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the Swift bridge.

        Args:
            config: Application configuration dictionary
            analytics: Analytics instance for tracking
            console_logger: Logger for console output
            error_logger: Logger for error output

        """
        self.config = config
        self.analytics = analytics
        self.console_logger = console_logger or logging.getLogger(__name__)
        self.error_logger = error_logger or self.console_logger

        # Parse helper config
        helper_config_dict = config.get("swift_helper", {})
        self.helper_config = SwiftHelperConfig(
            enabled=helper_config_dict.get("enabled", True),
            binary_path=helper_config_dict.get("binary_path"),
            socket_timeout_seconds=helper_config_dict.get("socket_timeout_seconds", SOCKET_TIMEOUT),
            auto_start=helper_config_dict.get("auto_start", True),
            max_retries=helper_config_dict.get("max_retries", 3),
            retry_delay_seconds=helper_config_dict.get("retry_delay_seconds", 1.0),
        )

        # For protocol compatibility
        self.apple_scripts_dir = config.get("apple_scripts_dir")

        # Daemon state
        self._daemon_process: asyncio.subprocess.Process | None = None
        self._socket_path: str | None = None
        self._initialized = False
        self._lock = asyncio.Lock()
        self._fallback_client: AppleScriptClient | None = None
        self._fallback_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the Swift bridge and start the daemon.

        Raises:
            FileNotFoundError: If Swift binary not found
            OSError: If daemon fails to start

        """
        if self._initialized:
            self.console_logger.debug("SwiftBridge already initialized")
            return

        async with self._lock:
            if self._initialized:
                return

            # Find Swift binary
            binary_path = self._find_binary()
            if not binary_path:
                msg = "Swift helper binary not found. Run 'swift-helper/build.sh' to build it."
                raise FileNotFoundError(msg)

            # Create socket path in temp directory
            socket_filename = f"music-helper-{os.getpid()}.sock"
            self._socket_path = str(Path(tempfile.gettempdir()) / socket_filename)

            # Start daemon if auto_start is enabled
            if self.helper_config.auto_start:
                await self._start_daemon(binary_path)

            self._initialized = True
            self.console_logger.info(
                "%s initialized (socket: %s)",
                LogFormat.entity("SwiftBridge"),
                self._socket_path,
            )

    def _find_binary(self) -> str | None:
        """Find the Swift helper binary.

        Search order:
        1. Configured path in helper_config.binary_path
        2. Relative to project: swift-helper/.build/release/MusicHelper
        3. Relative to project: swift-helper/.build/debug/MusicHelper

        Returns:
            Path to binary or None if not found

        """
        # Check configured path first
        if self.helper_config.binary_path:
            if Path(self.helper_config.binary_path).exists():
                return self.helper_config.binary_path
            self.console_logger.warning(
                "Configured Swift binary not found: %s",
                self.helper_config.binary_path,
            )

        # Try to find relative to project root
        # Assume we're in src/services/apple/, go up to project root
        project_root = Path(__file__).parent.parent.parent.parent

        # Try release build first (binary name is "music-helper", not "MusicHelper")
        release_path = project_root / "swift-helper" / ".build" / "release" / "music-helper"
        if release_path.exists():
            self.console_logger.debug("Found release binary: %s", release_path)
            return str(release_path)

        # Try debug build
        debug_path = project_root / "swift-helper" / ".build" / "debug" / "music-helper"
        if debug_path.exists():
            self.console_logger.debug("Found debug binary: %s", debug_path)
            return str(debug_path)

        # Also check the build output directory (from build.sh)
        build_output = project_root / "swift-helper" / "build" / "music-helper"
        if build_output.exists():
            self.console_logger.debug("Found binary in build/: %s", build_output)
            return str(build_output)

        return None

    async def _start_daemon(self, binary_path: str) -> None:
        """Start the Swift daemon process.

        Args:
            binary_path: Path to the Swift binary

        Raises:
            OSError: If daemon fails to start

        """
        if self._daemon_process is not None and self._daemon_process.returncode is None:
            self.console_logger.debug("Daemon already running")
            return

        # Clean up old socket if exists
        if self._socket_path:
            socket_path = Path(self._socket_path)
            if socket_path.exists():
                socket_path.unlink()

        self.console_logger.debug(
            "Starting Swift daemon: %s --socket %s",
            binary_path,
            self._socket_path,
        )

        # At this point socket_path is guaranteed to be set (line 185-186 above)
        assert self._socket_path is not None

        try:
            # S603: binary_path is from _find_binary() which only returns validated paths
            # S607: Using full path from _find_binary()
            self._daemon_process = await asyncio.create_subprocess_exec(
                binary_path,
                "--socket",
                self._socket_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # Detach from parent process group
            )

            # Wait for daemon to create socket (with timeout)
            await self._wait_for_socket()

            self.console_logger.info("Swift daemon started (pid: %d)", self._daemon_process.pid)

        except OSError as e:
            self.error_logger.exception("Failed to start Swift daemon: %s", e)
            msg = f"Failed to start Swift daemon: {e}"
            raise OSError(msg) from e

    async def _wait_for_socket(self, timeout: float = 10.0) -> None:
        """Wait for the daemon to create the socket file.

        Args:
            timeout: Maximum time to wait in seconds

        Raises:
            TimeoutError: If socket not created within timeout

        """
        if not self._socket_path:
            msg = "Socket path not set"
            raise ValueError(msg)

        socket_path = Path(self._socket_path)
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            if socket_path.exists():
                return
            # Check if daemon died
            if self._daemon_process and self._daemon_process.returncode is not None:
                stderr = ""
                stderr_stream = self._daemon_process.stderr
                if stderr_stream is not None:
                    stderr_bytes = await stderr_stream.read()
                    stderr = stderr_bytes.decode()
                msg = f"Swift daemon exited prematurely with code {self._daemon_process.returncode}: {stderr}"
                raise OSError(msg)
            await asyncio.sleep(0.1)

        msg = "Swift daemon socket not created within timeout"
        raise TimeoutError(msg)

    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a request to the Swift daemon and receive response.

        Args:
            method: RPC method name
            params: Optional parameters dictionary

        Returns:
            Response dictionary with 'success', 'result', and optionally 'error'

        Raises:
            SwiftDaemonNotRunningError: If daemon is not running
            OSError: If communication fails
            TimeoutError: If request times out

        """
        if not self._initialized:
            msg = "SwiftBridge not initialized"
            raise SwiftDaemonNotRunningError(msg)

        if not self._socket_path:
            msg = "Socket path not set"
            raise SwiftDaemonNotRunningError(msg)

        # Build request
        request_id = f"req-{uuid.uuid4()}"
        request = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        # Serialize to JSON
        request_json = json.dumps(request).encode()
        if len(request_json) > MAX_MESSAGE_SIZE:
            msg = f"Request too large: {len(request_json)} bytes"
            raise ValueError(msg)

        # Create length prefix (4 bytes, big-endian)
        length_prefix = struct.pack(">I", len(request_json))

        async def _perform_request() -> dict[str, Any]:
            reader, writer = await asyncio.open_unix_connection(self._socket_path)
            try:
                writer.write(length_prefix + request_json)
                await writer.drain()

                length_data = await reader.readexactly(LENGTH_PREFIX_SIZE)
                response_length = struct.unpack(">I", length_data)[0]

                if response_length > MAX_MESSAGE_SIZE:
                    message = f"Response too large: {response_length} bytes"
                    raise ValueError(message)

                response_data = await reader.readexactly(response_length)
                response = json.loads(response_data.decode())

                if response.get("id") != request_id:
                    self.console_logger.warning(
                        "Response ID mismatch: expected %s, got %s",
                        request_id,
                        response.get("id"),
                    )

                return response
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        try:
            timeout_seconds = self.helper_config.socket_timeout_seconds
            if timeout_seconds and timeout_seconds > 0:
                return await asyncio.wait_for(_perform_request(), timeout=timeout_seconds)
            return await _perform_request()
        except TimeoutError as e:
            msg = f"Request to Swift daemon timed out: {method}"
            raise TimeoutError(msg) from e
        except (asyncio.IncompleteReadError, OSError) as e:
            if self._daemon_process and self._daemon_process.returncode is not None:
                msg = f"Swift daemon died during request: {e}"
                raise SwiftDaemonNotRunningError(msg) from e
            msg = f"Socket communication failed: {e}"
            raise OSError(msg) from e

    def _handle_response(self, response: dict[str, Any], context: str = "") -> Any:
        """Handle response from Swift daemon, raising errors if needed.

        Args:
            response: Response dictionary from daemon
            context: Context for error messages (e.g., "fetch_all_track_ids")

        Returns:
            The 'result' field from successful response

        Raises:
            OSError: If response indicates an error

        """
        if response.get("success"):
            return response.get("result")

        # Handle error
        error = response.get("error", {})
        error_code = error.get("code", 1099)
        error_message = error.get("message", "Unknown error")
        error_errno = error.get("errno", ERROR_CODE_TO_ERRNO.get(error_code, 5))
        retryable = error.get("retryable", error_code in RETRYABLE_ERROR_CODES)

        # Log error with context
        log_msg = f"Swift daemon error in {context}: [{error_code}] {error_message}"
        if retryable:
            self.console_logger.warning(log_msg)
        else:
            self.error_logger.error(log_msg)

        # Raise appropriate exception based on error code
        if error_code == 1000:  # musicAppNotRunning
            raise MusicAppNotRunningError(error_message)

        full_message = f"{error_message} (code={error_code}, context={context})"
        raise SwiftBridgeError(full_message, errno=error_errno, retryable=retryable)

    # ─────────────────────────────────────────────────────────────────────────────
    # AppleScriptClientProtocol Implementation
    # ─────────────────────────────────────────────────────────────────────────────

    async def run_script(
        self,
        script_name: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
        context_artist: str | None = None,
        context_album: str | None = None,
        context_track: str | None = None,
        label: str | None = None,
    ) -> str | None:
        """Run an AppleScript file by name.

        Note: SwiftBridge supports a subset of AppleScript operations natively and
        falls back to AppleScript for unsupported scripts or arguments.
        For high-performance operations, use the dedicated methods:
        - fetch_all_track_ids().
        - fetch_tracks_by_ids().
        - Update_property() (via batch_update_tracks).

        Args:
            script_name: Name of the script file to execute
            arguments: Optional arguments to pass to the script
            timeout: Optional timeout in seconds
            context_artist: Artist name for contextual logging
            context_album: Album name for contextual logging
            context_track: Track name for contextual logging
            label: Custom label for logging

        Returns:
            Script output or None

        Raises:
            SwiftBridgeError: For Swift daemon errors without AppleScript fallback

        """
        handler = self._get_script_handler(script_name)
        if handler is None:
            return await self._run_fallback_script(
                script_name,
                arguments,
                timeout,
                context_artist,
                context_album,
                context_track,
                label,
            )
        return await handler(
            script_name,
            arguments,
            timeout,
            context_artist,
            context_album,
            context_track,
            label,
        )

    async def fetch_all_track_ids(
        self,
        timeout: float | None = None,
    ) -> list[str]:
        """Fetch all track IDs from Music.app.

        This uses the Swift daemon's native ScriptingBridge integration,
        which is ~63x faster than AppleScript (95s → ~1.5s).

        Args:
            timeout: Timeout in seconds for operation

        Returns:
            List of track ID strings

        """
        del timeout  # Protocol compliance: Swift daemon manages its own timeouts
        await self._ensure_initialized()

        self.console_logger.debug("Fetching all track IDs via SwiftBridge")

        response = await self._send_request("fetch_all_track_ids")
        result = self._handle_response(response, "fetch_all_track_ids")

        track_ids = result.get("track_ids", [])
        count = result.get("count", len(track_ids))

        self.console_logger.info("Fetched %d track IDs via SwiftBridge", count)
        return track_ids

    def _get_script_handler(
        self,
        script_name: str,
    ) -> ScriptHandler | None:
        handlers: dict[str, ScriptHandler] = {
            "fetch_track_ids.applescript": self._handle_fetch_track_ids,
            "fetch_tracks_by_ids.scpt": self._handle_fetch_tracks_by_ids,
            "fetch_tracks.applescript": self._handle_fetch_tracks,
            "update_property.applescript": self._handle_update_property,
            "batch_update_tracks.applescript": self._handle_batch_update,
        }
        return handlers.get(script_name)

    async def _handle_fetch_track_ids(
        self,
        _script_name: str,
        _arguments: list[str] | None,
        timeout: float | None,
        _context_artist: str | None,
        _context_album: str | None,
        _context_track: str | None,
        _label: str | None,
    ) -> str | None:
        ids = await self.fetch_all_track_ids(timeout=timeout)
        return ",".join(ids) if ids else None

    async def _handle_fetch_tracks_by_ids(
        self,
        script_name: str,
        arguments: list[str] | None,
        timeout: float | None,
        context_artist: str | None,
        context_album: str | None,
        context_track: str | None,
        label: str | None,
    ) -> str | None:
        if not arguments or not arguments[0].strip():
            return await self._run_fallback_script(
                script_name,
                arguments,
                timeout,
                context_artist,
                context_album,
                context_track,
                label,
            )

        track_ids = arguments[0].split(",")
        try:
            tracks = await self.fetch_tracks_by_ids(track_ids, timeout=timeout)
        except SwiftBridgeError as exc:
            self.error_logger.warning("SwiftBridge fetch_tracks_by_ids failed: %s", exc)
            return await self._run_fallback_script(
                script_name,
                arguments,
                timeout,
                context_artist,
                context_album,
                context_track,
                label,
            )
        return self._tracks_to_applescript_format(tracks)

    async def _handle_fetch_tracks(
        self,
        script_name: str,
        arguments: list[str] | None,
        timeout: float | None,
        context_artist: str | None,
        context_album: str | None,
        context_track: str | None,
        label: str | None,
    ) -> str | None:
        artist, offset, limit, min_date = self._parse_fetch_tracks_args(arguments)

        if arguments:
            if len(arguments) > 1 and arguments[1].strip() and offset is None:
                return await self._run_fallback_script(
                    script_name,
                    arguments,
                    timeout,
                    context_artist,
                    context_album,
                    context_track,
                    label,
                )
            if len(arguments) > 2 and arguments[2].strip() and limit is None:
                return await self._run_fallback_script(
                    script_name,
                    arguments,
                    timeout,
                    context_artist,
                    context_album,
                    context_track,
                    label,
                )
            if len(arguments) > 3 and arguments[3].strip() and min_date is None:
                return await self._run_fallback_script(
                    script_name,
                    arguments,
                    timeout,
                    context_artist,
                    context_album,
                    context_track,
                    label,
                )

        try:
            tracks = await self.fetch_tracks(
                artist=artist,
                limit=limit,
                offset=offset,
                min_date_added=min_date,
            )
        except SwiftBridgeError as exc:
            self.error_logger.warning("SwiftBridge fetch_tracks failed: %s", exc)
            return await self._run_fallback_script(
                script_name,
                arguments,
                timeout,
                context_artist,
                context_album,
                context_track,
                label,
            )
        return self._tracks_to_applescript_format(tracks)

    async def _handle_update_property(
        self,
        script_name: str,
        arguments: list[str] | None,
        timeout: float | None,
        context_artist: str | None,
        context_album: str | None,
        context_track: str | None,
        label: str | None,
    ) -> str | None:
        if not arguments or len(arguments) < 3:
            return "Error: Not enough arguments. Usage: TrackID PropertyName PropertyValue"

        track_id = arguments[0]
        raw_property = arguments[1]
        value = arguments[2]

        normalized_property = self._normalize_property_name(raw_property)
        if not normalized_property:
            return "Error: Missing property name"
        if normalized_property not in {"name", "album", "artist", "album_artist", "genre", "year"}:
            return f"Error: Unsupported property '{raw_property}'. Must be name, album, artist, album_artist, genre, or year."
        if not value or not value.strip():
            return "Error: Empty property value"

        try:
            result = await self.update_property(track_id, normalized_property, value.strip())
        except SwiftBridgeError as exc:
            self.error_logger.warning("SwiftBridge update_property failed: %s", exc)
            return await self._run_fallback_script(
                script_name,
                arguments,
                timeout,
                context_artist,
                context_album,
                context_track,
                label,
            )

        return self._format_update_property_result(track_id, normalized_property, value.strip(), result)

    async def _handle_batch_update(
        self,
        script_name: str,
        arguments: list[str] | None,
        timeout: float | None,
        context_artist: str | None,
        context_album: str | None,
        context_track: str | None,
        label: str | None,
    ) -> str | None:
        if not arguments:
            return "Error: No update string provided."
        updates = self._parse_batch_update_commands(arguments[0])
        if not updates:
            return "Error: No update commands provided."

        try:
            result = await self.batch_update_tracks(updates)
        except SwiftBridgeError as exc:
            self.error_logger.warning("SwiftBridge batch_update_tracks failed: %s", exc)
            return await self._run_fallback_script(
                script_name,
                arguments,
                timeout,
                context_artist,
                context_album,
                context_track,
                label,
            )

        return self._format_batch_update_result(result)

    async def fetch_tracks_by_ids(
        self,
        track_ids: list[str],
        batch_size: int = 1000,
        timeout: float | None = None,
    ) -> list[dict[str, str]]:
        """Fetch tracks by their IDs.

        This uses the Swift daemon's native ScriptingBridge integration,
        which is ~40x faster than AppleScript (120s → ~3s for 37K tracks).

        Args:
            track_ids: List of track IDs to fetch
            batch_size: Not used (Swift handles all IDs efficiently)
            timeout: Timeout in seconds for operation

        Returns:
            List of track dictionaries with metadata

        """
        del batch_size, timeout  # Protocol compliance: Swift daemon handles these
        if not track_ids:
            return []

        await self._ensure_initialized()

        self.console_logger.debug("Fetching %d tracks by IDs via SwiftBridge", len(track_ids))

        response = await self._send_request(
            "fetch_tracks_by_ids",
            {"track_ids": track_ids},
        )
        result = self._handle_response(response, "fetch_tracks_by_ids")

        tracks = result.get("tracks", [])

        # Convert Swift field names to Python field names
        converted_tracks = [self._convert_track_fields(t) for t in tracks]

        self.console_logger.info(
            "Fetched %d tracks via SwiftBridge (requested: %d)",
            len(converted_tracks),
            len(track_ids),
        )
        return converted_tracks

    # ─────────────────────────────────────────────────────────────────────────────
    # Additional Methods (not in protocol but useful)
    # ─────────────────────────────────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Check if Swift daemon and Music.app are running.

        Returns:
            Dictionary with health status:
            - music_app_running: bool
            - library_accessible: bool
            - track_count: int or None
            - version: str

        """
        await self._ensure_initialized()

        response = await self._send_request("health_check")
        return self._handle_response(response, "health_check")

    async def fetch_tracks(
        self,
        artist: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        min_date_added: int | None = None,
    ) -> list[dict[str, str]]:
        """Fetch tracks with optional filtering.

        Args:
            artist: Optional artist name filter
            limit: Optional maximum number of tracks
            offset: Optional 1-based offset for batch fetching
            min_date_added: Optional Unix timestamp for filtering by date added

        Returns:
            List of track dictionaries

        """
        await self._ensure_initialized()

        params: dict[str, Any] = {}
        if artist:
            params["artist"] = artist
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if min_date_added is not None:
            params["min_date_added"] = min_date_added

        response = await self._send_request("fetch_tracks", params or None)
        result = self._handle_response(response, "fetch_tracks")

        tracks = result.get("tracks", [])
        return [self._convert_track_fields(t) for t in tracks]

    async def update_property(
        self,
        track_id: str,
        property_name: str,
        value: str,
    ) -> dict[str, Any]:
        """Update a single property on a track.

        Args:
            track_id: Track persistent ID
            property_name: Property to update (genre, year, etc.)
            value: New value

        Returns:
            Update result dictionary

        """
        await self._ensure_initialized()

        response = await self._send_request(
            "update_property",
            {
                "track_id": track_id,
                "property": property_name,
                "value": value,
            },
        )
        return self._handle_response(response, "update_property")

    async def batch_update_tracks(
        self,
        updates: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Batch update multiple tracks.

        This is ~100x faster than AppleScript batch updates (50s → ~0.5s).

        Args:
            updates: List of update dictionaries with keys:
                - track_id: Track persistent ID
                - property: Property to update
                - value: New value

        Returns:
            Batch result with success/failure counts

        """
        if not updates:
            return {"results": [], "success_count": 0, "failure_count": 0}

        await self._ensure_initialized()

        response = await self._send_request(
            "batch_update_tracks",
            {"updates": updates},
        )
        return self._handle_response(response, "batch_update_tracks")

    async def shutdown(self) -> None:
        """Gracefully shutdown the Swift daemon."""
        if not self._initialized:
            return

        # Send shutdown request - daemon may already be dead
        with contextlib.suppress(OSError, SwiftBridgeError):
            await self._send_request("shutdown")
            self.console_logger.debug("Sent shutdown request to Swift daemon")

        # Wait for daemon to exit
        if self._daemon_process:
            try:
                await asyncio.wait_for(self._daemon_process.wait(), timeout=5.0)
            except TimeoutError:
                self.console_logger.warning("Swift daemon didn't exit, terminating")
                self._daemon_process.terminate()
                try:
                    await asyncio.wait_for(self._daemon_process.wait(), timeout=2.0)
                except TimeoutError:
                    self._daemon_process.kill()

            self._daemon_process = None

        # Clean up socket file
        if self._socket_path:
            socket_path = Path(self._socket_path)
            if socket_path.exists():
                socket_path.unlink()

        self._initialized = False
        self.console_logger.info("SwiftBridge shut down")

    # ─────────────────────────────────────────────────────────────────────────────
    # Private Helpers
    # ─────────────────────────────────────────────────────────────────────────────

    async def _ensure_initialized(self) -> None:
        """Ensure the bridge is initialized."""
        if not self._initialized:
            await self.initialize()

    async def _run_fallback_script(
        self,
        script_name: str,
        arguments: list[str] | None,
        timeout: float | None,
        context_artist: str | None,
        context_album: str | None,
        context_track: str | None,
        label: str | None,
    ) -> str | None:
        """Run a script via AppleScriptClient when SwiftBridge can't handle it."""
        client = await self._ensure_fallback_client()
        return await client.run_script(
            script_name,
            arguments,
            timeout=timeout,
            context_artist=context_artist,
            context_album=context_album,
            context_track=context_track,
            label=label,
        )

    async def _ensure_fallback_client(self) -> AppleScriptClient:
        """Initialize the AppleScript fallback client on demand."""
        async with self._fallback_lock:
            if self._fallback_client is None:
                from services.apple.applescript_client import AppleScriptClient  # noqa: PLC0415

                fallback_client = AppleScriptClient(
                    self.config,
                    self.analytics,
                    self.console_logger,
                    self.error_logger,
                )

                await fallback_client.initialize()
                self._fallback_client = fallback_client
        assert self._fallback_client is not None
        return self._fallback_client

    @staticmethod
    def _parse_int_arg(value: str) -> int | None:
        """Parse integer argument, returning None if invalid/empty."""
        if not value or not value.strip():
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_fetch_tracks_args(
        arguments: list[str] | None,
    ) -> tuple[str | None, int | None, int | None, int | None]:
        """Parse fetch_tracks.applescript arguments into structured values."""
        artist = None
        offset = None
        limit = None
        min_date = None

        if arguments:
            if len(arguments) > 0 and arguments[0].strip():
                artist = arguments[0].strip()
            if len(arguments) > 1 and arguments[1].strip():
                offset = SwiftBridge._parse_int_arg(arguments[1])
            if len(arguments) > 2 and arguments[2].strip():
                limit = SwiftBridge._parse_int_arg(arguments[2])
            if len(arguments) > 3 and arguments[3].strip():
                min_date = SwiftBridge._parse_int_arg(arguments[3])

        return artist, offset, limit, min_date

    @staticmethod
    def _normalize_property_name(raw_name: str) -> str:
        """Normalize property names to Swift helper format."""
        normalized = raw_name.strip().lower().replace("-", "_").replace(" ", "_")
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        return normalized.strip("_")

    @staticmethod
    def _normalize_date_string(value: Any) -> str:
        """Normalize date strings to AppleScript-compatible format."""
        if value is None:
            return ""
        raw = str(value).strip()
        if not raw:
            return ""
        if "T" not in raw:
            return raw
        candidate = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return raw
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _format_update_property_result(
        track_id: str,
        property_name: str,
        desired_value: str,
        result: dict[str, Any],
    ) -> str:
        """Format update_property response to match AppleScript output."""
        old_value = str(result.get("old_value", ""))
        new_value = str(result.get("new_value", desired_value))
        if old_value == new_value:
            return f"No Change: Track {track_id} {property_name} already set to {new_value}"
        return f"Success: Updated track {track_id} {property_name} from '{old_value}' to '{new_value}'"

    @staticmethod
    def _parse_batch_update_commands(command_string: str) -> list[dict[str, str]]:
        """Parse batch_update_tracks.applescript command string."""
        updates: list[dict[str, str]] = []
        for raw_command in command_string.split(";"):
            command = raw_command.strip()
            if not command:
                continue
            parts = command.split(":", 2)
            if len(parts) != 3:
                continue
            track_id, property_name, value = (part.strip() for part in parts)
            if not track_id or not property_name:
                continue
            updates.append(
                {
                    "track_id": track_id,
                    "property": property_name,
                    "value": value,
                }
            )
        return updates

    @staticmethod
    def _format_batch_update_result(result: dict[str, Any]) -> str:
        """Format batch update result to match AppleScript output."""
        failure_count = int(result.get("failure_count", 0))
        if failure_count > 0:
            return f"Success: Batch update process completed (failures: {failure_count})"
        return "Success: Batch update process completed."

    @staticmethod
    def _convert_track_fields(track: dict[str, Any]) -> dict[str, str]:
        """Convert Swift track field names to Python field names.

        Swift uses camelCase (album_artist, date_added, cloud_status)
        Python uses snake_case with some variations (track_status instead of cloud_status)

        Args:
            track: Track dictionary from Swift

        Returns:
            Track dictionary with Python field names

        """
        date_added = SwiftBridge._normalize_date_string(track.get("date_added", ""))
        modification_date = SwiftBridge._normalize_date_string(track.get("modification_date", ""))
        track_status = track.get("cloud_status") or track.get("track_status") or ""

        return {
            "id": str(track.get("id", "")),
            "name": str(track.get("name", "")),
            "artist": str(track.get("artist", "")),
            "album_artist": str(track.get("album_artist", "")),
            "album": str(track.get("album", "")),
            "genre": str(track.get("genre", "")),
            "date_added": date_added,
            "last_modified": modification_date,
            "modification_date": modification_date,
            "track_status": str(track_status),
            "year": str(track.get("year", "")),
            "release_year": str(track.get("release_year", "")),
        }

    @staticmethod
    def _tracks_to_applescript_format(tracks: list[dict[str, str]]) -> str:
        """Convert tracks to AppleScript output format (for compatibility).

        This is NOT recommended - use JSON responses directly instead.
        Only used for legacy compatibility with run_script().

        Args:
            tracks: List of track dictionaries

        Returns:
            AppleScript-style output with ASCII separators

        """
        # Import here to avoid circular dependency: track_delta → protocols → swift_bridge
        from core.tracks.track_delta import (  # noqa: PLC0415
            FIELD_SEPARATOR,
            LINE_SEPARATOR,
        )

        lines = []
        for track in tracks:
            modification_date = track.get("last_modified") or track.get("modification_date") or ""
            track_status = track.get("track_status") or track.get("cloud_status") or ""
            fields = [
                track.get("id", ""),
                track.get("name", ""),
                track.get("artist", ""),
                track.get("album_artist", ""),
                track.get("album", ""),
                track.get("genre", ""),
                track.get("date_added", ""),
                modification_date,
                track_status,
                track.get("year", ""),
                track.get("release_year", ""),
                "",  # Empty placeholder for compatibility
            ]
            lines.append(FIELD_SEPARATOR.join(fields))

        return LINE_SEPARATOR.join(lines)

    def __del__(self) -> None:
        """Cleanup daemon on object destruction."""
        # Use getattr to safely handle partially constructed objects
        daemon_process = getattr(self, "_daemon_process", None)
        if daemon_process and daemon_process.returncode is None:
            daemon_process.terminate()
        if socket_path := getattr(self, "_socket_path", None):
            with contextlib.suppress(OSError):
                Path(socket_path).unlink(missing_ok=True)
