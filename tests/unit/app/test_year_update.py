"""Tests for YearUpdateService."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.year_update import YearUpdateService
from core.models.track_models import ChangeLogEntry, TrackDict


@pytest.fixture
def mock_track_processor() -> MagicMock:
    """Create mock track processor."""
    return MagicMock()


@pytest.fixture
def mock_year_retriever() -> MagicMock:
    """Create mock year retriever."""
    return MagicMock()


@pytest.fixture
def mock_snapshot_manager() -> MagicMock:
    """Create mock snapshot manager."""
    return MagicMock()


@pytest.fixture
def mock_config() -> dict[str, Any]:
    """Create mock config."""
    return {"logs_base_dir": "/tmp/logs"}


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create console logger."""
    return logging.getLogger("test.console")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create error logger."""
    return logging.getLogger("test.error")


@pytest.fixture
def service(
    mock_track_processor: MagicMock,
    mock_year_retriever: MagicMock,
    mock_snapshot_manager: MagicMock,
    mock_config: dict[str, Any],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> YearUpdateService:
    """Create YearUpdateService instance."""
    return YearUpdateService(
        track_processor=mock_track_processor,
        year_retriever=mock_year_retriever,
        snapshot_manager=mock_snapshot_manager,
        config=mock_config,
        console_logger=console_logger,
        error_logger=error_logger,
    )


@pytest.fixture
def sample_tracks() -> list[TrackDict]:
    """Create sample tracks."""
    return [
        TrackDict(
            id="1",
            name="Track 1",
            artist="Artist",
            album="Album",
            genre="Rock",
            year="2020",
        ),
        TrackDict(
            id="2",
            name="Track 2",
            artist="Artist",
            album="Album",
            genre="Rock",
            year="2020",
        ),
    ]


class TestYearUpdateServiceInit:
    """Tests for YearUpdateService initialization."""

    def test_stores_track_processor(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should store track processor."""
        assert service._track_processor is mock_track_processor

    def test_stores_year_retriever(
        self,
        service: YearUpdateService,
        mock_year_retriever: MagicMock,
    ) -> None:
        """Should store year retriever."""
        assert service._year_retriever is mock_year_retriever

    def test_stores_snapshot_manager(
        self,
        service: YearUpdateService,
        mock_snapshot_manager: MagicMock,
    ) -> None:
        """Should store snapshot manager."""
        assert service._snapshot_manager is mock_snapshot_manager


class TestGetTracksForYearUpdate:
    """Tests for get_tracks_for_year_update method."""

    @pytest.mark.asyncio
    async def test_returns_tracks_when_found(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should return tracks when found."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=sample_tracks)

        result = await service.get_tracks_for_year_update(artist="Artist")

        assert result == sample_tracks

    @pytest.mark.asyncio
    async def test_returns_none_when_no_tracks(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should return None when no tracks found."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=[])

        result = await service.get_tracks_for_year_update(artist="Unknown")

        assert result is None

    @pytest.mark.asyncio
    async def test_fetches_all_artists_when_none(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should fetch all artists when artist is None."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=sample_tracks)

        await service.get_tracks_for_year_update(artist=None)

        mock_track_processor.fetch_tracks_async.assert_called_once_with(artist=None)


class TestRunUpdateYears:
    """Tests for run_update_years method."""

    @pytest.mark.asyncio
    async def test_logs_success_when_completed(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        mock_year_retriever: MagicMock,
        sample_tracks: list[TrackDict],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log success when update completes."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=sample_tracks)
        mock_year_retriever.process_album_years = AsyncMock(return_value=True)

        with caplog.at_level(logging.INFO):
            await service.run_update_years(artist=None, force=False)

        assert "Year update operation completed successfully" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_error_when_failed(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        mock_year_retriever: MagicMock,
        sample_tracks: list[TrackDict],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log error when update fails."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=sample_tracks)
        mock_year_retriever.process_album_years = AsyncMock(return_value=False)

        with caplog.at_level(logging.ERROR):
            await service.run_update_years(artist=None, force=False)

        assert "Year update operation failed" in caplog.text

    @pytest.mark.asyncio
    async def test_returns_early_when_no_tracks(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        mock_year_retriever: MagicMock,
    ) -> None:
        """Should return early when no tracks found."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=[])

        await service.run_update_years(artist="Unknown", force=False)

        mock_year_retriever.process_album_years.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_force_flag(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        mock_year_retriever: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should pass force flag to year retriever."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=sample_tracks)
        mock_year_retriever.process_album_years = AsyncMock(return_value=True)

        await service.run_update_years(artist=None, force=True)

        mock_year_retriever.process_album_years.assert_called_once_with(sample_tracks, force=True)


class TestRunRevertYears:
    """Tests for run_revert_years method."""

    @pytest.mark.asyncio
    async def test_returns_early_when_no_targets(
        self,
        service: YearUpdateService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should return early when no revert targets found."""
        with (
            patch("app.year_update.repair_utils.build_revert_targets", return_value=[]),
            caplog.at_level(logging.WARNING),
        ):
            await service.run_revert_years(artist="Artist", album=None)

        assert "No revert targets found" in caplog.text

    @pytest.mark.asyncio
    async def test_applies_reverts_when_targets_found(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should apply reverts when targets found."""
        targets = [{"track_id": "1", "year": 2019}]

        with (
            patch("app.year_update.repair_utils.build_revert_targets", return_value=targets),
            patch("app.year_update.repair_utils.apply_year_reverts", new_callable=AsyncMock) as mock_apply,
        ):
            mock_apply.return_value = (1, 0, [])

            await service.run_revert_years(artist="Artist", album="Album")

            mock_apply.assert_called_once_with(
                track_processor=mock_track_processor,
                artist="Artist",
                targets=targets,
            )

    @pytest.mark.asyncio
    async def test_saves_changes_report_when_changes(
        self,
        service: YearUpdateService,
    ) -> None:
        """Should save changes report when changes exist."""
        targets = [{"track_id": "1", "year": 2019}]
        changes = [MagicMock()]

        with (
            patch("app.year_update.repair_utils.build_revert_targets", return_value=targets),
            patch("app.year_update.repair_utils.apply_year_reverts", new_callable=AsyncMock) as mock_apply,
            patch("app.year_update.save_changes_report") as mock_save,
            patch("app.year_update.get_full_log_path", return_value="/tmp/revert.csv"),
        ):
            mock_apply.return_value = (1, 0, changes)

            await service.run_revert_years(artist="Artist", album=None)

            mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_revert_completion(
        self,
        service: YearUpdateService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log revert completion stats."""
        targets = [{"track_id": "1", "year": 2019}]

        with (
            patch("app.year_update.repair_utils.build_revert_targets", return_value=targets),
            patch("app.year_update.repair_utils.apply_year_reverts", new_callable=AsyncMock) as mock_apply,
            caplog.at_level(logging.INFO),
        ):
            mock_apply.return_value = (5, 2, [])

            await service.run_revert_years(artist="Artist", album=None)

        assert "5 tracks updated" in caplog.text
        assert "2 not found" in caplog.text


class TestUpdateAllYears:
    """Tests for update_all_years method."""

    @pytest.mark.asyncio
    async def test_processes_album_years(
        self,
        service: YearUpdateService,
        mock_year_retriever: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should process album years."""
        mock_year_retriever.process_album_years = AsyncMock(return_value=True)
        mock_year_retriever.get_last_updated_tracks.return_value = sample_tracks

        await service.update_all_years(tracks=sample_tracks, force=False)

        mock_year_retriever.process_album_years.assert_called_once_with(sample_tracks, force=False)

    @pytest.mark.asyncio
    async def test_updates_snapshot(
        self,
        service: YearUpdateService,
        mock_year_retriever: MagicMock,
        mock_snapshot_manager: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should update snapshot with updated tracks."""
        updated_tracks = [sample_tracks[0]]
        mock_year_retriever.process_album_years = AsyncMock(return_value=True)
        mock_year_retriever.get_last_updated_tracks.return_value = updated_tracks

        await service.update_all_years(tracks=sample_tracks, force=False)

        mock_snapshot_manager.update_tracks.assert_called_once_with(updated_tracks)

    @pytest.mark.asyncio
    async def test_raises_on_error(
        self,
        service: YearUpdateService,
        mock_year_retriever: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should raise exception on error."""
        mock_year_retriever.process_album_years = AsyncMock(side_effect=ValueError("Test error"))

        with pytest.raises(ValueError, match="Test error"):
            await service.update_all_years(tracks=sample_tracks, force=False)


class TestUpdateAllYearsWithLogs:
    """Tests for update_all_years_with_logs method."""

    @pytest.mark.asyncio
    async def test_returns_change_logs(
        self,
        service: YearUpdateService,
        mock_year_retriever: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should return change logs."""
        changes = [
            ChangeLogEntry(
                timestamp="2024-01-01 12:00:00",
                change_type="year_update",
                track_id="1",
                artist="Artist",
                album_name="Album",
                track_name="Track 1",
                old_year="2019",
                new_year="2020",
            )
        ]
        mock_year_retriever.get_album_years_with_logs = AsyncMock(return_value=(sample_tracks, changes))

        result = await service.update_all_years_with_logs(tracks=sample_tracks, force=False)

        assert result == changes

    @pytest.mark.asyncio
    async def test_updates_last_updated_tracks(
        self,
        service: YearUpdateService,
        mock_year_retriever: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should set last updated tracks."""
        mock_year_retriever.get_album_years_with_logs = AsyncMock(return_value=(sample_tracks, []))

        await service.update_all_years_with_logs(tracks=sample_tracks, force=False)

        mock_year_retriever.set_last_updated_tracks.assert_called_once_with(sample_tracks)

    @pytest.mark.asyncio
    async def test_updates_snapshot_on_success(
        self,
        service: YearUpdateService,
        mock_year_retriever: MagicMock,
        mock_snapshot_manager: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should update snapshot on success."""
        mock_year_retriever.get_album_years_with_logs = AsyncMock(return_value=(sample_tracks, []))

        await service.update_all_years_with_logs(tracks=sample_tracks, force=False)

        mock_snapshot_manager.update_tracks.assert_called_once_with(sample_tracks)

    @pytest.mark.asyncio
    async def test_returns_error_entry_on_exception(
        self,
        service: YearUpdateService,
        mock_year_retriever: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should return error entry on exception."""
        mock_year_retriever.get_album_years_with_logs = AsyncMock(side_effect=RuntimeError("API failed"))

        result = await service.update_all_years_with_logs(tracks=sample_tracks, force=False)

        assert len(result) == 1
        assert result[0].change_type == "year_update_error"
        assert result[0].artist == "ERROR"
        assert "RuntimeError" in result[0].album_name
        assert "API failed" in result[0].track_name

    @pytest.mark.asyncio
    async def test_passes_force_flag(
        self,
        service: YearUpdateService,
        mock_year_retriever: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should pass force flag to year retriever."""
        mock_year_retriever.get_album_years_with_logs = AsyncMock(return_value=(sample_tracks, []))

        await service.update_all_years_with_logs(tracks=sample_tracks, force=True)

        mock_year_retriever.get_album_years_with_logs.assert_called_once_with(sample_tracks, force=True)
