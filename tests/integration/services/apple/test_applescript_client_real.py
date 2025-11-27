"""Integration tests for AppleScriptClient with real Music.app.

These tests require:
- macOS with Music.app installed
- Music.app running
- At least one track in the library

Run with: pytest tests/integration/services/apple/ -v --tb=short
"""

import asyncio
import logging
import platform
import subprocess
from pathlib import Path

import pytest

from src.app.app_config import Config
from src.metrics.analytics import Analytics, LoggerContainer
from src.services.apple.applescript_client import AppleScriptClient


def is_music_app_running() -> bool:
    """Check if Music.app is running."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to (name of processes) contains "Music"'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "true"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def is_macos() -> bool:
    """Check if running on macOS."""
    return platform.system() == "Darwin"


# Skip all tests in this module if not on macOS or Music.app not running
pytestmark = [
    pytest.mark.skipif(not is_macos(), reason="Requires macOS"),
    pytest.mark.skipif(not is_music_app_running(), reason="Requires Music.app running"),
    pytest.mark.integration,
]


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create a console logger for tests."""
    logger = logging.getLogger("test.integration.applescript")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
    return logger


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create an error logger for tests."""
    logger = logging.getLogger("test.integration.applescript.error")
    logger.setLevel(logging.ERROR)
    return logger


@pytest.fixture
def analytics(
    app_config: dict,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> Analytics:
    """Create analytics instance for tests."""
    analytics_logger = logging.getLogger("test.integration.analytics")
    loggers = LoggerContainer(
        console_logger=console_logger,
        error_logger=error_logger,
        analytics_logger=analytics_logger,
    )
    return Analytics(config=app_config, loggers=loggers)


@pytest.fixture
def app_config() -> dict:
    """Load real application config."""
    config = Config()
    return config.load()


@pytest.fixture
def applescript_client(
    app_config: dict,
    analytics: Analytics,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> AppleScriptClient:
    """Create a real AppleScriptClient instance."""
    return AppleScriptClient(
        config=app_config,
        analytics=analytics,
        console_logger=console_logger,
        error_logger=error_logger,
    )


class TestMusicAppConnection:
    """Tests for Music.app connection and basic operations."""

    def test_music_app_is_running(self) -> None:
        """Verify Music.app is running (prerequisite for other tests)."""
        assert is_music_app_running(), "Music.app must be running for integration tests"

    @pytest.mark.asyncio
    async def test_client_initialization(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test client can be initialized with real config."""
        assert applescript_client is not None
        assert applescript_client.apple_scripts_dir is not None
        assert Path(applescript_client.apple_scripts_dir).exists()

    @pytest.mark.asyncio
    async def test_applescript_files_exist(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test required AppleScript files exist."""
        scripts_dir = Path(applescript_client.apple_scripts_dir)

        required_scripts = [
            "fetch_tracks.scpt",
            "update_property.applescript",
        ]

        for script in required_scripts:
            script_path = scripts_dir / script
            assert script_path.exists(), f"Required script not found: {script}"


class TestFetchTracks:
    """Tests for fetching tracks from Music.app."""

    @pytest.mark.asyncio
    async def test_run_script_code_get_track_count(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test running inline AppleScript to get track count."""
        await applescript_client.initialize()

        script = 'tell application "Music" to count of tracks of library playlist 1'
        result = await applescript_client.run_script_code(script, timeout=30)

        # Result should be a number
        assert result is not None
        track_count = int(result.strip())
        assert track_count >= 0
        print(f"Library contains {track_count} tracks")

    @pytest.mark.asyncio
    async def test_run_script_code_get_first_track_name(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test getting first track name from library."""
        await applescript_client.initialize()

        script = '''
        tell application "Music"
            if (count of tracks of library playlist 1) > 0 then
                return name of track 1 of library playlist 1
            else
                return "NO_TRACKS"
            end if
        end tell
        '''
        result = await applescript_client.run_script_code(script, timeout=30)

        assert result is not None
        result = result.strip()
        if result != "NO_TRACKS":
            print(f"First track: {result}")
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_run_script_fetch_tracks_limited(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test fetching a limited number of tracks via script file."""
        await applescript_client.initialize()

        # Simplified script without tab characters
        script = '''
tell application "Music"
    set trackCount to count of tracks of library playlist 1
    if trackCount > 0 then
        set t to track 1 of library playlist 1
        return (database ID of t as text) & "|" & (name of t) & "|" & (artist of t)
    else
        return "NO_TRACKS"
    end if
end tell
'''
        result = await applescript_client.run_script_code(script, timeout=60)

        assert result is not None
        result = result.strip()
        if result != "NO_TRACKS":
            parts = result.split("|")
            assert len(parts) >= 3, f"Track should have at least 3 fields: {parts}"
            print(f"First track: ID={parts[0]}, Name={parts[1]}, Artist={parts[2]}")


class TestRunScriptFile:
    """Tests for running AppleScript files."""

    @pytest.mark.asyncio
    async def test_run_fetch_tracks_script(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test running the fetch_tracks.scpt file."""
        await applescript_client.initialize()

        scripts_dir = Path(applescript_client.apple_scripts_dir)
        fetch_script = scripts_dir / "fetch_tracks.scpt"

        if not fetch_script.exists():
            pytest.skip("fetch_tracks.scpt not found")

        # Run with test artist filter to limit results
        # The script takes artist name as argument
        result = await applescript_client.run_script(
            str(fetch_script),
            arguments=["__TEST_NONEXISTENT_ARTIST__"],  # Non-existent artist = empty result
            timeout=30,
        )

        # Should return empty or valid output (no crash)
        assert result is not None or result == ""


class TestScriptExecution:
    """Tests for script execution edge cases."""

    @pytest.mark.asyncio
    async def test_run_script_code_with_unicode(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test running script with Unicode characters."""
        await applescript_client.initialize()

        # Simple echo test with Unicode
        script = 'return "Тест Unicode: こんにちは"'
        result = await applescript_client.run_script_code(script, timeout=10)

        assert result is not None
        assert "Тест" in result or "Unicode" in result

    @pytest.mark.asyncio
    async def test_run_script_code_error_handling(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test error handling for invalid AppleScript.

        Note: The client catches errors and returns None instead of raising.
        """
        await applescript_client.initialize()

        # Invalid AppleScript syntax
        script = 'this is not valid applescript syntax at all'

        result = await applescript_client.run_script_code(script, timeout=10)
        # Client catches errors and returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_run_script_code_timeout(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test script timeout handling.

        Note: The client catches timeout errors and returns None instead of raising.
        """
        await applescript_client.initialize()

        # Script that takes too long
        script = '''
delay 3
return "done"
'''

        result = await applescript_client.run_script_code(script, timeout=1)
        # Client catches timeout and returns None
        assert result is None


class TestMusicAppQueries:
    """Tests for querying Music.app metadata."""

    @pytest.mark.asyncio
    async def test_get_library_stats(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test getting library statistics."""
        await applescript_client.initialize()

        script = '''
        tell application "Music"
            set trackCount to count of tracks of library playlist 1
            set playlistCount to count of playlists
            return (trackCount as text) & "," & (playlistCount as text)
        end tell
        '''
        result = await applescript_client.run_script_code(script, timeout=30)

        assert result is not None
        parts = result.strip().split(",")
        assert len(parts) == 2

        track_count = int(parts[0])
        playlist_count = int(parts[1])

        print(f"Library stats: {track_count} tracks, {playlist_count} playlists")
        assert track_count >= 0
        assert playlist_count >= 0

    @pytest.mark.asyncio
    async def test_get_genres_in_library(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test getting unique genres from library."""
        await applescript_client.initialize()

        # Simpler script that gets genres from first 50 tracks
        script = '''
tell application "Music"
    set genreList to {}
    set trackCount to count of tracks of library playlist 1
    if trackCount > 50 then set trackCount to 50
    repeat with i from 1 to trackCount
        set t to track i of library playlist 1
        set g to genre of t
        if g is not in genreList and g is not "" then
            set end of genreList to g
        end if
    end repeat
    set AppleScript's text item delimiters to linefeed
    return genreList as text
end tell
'''
        result = await applescript_client.run_script_code(script, timeout=60)

        assert result is not None
        genres = [g.strip() for g in result.strip().split("\n") if g.strip()]

        print(f"Found {len(genres)} unique genres: {genres[:10]}...")  # Show first 10


class TestConcurrency:
    """Tests for concurrent script execution."""

    @pytest.mark.asyncio
    async def test_concurrent_script_execution(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test running multiple scripts concurrently."""
        await applescript_client.initialize()

        async def get_count() -> int:
            script = 'tell application "Music" to count of tracks of library playlist 1'
            result = await applescript_client.run_script_code(script, timeout=30)
            return int(result.strip())

        # Run 3 queries concurrently
        results = await asyncio.gather(
            get_count(),
            get_count(),
            get_count(),
        )

        # All should return same count
        assert len(results) == 3
        assert results[0] == results[1] == results[2]
        print(f"Concurrent queries all returned: {results[0]} tracks")
