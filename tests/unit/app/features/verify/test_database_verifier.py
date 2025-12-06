"""Unit tests for database verifier module."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from src.app.features.verify.database_verifier import DatabaseVerifier
from src.core.models.track_models import TrackDict
from src.core.models.types import AppleScriptClientProtocol


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
def db_verify_logger() -> logging.Logger:
    """Create test db_verify logger."""
    return logging.getLogger("test.db_verify")


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
    db_verify_logger: logging.Logger,
    mock_analytics: Any,
    config: dict[str, Any],
) -> DatabaseVerifier:
    """Create DatabaseVerifier instance."""
    return DatabaseVerifier(
        ap_client=mock_ap_client,
        console_logger=console_logger,
        error_logger=error_logger,
        db_verify_logger=db_verify_logger,
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
        db_verify_logger: logging.Logger,
        mock_analytics: MagicMock,
        config: dict[str, Any],
    ) -> None:
        """Should store all dependencies correctly."""
        verifier = DatabaseVerifier(
            ap_client=mock_ap_client,
            console_logger=console_logger,
            error_logger=error_logger,
            db_verify_logger=db_verify_logger,
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
        db_verify_logger: logging.Logger,
        mock_analytics: MagicMock,
        config: dict[str, Any],
    ) -> None:
        """Should set dry_run flag when specified."""
        verifier = DatabaseVerifier(
            ap_client=mock_ap_client,
            console_logger=console_logger,
            error_logger=error_logger,
            db_verify_logger=db_verify_logger,
            analytics=mock_analytics,
            config=config,
            dry_run=True,
        )

        assert verifier.dry_run is True


class TestCanRunIncremental:
    """Tests for can_run_incremental method."""

    @pytest.mark.asyncio
    async def test_returns_true_when_force_run(self, verifier: DatabaseVerifier) -> None:
        """Should return True when force_run is True."""
        result = await verifier.can_run_incremental(force_run=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_no_previous_run(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return True when no previous run file exists."""
        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(tmp_path / "nonexistent.log"),
        ):
            result = await verifier.can_run_incremental()

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_enough_time_passed(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
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
    async def test_returns_false_when_not_enough_time_passed(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
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
    async def test_handles_future_timestamp(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
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
    async def test_updates_timestamp(self, verifier: DatabaseVerifier, caplog: pytest.LogCaptureFixture) -> None:
        """Should update timestamp via IncrementalRunTracker."""
        with (
            patch("src.app.features.verify.database_verifier.IncrementalRunTracker") as mock_tracker_class,
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

    def test_logs_when_no_invalid_tracks(self, verifier: DatabaseVerifier, caplog: pytest.LogCaptureFixture) -> None:
        """Should log when no invalid tracks found."""
        with caplog.at_level(logging.INFO):
            verifier._handle_invalid_tracks([], [], "/path/tracks.csv")

        assert "INVALID_TRACKS | count=0" in caplog.text

    def test_removes_invalid_tracks_in_normal_mode(self, verifier: DatabaseVerifier) -> None:
        """Should remove invalid tracks when not in dry run."""
        existing_tracks = [
            _create_track("1", "Track 1"),
            _create_track("2", "Track 2"),
        ]
        invalid_tracks = ["2"]

        with patch("src.app.features.verify.database_verifier.save_to_csv") as mock_save:
            verifier._handle_invalid_tracks(invalid_tracks, existing_tracks, "/path/tracks.csv")

            mock_save.assert_called_once()
            saved_tracks = mock_save.call_args[0][0]
            assert len(saved_tracks) == 1
            assert saved_tracks[0].id == "1"

    def test_records_action_in_dry_run(
        self,
        mock_ap_client: Any,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        db_verify_logger: logging.Logger,
        mock_analytics: Any,
        config: dict[str, Any],
    ) -> None:
        """Should record action in dry run mode."""
        verifier = DatabaseVerifier(
            ap_client=mock_ap_client,
            console_logger=console_logger,
            error_logger=error_logger,
            db_verify_logger=db_verify_logger,
            analytics=mock_analytics,
            config=config,
            dry_run=True,
        )

        existing_tracks = [_create_track("1"), _create_track("2")]
        invalid_tracks = ["2"]

        verifier._handle_invalid_tracks(invalid_tracks, existing_tracks, "/path/tracks.csv")

        actions = verifier.get_dry_run_actions()
        assert len(actions) == 1
        assert actions[0]["action"] == "remove_invalid_tracks"
        assert actions[0]["count"] == 1


class TestGetDryRunActions:
    """Tests for get_dry_run_actions method."""

    def test_returns_empty_initially(self, verifier: DatabaseVerifier) -> None:
        """Should return empty list initially."""
        assert verifier.get_dry_run_actions() == []


class TestShouldAutoVerify:
    """Tests for should_auto_verify method."""

    @pytest.mark.asyncio
    async def test_returns_false_when_disabled(self, verifier: DatabaseVerifier) -> None:
        """Should return False when auto_verify_days is 0 or negative."""
        verifier.config["database_verification"] = {"auto_verify_days": 0}
        result = await verifier.should_auto_verify()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_no_previous_verification(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return True when no previous verification file exists."""
        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(tmp_path / "nonexistent.csv"),
        ):
            result = await verifier.should_auto_verify()

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_enough_days_passed(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return True when enough days have passed."""
        csv_file = tmp_path / "track_list.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "track_list_last_verify.txt"
        old_time = datetime.now(tz=UTC) - timedelta(days=10)
        last_verify_file.write_text(old_time.isoformat())

        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(csv_file),
        ):
            result = await verifier.should_auto_verify()

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_recently_verified(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return False when recently verified."""
        csv_file = tmp_path / "track_list.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "track_list_last_verify.txt"
        recent_time = datetime.now(tz=UTC) - timedelta(days=2)
        last_verify_file.write_text(recent_time.isoformat())

        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(csv_file),
        ):
            result = await verifier.should_auto_verify()

        assert result is False

    @pytest.mark.asyncio
    async def test_handles_file_read_error(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return True when file read fails."""
        csv_file = tmp_path / "track_list.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "track_list_last_verify.txt"
        last_verify_file.write_text("invalid-datetime-format")

        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(csv_file),
        ):
            result = await verifier.should_auto_verify()

        assert result is True


class TestVerifyTrackExists:
    """Tests for _verify_track_exists method."""

    @pytest.mark.asyncio
    async def test_returns_true_when_exists(self, verifier: DatabaseVerifier) -> None:
        """Should return True when AppleScript returns 'exists'."""
        verifier.ap_client.run_script_code = AsyncMock(return_value="exists")
        result = await verifier._verify_track_exists("123")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self, verifier: DatabaseVerifier) -> None:
        """Should return False when AppleScript returns 'not_found'."""
        verifier.ap_client.run_script_code = AsyncMock(return_value="not_found")
        result = await verifier._verify_track_exists("123")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_error_assume_exists(self, verifier: DatabaseVerifier) -> None:
        """Should return True when AppleScript returns 'error_assume_exists'."""
        verifier.ap_client.run_script_code = AsyncMock(return_value="error_assume_exists")
        result = await verifier._verify_track_exists("123")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_on_none_result(self, verifier: DatabaseVerifier) -> None:
        """Should return True when AppleScript returns None."""
        verifier.ap_client.run_script_code = AsyncMock(return_value=None)
        result = await verifier._verify_track_exists("123")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_on_exception(self, verifier: DatabaseVerifier) -> None:
        """Should return True when AppleScript raises exception."""
        verifier.ap_client.run_script_code = AsyncMock(side_effect=OSError("AppleScript failed"))
        result = await verifier._verify_track_exists("123")
        assert result is True

    @pytest.mark.asyncio
    async def test_raises_on_non_numeric_track_id(self, verifier: DatabaseVerifier) -> None:
        """Should raise ValueError for non-numeric track_id."""
        with pytest.raises(ValueError, match="track_id must be numeric"):
            await verifier._verify_track_exists("abc")

    @pytest.mark.asyncio
    async def test_converts_non_string_result_to_string(self, verifier: DatabaseVerifier) -> None:
        """Should handle non-string AppleScript result."""
        verifier.ap_client.run_script_code = AsyncMock(return_value=123)
        result = await verifier._verify_track_exists("456")
        # Non-"exists" and non-"not_found" returns True
        assert result is True


class TestShouldSkipVerification:
    """Tests for _should_skip_verification method."""

    @pytest.mark.asyncio
    async def test_returns_false_when_force(self, verifier: DatabaseVerifier) -> None:
        """Should return False when force is True."""
        result = await verifier._should_skip_verification(force=True, csv_path="/any/path.csv", auto_verify_days=7)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_last_verify_file(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return False when no last verify file exists."""
        csv_path = str(tmp_path / "tracks.csv")
        result = await verifier._should_skip_verification(force=False, csv_path=csv_path, auto_verify_days=7)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_recently_verified(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return True when recently verified."""
        csv_file = tmp_path / "tracks.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "tracks_last_verify.txt"
        recent_time = datetime.now(tz=UTC) - timedelta(days=2)
        last_verify_file.write_text(recent_time.isoformat())

        result = await verifier._should_skip_verification(force=False, csv_path=str(csv_file), auto_verify_days=7)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_threshold_exceeded(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return False when verification threshold exceeded."""
        csv_file = tmp_path / "tracks.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "tracks_last_verify.txt"
        old_time = datetime.now(tz=UTC) - timedelta(days=10)
        last_verify_file.write_text(old_time.isoformat())

        result = await verifier._should_skip_verification(force=False, csv_path=str(csv_file), auto_verify_days=7)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_read_error(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return False when file read fails."""
        csv_file = tmp_path / "tracks.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "tracks_last_verify.txt"
        last_verify_file.write_text("invalid-datetime")

        result = await verifier._should_skip_verification(force=False, csv_path=str(csv_file), auto_verify_days=7)
        assert result is False


class TestGetTracksToVerify:
    """Tests for _get_tracks_to_verify method."""

    def test_returns_all_tracks_by_default(self, verifier: DatabaseVerifier) -> None:
        """Should return all tracks when no filter applied."""
        tracks = [_create_track("1"), _create_track("2")]
        result = verifier._get_tracks_to_verify(tracks, apply_test_filter=False)
        assert len(result) == 2

    def test_filters_by_test_artists_in_dry_run(
        self,
        mock_ap_client: Any,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        db_verify_logger: logging.Logger,
        mock_analytics: Any,
        config: dict[str, Any],
    ) -> None:
        """Should filter by test_artists when in dry_run mode."""
        config["test_artists"] = ["Artist1"]
        verifier = DatabaseVerifier(
            ap_client=mock_ap_client,
            console_logger=console_logger,
            error_logger=error_logger,
            db_verify_logger=db_verify_logger,
            analytics=mock_analytics,
            config=config,
            dry_run=True,
        )

        track1 = TrackDict(id="1", name="Track1", artist="Artist1", album="Album")
        track2 = TrackDict(id="2", name="Track2", artist="Artist2", album="Album")
        tracks = [track1, track2]

        result = verifier._get_tracks_to_verify(tracks, apply_test_filter=True)
        assert len(result) == 1
        assert result[0].id == "1"


class TestVerifyTracksInBatches:
    """Tests for _verify_tracks_in_batches method."""

    @pytest.mark.asyncio
    async def test_returns_invalid_track_ids(self, verifier: DatabaseVerifier) -> None:
        """Should return list of invalid track IDs."""
        tracks = [_create_track("1"), _create_track("2"), _create_track("3")]
        verifier.ap_client.run_script_code = AsyncMock(side_effect=["exists", "not_found", "exists"])

        result = await verifier._verify_tracks_in_batches(tracks, {"batch_size": 10, "pause_seconds": 0})

        assert result == ["2"]

    @pytest.mark.asyncio
    async def test_processes_in_batches(self, verifier: DatabaseVerifier) -> None:
        """Should process tracks in batches."""
        tracks = [_create_track(str(i)) for i in range(5)]
        verifier.ap_client.run_script_code = AsyncMock(return_value="exists")

        with patch.object(verifier, "_log_batch_progress") as mock_log:
            await verifier._verify_tracks_in_batches(tracks, {"batch_size": 2, "pause_seconds": 0})

        # 5 tracks / 2 batch_size = 3 batches
        assert mock_log.call_count == 3


class TestUpdateVerificationTimestamp:
    """Tests for _update_verification_timestamp method."""

    @pytest.mark.asyncio
    async def test_skips_in_dry_run(
        self,
        mock_ap_client: Any,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        db_verify_logger: logging.Logger,
        mock_analytics: Any,
        config: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """Should skip updating timestamp in dry run mode."""
        verifier = DatabaseVerifier(
            ap_client=mock_ap_client,
            console_logger=console_logger,
            error_logger=error_logger,
            db_verify_logger=db_verify_logger,
            analytics=mock_analytics,
            config=config,
            dry_run=True,
        )

        csv_path = str(tmp_path / "tracks.csv")
        last_verify_file = tmp_path / "tracks_last_verify.txt"

        await verifier._update_verification_timestamp(csv_path)

        assert not last_verify_file.exists()

    @pytest.mark.asyncio
    async def test_writes_timestamp(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should write current timestamp to file."""
        csv_path = str(tmp_path / "tracks.csv")
        last_verify_file = tmp_path / "tracks_last_verify.txt"

        await verifier._update_verification_timestamp(csv_path)

        assert last_verify_file.exists()
        content = last_verify_file.read_text()
        # Verify it's a valid ISO datetime
        parsed = datetime.fromisoformat(content)
        assert parsed.tzinfo is not None


class TestVerifyAndCleanTrackDatabase:
    """Tests for verify_and_clean_track_database method."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_tracks(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return 0 when no tracks to verify."""
        csv_path = str(tmp_path / "tracks.csv")

        with (
            patch(
                "src.app.features.verify.database_verifier.get_full_log_path",
                return_value=csv_path,
            ),
            patch(
                "src.app.features.verify.database_verifier.load_track_list",
                return_value={},
            ),
        ):
            result = await verifier.verify_and_clean_track_database()

        assert result == 0

    @pytest.mark.asyncio
    async def test_skips_when_recently_verified(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should skip when recently verified."""
        csv_file = tmp_path / "tracks.csv"
        csv_file.write_text("")
        last_verify_file = tmp_path / "tracks_last_verify.txt"
        recent_time = datetime.now(tz=UTC) - timedelta(days=1)
        last_verify_file.write_text(recent_time.isoformat())

        tracks = {"1": _create_track("1")}

        with (
            patch(
                "src.app.features.verify.database_verifier.get_full_log_path",
                return_value=str(csv_file),
            ),
            patch(
                "src.app.features.verify.database_verifier.load_track_list",
                return_value=tracks,
            ),
        ):
            result = await verifier.verify_and_clean_track_database()

        assert result == 0

    @pytest.mark.asyncio
    async def test_verifies_and_returns_invalid_count(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should verify tracks and return invalid count."""
        csv_file = tmp_path / "tracks.csv"
        csv_file.write_text("")

        tracks = {"1": _create_track("1"), "2": _create_track("2")}
        verifier.ap_client.run_script_code = AsyncMock(side_effect=["exists", "not_found"])

        with (
            patch(
                "src.app.features.verify.database_verifier.get_full_log_path",
                return_value=str(csv_file),
            ),
            patch(
                "src.app.features.verify.database_verifier.load_track_list",
                return_value=tracks,
            ),
            patch("src.app.features.verify.database_verifier.save_to_csv"),
        ):
            result = await verifier.verify_and_clean_track_database(force=True)

        assert result == 1


class TestCanRunIncrementalLegacyFormats:
    """Tests for can_run_incremental with legacy datetime formats."""

    @pytest.mark.asyncio
    async def test_handles_legacy_datetime_format(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should handle legacy YYYY-MM-DD HH:MM:SS format."""
        last_run_file = tmp_path / "last_run.log"
        old_time = datetime.now(tz=UTC) - timedelta(hours=2)
        last_run_file.write_text(old_time.strftime("%Y-%m-%d %H:%M:%S"))

        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(last_run_file),
        ):
            result = await verifier.can_run_incremental()

        assert result is True

    @pytest.mark.asyncio
    async def test_handles_date_only_format(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should handle date-only YYYY-MM-DD format."""
        last_run_file = tmp_path / "last_run.log"
        old_date = (datetime.now(tz=UTC) - timedelta(days=2)).strftime("%Y-%m-%d")
        last_run_file.write_text(old_date)

        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(last_run_file),
        ):
            result = await verifier.can_run_incremental()

        assert result is True

    @pytest.mark.asyncio
    async def test_handles_invalid_format_gracefully(self, verifier: DatabaseVerifier, tmp_path: Path) -> None:
        """Should return True on invalid datetime format."""
        last_run_file = tmp_path / "last_run.log"
        last_run_file.write_text("not-a-valid-datetime-at-all")

        with patch(
            "src.app.features.verify.database_verifier.get_full_log_path",
            return_value=str(last_run_file),
        ):
            result = await verifier.can_run_incremental()

        # On error, should return True to allow run
        assert result is True


class TestLogMethods:
    """Tests for logging methods."""

    def test_log_verify_start(self, verifier: DatabaseVerifier, caplog: pytest.LogCaptureFixture) -> None:
        """Should log verification start."""
        with caplog.at_level(logging.INFO):
            verifier._log_verify_start(100)

        assert "DB_VERIFY" in caplog.text or "tracks" in caplog.text.lower()

    def test_log_batch_progress(self, verifier: DatabaseVerifier, caplog: pytest.LogCaptureFixture) -> None:
        """Should log batch progress."""
        with caplog.at_level(logging.INFO):
            verifier._log_batch_progress(batch_num=1, total_batches=5, checked=20, valid=18, invalid=2)

        # Verify some progress was logged
        assert len(caplog.records) > 0

    def test_log_verify_complete(self, verifier: DatabaseVerifier, caplog: pytest.LogCaptureFixture) -> None:
        """Should log verification complete."""
        import time

        verifier._verify_start_time = time.time() - 10

        with caplog.at_level(logging.INFO):
            verifier._log_verify_complete(total=100, invalid=5, removed=5)

        assert "DONE" in caplog.text or "done" in caplog.text.lower()
