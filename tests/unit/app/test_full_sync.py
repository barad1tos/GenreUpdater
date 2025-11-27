"""Unit tests for full sync module."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.full_sync import run_full_resync

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create test console logger."""
    return logging.getLogger("test.console")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create test error logger."""
    return logging.getLogger("test.error")


@pytest.fixture
def config(tmp_path: Path) -> dict[str, Any]:
    """Create test configuration."""
    return {
        "logs_base_dir": str(tmp_path / "logs"),
        "logging": {"csv_output_file": "track_list.csv"},
    }


@pytest.fixture
def mock_cache_service() -> MagicMock:
    """Create mock cache service."""
    return MagicMock(spec=["get", "set", "clear"])


@pytest.fixture
def mock_track_processor() -> MagicMock:
    """Create mock track processor."""
    processor = MagicMock()
    processor.fetch_tracks_async = AsyncMock(
        return_value=[
            {"id": "1", "name": "Track 1", "artist": "Artist 1", "album": "Album 1"},
            {"id": "2", "name": "Track 2", "artist": "Artist 2", "album": "Album 2"},
        ]
    )
    processor.ap_client = MagicMock()
    return processor


class TestRunFullResync:
    """Tests for run_full_resync function."""

    @pytest.mark.asyncio
    async def test_exits_early_when_music_app_not_running(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should exit early when Music.app is not running."""
        with patch(
            "src.app.full_sync.is_music_app_running", return_value=False
        ) as mock_check:
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

            mock_check.assert_called_once_with(error_logger)
            mock_track_processor.fetch_tracks_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_exits_early_when_no_tracks_found(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should exit early when no tracks found in Music.app."""
        mock_track_processor.fetch_tracks_async.return_value = []

        with (
            patch("src.app.full_sync.is_music_app_running", return_value=True),
            patch("src.app.full_sync.sync_track_list_with_current") as mock_sync,
            caplog.at_level(logging.WARNING),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

            mock_track_processor.fetch_tracks_async.assert_called_once()
            mock_sync.assert_not_called()
            assert "No tracks found" in caplog.text

    @pytest.mark.asyncio
    async def test_performs_full_sync_successfully(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should perform full sync when tracks are found."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("src.app.full_sync.is_music_app_running", return_value=True),
            patch("src.app.full_sync.get_full_log_path", return_value=csv_path),
            patch("src.app.full_sync.sync_track_list_with_current", new_callable=AsyncMock) as mock_sync,
            caplog.at_level(logging.INFO),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

            mock_track_processor.fetch_tracks_async.assert_called_once()
            mock_sync.assert_called_once()

            # Verify sync was called with correct arguments
            call_args = mock_sync.call_args
            assert len(call_args[0][0]) == 2  # Two tracks
            assert call_args[0][1] == csv_path
            assert call_args[1]["partial_sync"] is False

    @pytest.mark.asyncio
    async def test_raises_on_os_error(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should raise OSError when sync fails."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("src.app.full_sync.is_music_app_running", return_value=True),
            patch("src.app.full_sync.get_full_log_path", return_value=csv_path),
            patch(
                "src.app.full_sync.sync_track_list_with_current",
                new_callable=AsyncMock,
                side_effect=OSError("Disk full"),
            ),
            pytest.raises(OSError, match="Disk full"),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

    @pytest.mark.asyncio
    async def test_raises_on_runtime_error(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should raise RuntimeError when sync fails."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("src.app.full_sync.is_music_app_running", return_value=True),
            patch("src.app.full_sync.get_full_log_path", return_value=csv_path),
            patch(
                "src.app.full_sync.sync_track_list_with_current",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Unexpected error"),
            ),
            pytest.raises(RuntimeError, match="Unexpected error"),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

    @pytest.mark.asyncio
    async def test_logs_track_count(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log the number of tracks found."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("src.app.full_sync.is_music_app_running", return_value=True),
            patch("src.app.full_sync.get_full_log_path", return_value=csv_path),
            patch("src.app.full_sync.sync_track_list_with_current", new_callable=AsyncMock),
            caplog.at_level(logging.INFO),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

            assert "Found 2 tracks" in caplog.text
            assert "synchronized: 2 tracks" in caplog.text
