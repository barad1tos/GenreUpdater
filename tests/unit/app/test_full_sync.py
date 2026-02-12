"""Unit tests for full sync module."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.full_sync import main, run_full_resync
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from pathlib import Path

    from core.models.track_models import AppConfig


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    """Create test configuration."""
    return create_test_app_config(
        logs_base_dir=str(tmp_path / "logs"),
        logging={
            "max_runs": 3,
            "main_log_file": "test.log",
            "analytics_log_file": "analytics.log",
            "csv_output_file": "track_list.csv",
            "changes_report_file": "changes.json",
            "dry_run_report_file": "dryrun.json",
            "last_incremental_run_file": "lastrun.json",
            "pending_verification_file": "pending.json",
            "last_db_verify_log": "dbverify.log",
            "levels": {
                "console": "INFO",
                "main_file": "INFO",
                "analytics_file": "INFO",
            },
        },
    )


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
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should exit early when Music.app is not running."""
        with patch("app.full_sync.is_music_app_running", return_value=False) as mock_check:
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
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should exit early when no tracks found in Music.app."""
        mock_track_processor.fetch_tracks_async.return_value = []

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.sync_track_list_with_current") as mock_sync,
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
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should perform full sync when tracks are found."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.get_full_log_path", return_value=csv_path),
            patch("app.full_sync.sync_track_list_with_current", new_callable=AsyncMock) as mock_sync,
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
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should raise OSError when sync fails."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.get_full_log_path", return_value=csv_path),
            patch(
                "app.full_sync.sync_track_list_with_current",
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
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should raise RuntimeError when sync fails."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.get_full_log_path", return_value=csv_path),
            patch(
                "app.full_sync.sync_track_list_with_current",
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
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log the number of tracks found."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.get_full_log_path", return_value=csv_path),
            patch("app.full_sync.sync_track_list_with_current", new_callable=AsyncMock),
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
            assert "Full resync completed: 2 tracks" in caplog.text

    @pytest.mark.asyncio
    async def test_raises_on_value_error(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should raise ValueError when sync fails."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.get_full_log_path", return_value=csv_path),
            patch(
                "app.full_sync.sync_track_list_with_current",
                new_callable=AsyncMock,
                side_effect=ValueError("Invalid config"),
            ),
            pytest.raises(ValueError, match="Invalid config"),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

    @pytest.mark.asyncio
    async def test_logs_error_on_exception(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log error when exception occurs."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.get_full_log_path", return_value=csv_path),
            patch(
                "app.full_sync.sync_track_list_with_current",
                new_callable=AsyncMock,
                side_effect=OSError("Test error"),
            ),
            caplog.at_level(logging.ERROR),
            pytest.raises(OSError),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

        assert "Full resync failed" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_music_app_running(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log when Music.app is running."""
        mock_track_processor.fetch_tracks_async.return_value = []

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            caplog.at_level(logging.INFO),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

        assert "Music.app is running" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_starting_message(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log starting message."""
        with (
            patch("app.full_sync.is_music_app_running", return_value=False),
            caplog.at_level(logging.INFO),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

        assert "Starting full media library resync" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_csv_path(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log CSV path when synchronizing."""
        csv_path = str(tmp_path / "track_list.csv")

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.get_full_log_path", return_value=csv_path),
            patch("app.full_sync.sync_track_list_with_current", new_callable=AsyncMock),
            caplog.at_level(logging.INFO),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

        assert "Synchronizing with database" in caplog.text

    @pytest.mark.asyncio
    async def test_passes_applescript_client(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should pass applescript_client to sync function."""
        csv_path = str(tmp_path / "track_list.csv")
        mock_ap_client = MagicMock()
        mock_track_processor.ap_client = mock_ap_client

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.get_full_log_path", return_value=csv_path),
            patch("app.full_sync.sync_track_list_with_current", new_callable=AsyncMock) as mock_sync,
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

        assert mock_sync.call_args[1]["applescript_client"] is mock_ap_client

    @pytest.mark.asyncio
    async def test_creates_csv_directory(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
        mock_cache_service: MagicMock,
        mock_track_processor: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should create CSV directory if not exists."""
        csv_path = str(tmp_path / "new_dir" / "track_list.csv")

        with (
            patch("app.full_sync.is_music_app_running", return_value=True),
            patch("app.full_sync.get_full_log_path", return_value=csv_path),
            patch("app.full_sync.sync_track_list_with_current", new_callable=AsyncMock),
        ):
            await run_full_resync(
                console_logger,
                error_logger,
                config,
                mock_cache_service,
                mock_track_processor,
            )

        assert (tmp_path / "new_dir").exists()


class TestMain:
    """Tests for main function."""

    @pytest.mark.asyncio
    async def test_exits_when_config_not_found(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Should exit when config file not found."""
        with (
            patch("app.full_sync.project_root", tmp_path),
            pytest.raises(SystemExit) as exc_info,
        ):
            await main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Configuration file not found" in captured.out

    @pytest.mark.asyncio
    async def test_uses_config_yaml(
        self,
        tmp_path: Path,
    ) -> None:
        """Should use config.yaml when found."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock()
        mock_deps.close = AsyncMock()

        mock_deps.cache_service = MagicMock()

        mock_updater = MagicMock()
        mock_updater.track_processor = MagicMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), None)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            patch("app.full_sync.MusicUpdater", return_value=mock_updater),
            patch("app.full_sync.run_full_resync", new_callable=AsyncMock),
        ):
            await main()

        mock_deps.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_prints_both_configs_message(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Should print message when both config files found."""
        (tmp_path / "config.yaml").write_text("logs_base_dir: /tmp")
        (tmp_path / "my-config.yaml").write_text("logs_base_dir: /tmp2")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock()
        mock_deps.close = AsyncMock()

        mock_deps.cache_service = MagicMock()

        mock_updater = MagicMock()
        mock_updater.track_processor = MagicMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), None)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            patch("app.full_sync.MusicUpdater", return_value=mock_updater),
            patch("app.full_sync.run_full_resync", new_callable=AsyncMock),
        ):
            await main()

        captured = capsys.readouterr()
        assert "Both" in captured.out
        assert "config.yaml" in captured.out

    @pytest.mark.asyncio
    async def test_handles_keyboard_interrupt(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Should handle keyboard interrupt gracefully."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock(side_effect=KeyboardInterrupt())
        mock_deps.close = AsyncMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), None)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            pytest.raises(SystemExit) as exc_info,
        ):
            await main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "cancelled by user" in captured.out

    @pytest.mark.asyncio
    async def test_handles_runtime_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Should handle runtime errors."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock(side_effect=RuntimeError("Test runtime error"))
        mock_deps.close = AsyncMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), None)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            pytest.raises(SystemExit) as exc_info,
        ):
            await main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Resync failed" in captured.out

    @pytest.mark.asyncio
    async def test_handles_os_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Should handle OS errors."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock(side_effect=OSError("Disk error"))
        mock_deps.close = AsyncMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), None)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            pytest.raises(SystemExit) as exc_info,
        ):
            await main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Resync failed" in captured.out

    @pytest.mark.asyncio
    async def test_handles_value_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Should handle value errors."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock(side_effect=ValueError("Bad value"))
        mock_deps.close = AsyncMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), None)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            pytest.raises(SystemExit) as exc_info,
        ):
            await main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Resync failed" in captured.out

    @pytest.mark.asyncio
    async def test_closes_deps_on_success(
        self,
        tmp_path: Path,
    ) -> None:
        """Should close dependency container on success."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock()
        mock_deps.close = AsyncMock()

        mock_deps.cache_service = MagicMock()

        mock_updater = MagicMock()
        mock_updater.track_processor = MagicMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), None)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            patch("app.full_sync.MusicUpdater", return_value=mock_updater),
            patch("app.full_sync.run_full_resync", new_callable=AsyncMock),
        ):
            await main()

        mock_deps.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stops_listener_on_cleanup(
        self,
        tmp_path: Path,
    ) -> None:
        """Should stop listener on cleanup."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock()
        mock_deps.close = AsyncMock()

        mock_deps.cache_service = MagicMock()

        mock_updater = MagicMock()
        mock_updater.track_processor = MagicMock()

        mock_listener = MagicMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), mock_listener)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            patch("app.full_sync.MusicUpdater", return_value=mock_updater),
            patch("app.full_sync.run_full_resync", new_callable=AsyncMock),
        ):
            await main()

        mock_listener.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_listener_stop_exception(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Should handle exception during listener.stop()."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock()
        mock_deps.close = AsyncMock()

        mock_deps.cache_service = MagicMock()

        mock_updater = MagicMock()
        mock_updater.track_processor = MagicMock()

        mock_listener = MagicMock()
        mock_listener.stop.side_effect = RuntimeError("Listener error")

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), mock_listener)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            patch("app.full_sync.MusicUpdater", return_value=mock_updater),
            patch("app.full_sync.run_full_resync", new_callable=AsyncMock),
        ):
            await main()

        captured = capsys.readouterr()
        assert "Exception during listener.stop()" in captured.out

    @pytest.mark.asyncio
    async def test_prints_header(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Should print header on start."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock()
        mock_deps.close = AsyncMock()

        mock_deps.cache_service = MagicMock()

        mock_updater = MagicMock()
        mock_updater.track_processor = MagicMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), None)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            patch("app.full_sync.MusicUpdater", return_value=mock_updater),
            patch("app.full_sync.run_full_resync", new_callable=AsyncMock),
        ):
            await main()

        captured = capsys.readouterr()
        assert "Music Genre Updater" in captured.out
        assert "Full Media Library Resync" in captured.out

    @pytest.mark.asyncio
    async def test_prints_completion_message(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Should print completion message."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logs_base_dir: /tmp")

        mock_deps = MagicMock()
        mock_deps.initialize = AsyncMock()
        mock_deps.close = AsyncMock()

        mock_deps.cache_service = MagicMock()

        mock_updater = MagicMock()
        mock_updater.track_processor = MagicMock()

        with (
            patch("app.full_sync.project_root", tmp_path),
            patch("app.full_sync.load_config", return_value={"logs_base_dir": "/tmp"}),
            patch("app.full_sync.get_loggers", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), None)),
            patch("app.full_sync.DependencyContainer", return_value=mock_deps),
            patch("app.full_sync.MusicUpdater", return_value=mock_updater),
            patch("app.full_sync.run_full_resync", new_callable=AsyncMock),
        ):
            await main()

        captured = capsys.readouterr()
        assert "Full resync completed" in captured.out
        assert "track_list.csv is now synchronized" in captured.out
