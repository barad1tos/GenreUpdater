"""Unit tests for dry run module."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from src.core.dry_run import DRY_RUN_SUCCESS_MESSAGE, DryRunAppleScriptClient
from src.core.models.types import AppleScriptClientProtocol


@pytest.fixture
def mock_real_client() -> Any:
    """Create a mock real AppleScript client."""
    client = create_autospec(AppleScriptClientProtocol, instance=True)
    client.initialize = AsyncMock()
    client.run_script = AsyncMock(return_value="real_output")
    client.fetch_tracks_by_ids = AsyncMock(return_value=[{"id": "1", "name": "Track"}])
    return client


@pytest.fixture
def config() -> dict[str, Any]:
    """Create test configuration."""
    return {
        "apple_scripts_dir": "/path/to/scripts",
        "development": {"test_artists": []},
    }


@pytest.fixture
def loggers() -> tuple[logging.Logger, logging.Logger]:
    """Create test loggers."""
    console_logger = logging.getLogger("test.console")
    error_logger = logging.getLogger("test.error")
    return console_logger, error_logger


@pytest.fixture
def dry_run_client(
    mock_real_client: Any,
    config: dict[str, Any],
    loggers: tuple[logging.Logger, logging.Logger],
) -> DryRunAppleScriptClient:
    """Create a DryRunAppleScriptClient instance."""
    console_logger, error_logger = loggers
    return DryRunAppleScriptClient(
        real_client=mock_real_client,
        config=config,
        console_logger=console_logger,
        error_logger=error_logger,
    )


class TestDryRunClientInit:
    """Tests for DryRunAppleScriptClient initialization."""

    def test_init_stores_dependencies(
        self,
        mock_real_client: Any,
        config: dict[str, Any],
        loggers: tuple[logging.Logger, logging.Logger],
    ) -> None:
        """Should store all dependencies correctly."""
        console_logger, error_logger = loggers
        client = DryRunAppleScriptClient(
            real_client=mock_real_client,
            config=config,
            console_logger=console_logger,
            error_logger=error_logger,
        )

        assert client.config is config
        assert client.console_logger is console_logger
        assert client.error_logger is error_logger
        assert client.actions == []
        assert client.apple_scripts_dir == "/path/to/scripts"


class TestDryRunClientInitialize:
    """Tests for initialize method."""

    @pytest.mark.asyncio
    async def test_initialize_delegates_to_real_client(
        self, dry_run_client: DryRunAppleScriptClient, mock_real_client: Any
    ) -> None:
        """Should delegate initialization to real client."""
        await dry_run_client.initialize()
        mock_real_client.initialize.assert_called_once()


class TestDryRunClientRunScript:
    """Tests for run_script method."""

    @pytest.mark.asyncio
    async def test_fetch_script_delegates_to_real_client(
        self, dry_run_client: DryRunAppleScriptClient, mock_real_client: Any
    ) -> None:
        """Fetch operations should delegate to real client."""
        result = await dry_run_client.run_script(
            "fetch_tracks.scpt", arguments=["Artist Name"]
        )

        mock_real_client.run_script.assert_called_once()
        assert result == "real_output"

    @pytest.mark.asyncio
    async def test_update_script_logs_and_returns_success(
        self,
        dry_run_client: DryRunAppleScriptClient,
        mock_real_client: Any,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Update operations should log and return success message."""
        with caplog.at_level(logging.INFO):
            result = await dry_run_client.run_script(
                "update_track.scpt", arguments=["track_id", "new_genre"]
            )

        assert result == DRY_RUN_SUCCESS_MESSAGE
        mock_real_client.run_script.assert_not_called()
        assert "DRY-RUN: Would run update_track.scpt" in caplog.text

    @pytest.mark.asyncio
    async def test_update_script_records_action(
        self, dry_run_client: DryRunAppleScriptClient
    ) -> None:
        """Update operations should record actions."""
        await dry_run_client.run_script("update.scpt", arguments=["arg1", "arg2"])

        assert len(dry_run_client.actions) == 1
        assert dry_run_client.actions[0]["script"] == "update.scpt"
        assert dry_run_client.actions[0]["args"] == ["arg1", "arg2"]

    @pytest.mark.asyncio
    async def test_fetch_with_test_artists_logs_info(
        self,
        mock_real_client: Any,
        loggers: tuple[logging.Logger, logging.Logger],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log when test artists are configured."""
        console_logger, error_logger = loggers
        config_with_artists: dict[str, Any] = {
            "apple_scripts_dir": "/scripts",
            "development": {"test_artists": ["Artist1", "Artist2"]},
        }
        client = DryRunAppleScriptClient(
            real_client=mock_real_client,
            config=config_with_artists,
            console_logger=console_logger,
            error_logger=error_logger,
        )

        with caplog.at_level(logging.INFO):
            await client.run_script("fetch_all.scpt", arguments=[""])

        assert "Test artists configured" in caplog.text


class TestDryRunClientRunScriptCode:
    """Tests for run_script_code method."""

    @pytest.mark.asyncio
    async def test_run_script_code_returns_success(
        self, dry_run_client: DryRunAppleScriptClient
    ) -> None:
        """Should return success message for inline code."""
        result = await dry_run_client.run_script_code("tell application Music")
        assert result == DRY_RUN_SUCCESS_MESSAGE

    @pytest.mark.asyncio
    async def test_run_script_code_records_action(
        self, dry_run_client: DryRunAppleScriptClient
    ) -> None:
        """Should record inline code execution."""
        code = "tell application Music to play"
        await dry_run_client.run_script_code(code, arguments=["arg"])

        assert len(dry_run_client.actions) == 1
        assert dry_run_client.actions[0]["code"] == code
        assert dry_run_client.actions[0]["args"] == ["arg"]

    @pytest.mark.asyncio
    async def test_run_script_code_with_timeout(
        self, dry_run_client: DryRunAppleScriptClient
    ) -> None:
        """Should handle timeout parameter."""
        result = await dry_run_client.run_script_code(
            "tell application Music", timeout=5.0
        )
        assert result == DRY_RUN_SUCCESS_MESSAGE


class TestDryRunClientFetchTracksByIds:
    """Tests for fetch_tracks_by_ids method."""

    @pytest.mark.asyncio
    async def test_fetch_tracks_by_ids_delegates(
        self,
        dry_run_client: DryRunAppleScriptClient,
        mock_real_client: Any,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should delegate to real client for fetching tracks."""
        track_ids = ["1", "2", "3"]

        with caplog.at_level(logging.INFO):
            result = await dry_run_client.fetch_tracks_by_ids(track_ids, batch_size=100)

        mock_real_client.fetch_tracks_by_ids.assert_called_once_with(
            track_ids, batch_size=100, timeout=None
        )
        assert result == [{"id": "1", "name": "Track"}]
        assert "DRY-RUN: Fetching 3 tracks" in caplog.text


class TestDryRunClientGetActions:
    """Tests for get_actions method."""

    def test_get_actions_returns_empty_initially(
        self, dry_run_client: DryRunAppleScriptClient
    ) -> None:
        """Should return empty list initially."""
        assert dry_run_client.get_actions() == []

    @pytest.mark.asyncio
    async def test_get_actions_returns_recorded_actions(
        self, dry_run_client: DryRunAppleScriptClient
    ) -> None:
        """Should return all recorded actions."""
        await dry_run_client.run_script("update1.scpt", arguments=["a"])
        await dry_run_client.run_script_code("code1")

        actions = dry_run_client.get_actions()
        assert len(actions) == 2
        assert actions[0]["script"] == "update1.scpt"
        assert actions[1]["code"] == "code1"
