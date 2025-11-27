"""Unit tests for database verifier module."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from src.app.features.verify.database_verifier import DatabaseVerifier
from src.core.models.track_models import TrackDict
from src.core.models.types import AppleScriptClientProtocol

if TYPE_CHECKING:
    from pathlib import Path


def _create_track(track_id: str, name: str = "Track") -> TrackDict:
    """Helper to create TrackDict for tests."""
    return TrackDict(id=track_id, name=name, artist="Artist", album="Album")


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create test console logger."""
    return logging.getLogger("test.console")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create test error logger."""
    return logging.getLogger("test.error")


@pytest.fixture
def mock_ap_client() -> Any:
    """Create mock AppleScript client."""
    client = create_autospec(AppleScriptClientProtocol, instance=True)
    client.run_script_code = AsyncMock(return_value="exists")
    return client


@pytest.fixture
def mock_analytics() -> Any:
    """Create mock analytics."""
    return MagicMock()


@pytest.fixture
def config(tmp_path: Path) -> dict[str, Any]:
    """Create test configuration."""
    return {
        "logs_base_dir": str(tmp_path / "logs"),
        "logging": {
            "last_incremental_run_file": "last_run.log",
            "csv_output_file": "track_list.csv",
        },
        "incremental_interval_minutes": 60,
        "verify_database": {
            "batch_size": 10,
            "pause_seconds": 0.1,
            "auto_verify_days": 7,
        },
    }


@pytest.fixture
def verifier(
    mock_ap_client: Any,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    mock_analytics: Any,
    config: dict[str, Any],
) -> DatabaseVerifier:
    """Create DatabaseVerifier instance."""
    return DatabaseVerifier(
        ap_client=mock_ap_client,
        console_logger=console_logger,
        error_logger=error_logger,
        analytics=mock_analytics,
        config=config,
    )


class TestDatabaseVerifierInit:
    """Tests for DatabaseVerifier initialization."""

    def test_init_stores_dependencies(
        self,
        mock_ap_client: Any,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_analytics: MagicMock,
        config: dict[str, Any],
    ) -> None:
        """Should store all dependencies correctly."""
        verifier = DatabaseVerifier(
            ap_client=mock_ap_client,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=mock_analytics,
            config=config,
        )

        assert verifier.ap_client is mock_ap_client
        assert verifier.console_logger is console_logger
        assert verifier.error_logger is error_logger
        assert verifier.analytics is mock_analytics
        assert verifier.config is config
        assert verifier.dry_run is False

    def test_init_with_dry_run(
        self,
        mock_ap_client: Any,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_analytics: MagicMock,
        config: dict[str, Any],
    ) -> None:
        """Should set dry_run flag when specified."""
        verifier = DatabaseVerifier(
            ap_client=mock_ap_client,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=mock_analytics,
            config=config,
            dry_run=True,
        )

        assert verifier.dry_run is True


class TestCanRunIncremental:
    """Tests for can_run_incremental method."""

    @pytest.mark.asyncio
    async def test_returns_true_when_force_run(
        self, verifier: DatabaseVerifier
    ) -> None:
        """Should return True when force_run is True."""
        result = await verifier.can_run_incremental(force_run=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_no_previous_run(
        self, verifier: DatabaseVerifier, tmp_path: Path
    ) -> None:
        """Should return True when no previous run file exists."""
        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(tmp_path / "nonexistent.log"),
        ):
            result = await verifier.can_run_incremental()

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_enough_time_passed(
        self, verifier: DatabaseVerifier, tmp_path: Path
    ) -> None:
        """Should return True when enough time has passed."""
        last_run_file = tmp_path / "last_run.log"
        old_time = datetime.now(tz=UTC) - timedelta(hours=2)
        last_run_file.write_text(old_time.isoformat())

        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(last_run_file),
        ):
            result = await verifier.can_run_incremental()

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_enough_time_passed(
        self, verifier: DatabaseVerifier, tmp_path: Path
    ) -> None:
        """Should return False when not enough time has passed."""
        last_run_file = tmp_path / "last_run.log"
        recent_time = datetime.now(tz=UTC) - timedelta(minutes=30)
        last_run_file.write_text(recent_time.isoformat())

        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(last_run_file),
        ):
            result = await verifier.can_run_incremental()

        assert result is False

    @pytest.mark.asyncio
    async def test_handles_future_timestamp(
        self, verifier: DatabaseVerifier, tmp_path: Path
    ) -> None:
        """Should return True when timestamp is in the future."""
        last_run_file = tmp_path / "last_run.log"
        future_time = datetime.now(tz=UTC) + timedelta(hours=1)
        last_run_file.write_text(future_time.isoformat())

        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(last_run_file),
        ):
            result = await verifier.can_run_incremental()

        assert result is True


class TestUpdateLastIncrementalRun:
    """Tests for update_last_incremental_run method."""

    @pytest.mark.asyncio
    async def test_updates_timestamp(
        self, verifier: DatabaseVerifier, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should update timestamp via IncrementalRunTracker."""
        with (
            patch(
                "src.app.features.verify.database_verifier.IncrementalRunTracker"
            ) as mock_tracker_class,
            caplog.at_level(logging.INFO),
        ):
            mock_tracker = MagicMock()
            mock_tracker.update_last_run_timestamp = AsyncMock()
            mock_tracker.get_last_run_file_path.return_value = "/path/to/file"
            mock_tracker_class.return_value = mock_tracker

            await verifier.update_last_incremental_run()

            mock_tracker.update_last_run_timestamp.assert_called_once()
            assert "Updated last incremental run timestamp" in caplog.text


class TestHandleInvalidTracks:
    """Tests for _handle_invalid_tracks method."""

    def test_logs_when_no_invalid_tracks(
        self, verifier: DatabaseVerifier, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should log success when no invalid tracks."""
        with caplog.at_level(logging.INFO):
            verifier._handle_invalid_tracks([], [], "/path/tracks.csv")

        assert "All tracks in database are valid" in caplog.text

    def test_removes_invalid_tracks_in_normal_mode(
        self, verifier: DatabaseVerifier
    ) -> None:
        """Should remove invalid tracks when not in dry run."""
        existing_tracks = [
            _create_track("1", "Track 1"),
            _create_track("2", "Track 2"),
        ]
        invalid_tracks = ["2"]

        with patch(
            "src.app.features.verify.database_verifier.save_to_csv"
        ) as mock_save:
            verifier._handle_invalid_tracks(
                invalid_tracks, existing_tracks, "/path/tracks.csv"
            )

            mock_save.assert_called_once()
            saved_tracks = mock_save.call_args[0][0]
            assert len(saved_tracks) == 1
            assert saved_tracks[0].id == "1"

    def test_records_action_in_dry_run(
        self,
        mock_ap_client: Any,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_analytics: Any,
        config: dict[str, Any],
    ) -> None:
        """Should record action in dry run mode."""
        verifier = DatabaseVerifier(
            ap_client=mock_ap_client,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=mock_analytics,
            config=config,
            dry_run=True,
        )

        existing_tracks = [_create_track("1"), _create_track("2")]
        invalid_tracks = ["2"]

        verifier._handle_invalid_tracks(
            invalid_tracks, existing_tracks, "/path/tracks.csv"
        )

        actions = verifier.get_dry_run_actions()
        assert len(actions) == 1
        assert actions[0]["action"] == "remove_invalid_tracks"
        assert actions[0]["count"] == 1


class TestGetDryRunActions:
    """Tests for get_dry_run_actions method."""

    def test_returns_empty_initially(self, verifier: DatabaseVerifier) -> None:
        """Should return empty list initially."""
        assert verifier.get_dry_run_actions() == []
