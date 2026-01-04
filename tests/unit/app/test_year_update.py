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

    def test_stores_optional_cleaning_service(
        self,
        mock_track_processor: MagicMock,
        mock_year_retriever: MagicMock,
        mock_snapshot_manager: MagicMock,
        mock_config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should store optional cleaning service."""
        mock_cleaning = MagicMock()
        service = YearUpdateService(
            track_processor=mock_track_processor,
            year_retriever=mock_year_retriever,
            snapshot_manager=mock_snapshot_manager,
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            cleaning_service=mock_cleaning,
        )
        assert service._cleaning_service is mock_cleaning

    def test_stores_optional_artist_renamer(
        self,
        mock_track_processor: MagicMock,
        mock_year_retriever: MagicMock,
        mock_snapshot_manager: MagicMock,
        mock_config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should store optional artist renamer."""
        mock_renamer = MagicMock()
        service = YearUpdateService(
            track_processor=mock_track_processor,
            year_retriever=mock_year_retriever,
            snapshot_manager=mock_snapshot_manager,
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            artist_renamer=mock_renamer,
        )
        assert service._artist_renamer is mock_renamer

    def test_test_artists_defaults_to_none(
        self,
        service: YearUpdateService,
    ) -> None:
        """Should default test_artists to None."""
        assert service._test_artists is None


class TestSetTestArtists:
    """Tests for set_test_artists method."""

    def test_set_test_artists_stores_set(
        self,
        service: YearUpdateService,
    ) -> None:
        """Should store test artists set."""
        test_artists = {"Artist1", "Artist2"}
        service.set_test_artists(test_artists)
        assert service._test_artists == test_artists

    def test_set_test_artists_can_set_none(
        self,
        service: YearUpdateService,
    ) -> None:
        """Should allow setting None to disable filtering."""
        service.set_test_artists({"Artist1"})
        service.set_test_artists(None)
        assert service._test_artists is None

    def test_set_test_artists_empty_set(
        self,
        service: YearUpdateService,
    ) -> None:
        """Should handle empty set."""
        service.set_test_artists(set())
        assert service._test_artists == set()


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
        """Should fetch all artists when artist is None using batch fetcher."""
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)

        await service.get_tracks_for_year_update(artist=None)

        mock_track_processor.fetch_tracks_in_batches.assert_called_once()

    @pytest.mark.asyncio
    async def test_filters_by_test_artists_when_set(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should filter tracks by test_artists when set."""
        all_tracks: list[TrackDict] = [
            TrackDict(id="1", name="Track 1", artist="Artist1", album="A", genre="Rock", year="2020"),
            TrackDict(id="2", name="Track 2", artist="Artist2", album="B", genre="Pop", year="2021"),
            TrackDict(id="3", name="Track 3", artist="OtherArtist", album="C", genre="Jazz", year="2019"),
        ]
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=all_tracks)

        service.set_test_artists({"Artist1", "Artist2"})
        result = await service.get_tracks_for_year_update(artist=None)

        assert result is not None
        assert len(result) == 2
        assert all(t.get("artist") in {"Artist1", "Artist2"} for t in result)

    @pytest.mark.asyncio
    async def test_does_not_filter_when_test_artists_none(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should not filter tracks when test_artists is None."""
        all_tracks: list[TrackDict] = [
            TrackDict(id="1", name="Track 1", artist="Artist1", album="A", genre="Rock", year="2020"),
            TrackDict(id="2", name="Track 2", artist="Artist2", album="B", genre="Pop", year="2021"),
        ]
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=all_tracks)

        service.set_test_artists(None)
        result = await service.get_tracks_for_year_update(artist=None)

        assert result is not None
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_test_artists_filters_all(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should return None when test_artists filters all tracks."""
        all_tracks: list[TrackDict] = [
            TrackDict(id="1", name="Track 1", artist="OtherArtist", album="A", genre="Rock", year="2020"),
        ]
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=all_tracks)

        service.set_test_artists({"NonExistent"})
        result = await service.get_tracks_for_year_update(artist=None)

        assert result is None


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
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)
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
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)
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
        """Should return early when no tracks found (specific artist)."""
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
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)
        mock_year_retriever.process_album_years = AsyncMock(return_value=True)

        await service.run_update_years(artist=None, force=True)

        mock_year_retriever.process_album_years.assert_called_once_with(sample_tracks, force=True, fresh=False)


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
                year_before_mgu="2019",
                year_set_by_mgu="2020",
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
