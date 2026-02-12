"""Integration tests for AppleScriptClient with real Music.app.

These tests require:
- macOS with Music.app installed
- Music.app running
- At least one track in the library

Run with: pytest tests/integration/services/apple/ -v --tb=short
"""

from __future__ import annotations

import logging
import platform
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.app_config import Config
from metrics.analytics import Analytics, LoggerContainer
from services.apple.applescript_client import AppleScriptClient

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


def is_music_app_running() -> bool:
    """Check if Music.app is running."""
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", 'tell application "System Events" to (name of processes) contains "Music"'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
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
    app_config: AppConfig,
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
    return Analytics(config=app_config.model_dump(), loggers=loggers)


@pytest.fixture
def app_config() -> AppConfig:
    """Load real application config."""
    config = Config()
    return config.load()


@pytest.fixture
def applescript_client(
    app_config: AppConfig,
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
        assert applescript_client.apple_scripts_dir is not None
        scripts_dir = Path(applescript_client.apple_scripts_dir)

        required_scripts = [
            "fetch_tracks.applescript",
            "update_property.applescript",
        ]

        for script in required_scripts:
            script_path = scripts_dir / script
            assert script_path.exists(), f"Required script not found: {script}"


class TestRunScriptFile:
    """Tests for running AppleScript files."""

    @pytest.mark.asyncio
    async def test_run_fetch_tracks_script(
        self,
        applescript_client: AppleScriptClient,
    ) -> None:
        """Test running the fetch_tracks.applescript file."""
        await applescript_client.initialize()

        assert applescript_client.apple_scripts_dir is not None
        scripts_dir = Path(applescript_client.apple_scripts_dir)
        fetch_script = scripts_dir / "fetch_tracks.applescript"

        if not fetch_script.exists():
            pytest.skip("fetch_tracks.applescript not found")

        # Run with test artist filter to limit results
        # The script takes artist name as argument
        result = await applescript_client.run_script(
            str(fetch_script),
            arguments=["__TEST_NONEXISTENT_ARTIST__"],  # Non-existent artist = empty result
            timeout=30,
        )

        # Should return empty or valid output (no crash)
        assert result is not None or result == ""
