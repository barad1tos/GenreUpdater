"""Tests for YearUpdateService."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.year_update import YearUpdateService
from core.models.track_models import ChangeLogEntry, TrackDict
from tests.factories import create_test_app_config  # sourcery skip: dont-import-test-modules

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


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
def mock_config() -> AppConfig:
    """Create mock config."""
    return create_test_app_config(logs_base_dir="/tmp/logs")


@pytest.fixture
def service(
    mock_track_processor: MagicMock,
    mock_year_retriever: MagicMock,
    mock_snapshot_manager: MagicMock,
    mock_config: AppConfig,
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
        mock_config: AppConfig,
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
        mock_config: AppConfig,
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


class TestFormatRestoreTarget:
    """Tests for _format_restore_target static method."""

    def test_returns_all_artists_when_no_artist(self) -> None:
        """Should return 'for all artists' when artist is None."""
        result = YearUpdateService._format_restore_target(None, None)
        assert result == " for all artists"

    def test_returns_artist_only_when_no_album(self) -> None:
        """Should return artist name when album is None."""
        result = YearUpdateService._format_restore_target("Crematory", None)
        assert result == " for 'Crematory'"

    def test_returns_artist_and_album_when_both(self) -> None:
        """Should return artist and album when both provided."""
        result = YearUpdateService._format_restore_target("Crematory", "Awake")
        assert result == " for 'Crematory' - Awake"


class TestShouldRestoreTrack:
    """Tests for _should_restore_track static method."""

    @staticmethod
    def _assert_no_restore(track: TrackDict, threshold: int) -> None:
        """Assert that a track should NOT be restored."""
        should_restore, release_year_result = YearUpdateService._should_restore_track(track, threshold)
        assert should_restore is False
        assert release_year_result is None

    @staticmethod
    def _assert_should_restore(track: TrackDict, threshold: int, expected_year: str) -> None:
        """Assert that a track SHOULD be restored with expected year."""
        should_restore, release_year_result = YearUpdateService._should_restore_track(track, threshold)
        assert should_restore is True
        assert release_year_result == expected_year

    def test_returns_false_when_no_release_year(self) -> None:
        """Should return False when track has no release_year."""
        track = TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="2020")
        self._assert_no_restore(track, threshold=5)

    def test_returns_false_when_years_within_threshold(self) -> None:
        """Should return False when year difference is within threshold."""
        track = TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="2020", release_year="2022")
        self._assert_no_restore(track, threshold=5)

    def test_returns_true_when_years_exceed_threshold(self) -> None:
        """Should return True when year difference exceeds threshold."""
        track = TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="2025", release_year="1997")
        self._assert_should_restore(track, threshold=5, expected_year="1997")

    def test_returns_false_when_invalid_years(self) -> None:
        """Should return False when years cannot be parsed."""
        track = TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="invalid", release_year="also_invalid")
        self._assert_no_restore(track, threshold=5)

    def test_exact_threshold_does_not_trigger(self) -> None:
        """Should not restore when difference equals threshold exactly."""
        track = TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="2020", release_year="2015")
        self._assert_no_restore(track, threshold=5)


class TestFindAlbumsNeedingRestoration:
    """Tests for _find_albums_needing_restoration method."""

    def test_finds_albums_with_year_discrepancy(
        self,
        service: YearUpdateService,
    ) -> None:
        """Should find albums where year differs from release_year."""
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Crematory", album="Awake", genre="Metal", year="2025", release_year="1997"),
            TrackDict(id="2", name="Track 2", artist="Crematory", album="Awake", genre="Metal", year="2025", release_year="1997"),
        ]

        result = service._find_albums_needing_restoration(tracks, threshold=5)

        assert ("Crematory", "Awake") in result
        assert len(result[("Crematory", "Awake")]) == 2

    def test_ignores_albums_within_threshold(
        self,
        service: YearUpdateService,
    ) -> None:
        """Should ignore albums where year is close to release_year."""
        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="2020", release_year="2019"),
        ]

        result = service._find_albums_needing_restoration(tracks, threshold=5)

        assert len(result) == 0

    def test_ignores_tracks_without_release_year(
        self,
        service: YearUpdateService,
    ) -> None:
        """Should ignore tracks without release_year."""
        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="2025"),
        ]

        result = service._find_albums_needing_restoration(tracks, threshold=5)

        assert len(result) == 0

    def test_groups_tracks_by_album(
        self,
        service: YearUpdateService,
    ) -> None:
        """Should group tracks by artist-album key."""
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Artist1", album="Album1", genre="Rock", year="2025", release_year="1997"),
            TrackDict(id="2", name="Track 2", artist="Artist1", album="Album1", genre="Rock", year="2025", release_year="1997"),
            TrackDict(id="3", name="Track 3", artist="Artist2", album="Album2", genre="Pop", year="2025", release_year="2000"),
        ]

        result = service._find_albums_needing_restoration(tracks, threshold=5)

        assert len(result) == 2
        assert len(result[("Artist1", "Album1")]) == 2
        assert len(result[("Artist2", "Album2")]) == 1


class TestGetConsensusYear:
    """Tests for _get_consensus_year static method."""

    def test_returns_most_common_year(self) -> None:
        """Should return the most common release_year."""
        track_data: list[tuple[TrackDict, str]] = [
            (MagicMock(), "1997"),
            (MagicMock(), "1997"),
            (MagicMock(), "1998"),
        ]

        result = YearUpdateService._get_consensus_year(track_data)

        assert result == "1997"

    def test_returns_none_when_empty(self) -> None:
        """Should return None when no release years."""
        track_data: list[tuple[TrackDict, str]] = []

        result = YearUpdateService._get_consensus_year(track_data)

        assert result is None

    def test_returns_none_when_all_empty_strings(self) -> None:
        """Should return None when all release years are empty."""
        track_data: list[tuple[TrackDict, str]] = [
            (MagicMock(), ""),
            (MagicMock(), ""),
        ]

        result = YearUpdateService._get_consensus_year(track_data)

        assert result is None


class TestUpdateSingleTrackYear:
    """Tests for _update_single_track_year method."""

    @pytest.mark.asyncio
    async def test_returns_change_entry_on_success(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should return ChangeLogEntry on successful update."""
        track = TrackDict(id="123", name="Track Name", artist="Artist", album="Album", genre="Rock", year="2025")
        mock_track_processor.update_track_async = AsyncMock(return_value=True)

        result = await service._update_single_track_year(track, consensus_release_year="1997", artist="Artist", album="Album")

        assert result is not None
        assert result.track_id == "123"
        assert result.year_before_mgu == "2025"
        assert result.year_set_by_mgu == "1997"
        assert result.change_type == "year_restored_from_release_year"

    @pytest.mark.asyncio
    async def test_returns_none_when_update_fails(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should return None when update fails."""
        track = TrackDict(id="123", name="Track Name", artist="Artist", album="Album", genre="Rock", year="2025")
        mock_track_processor.update_track_async = AsyncMock(return_value=False)

        result = await service._update_single_track_year(track, consensus_release_year="1997", artist="Artist", album="Album")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_year_already_matches(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should return None when year already matches consensus."""
        track = TrackDict(id="123", name="Track Name", artist="Artist", album="Album", genre="Rock", year="1997")

        result = await service._update_single_track_year(track, consensus_release_year="1997", artist="Artist", album="Album")

        assert result is None
        mock_track_processor.update_track_async.assert_not_called()


class TestRunRestoreReleaseYears:
    """Tests for run_restore_release_years method."""

    @pytest.mark.asyncio
    async def test_returns_early_when_no_tracks(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should return early when no tracks found."""
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=[])

        with caplog.at_level(logging.WARNING):
            await service.run_restore_release_years()

        assert "No tracks found" in caplog.text

    @pytest.mark.asyncio
    async def test_returns_early_when_no_albums_need_restoration(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should return early when no albums need restoration."""
        # Tracks with matching years
        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="2020", release_year="2020"),
        ]
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=tracks)

        with caplog.at_level(logging.INFO):
            await service.run_restore_release_years()

        assert "No albums found needing year restoration" in caplog.text

    @pytest.mark.asyncio
    async def test_processes_albums_with_year_discrepancy(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should process albums with year discrepancy."""
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Crematory", album="Awake", genre="Metal", year="2025", release_year="1997"),
        ]
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=tracks)
        mock_track_processor.update_track_async = AsyncMock(return_value=True)

        with caplog.at_level(logging.INFO):
            await service.run_restore_release_years()

        assert "Found 1 albums needing year restoration" in caplog.text
        assert "Crematory - Awake" in caplog.text
        assert "1 tracks updated" in caplog.text

    @pytest.mark.asyncio
    async def test_filters_by_album_when_specified(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should filter tracks by album when specified."""
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Crematory", album="Awake", genre="Metal", year="2025", release_year="1997"),
            TrackDict(id="2", name="Track 2", artist="Crematory", album="Illusions", genre="Metal", year="2025", release_year="2000"),
        ]
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=tracks)
        mock_track_processor.update_track_async = AsyncMock(return_value=True)

        await service.run_restore_release_years(artist="Crematory", album="Awake")

        # Should only update the Awake track
        assert mock_track_processor.update_track_async.call_count == 1

    @pytest.mark.asyncio
    async def test_saves_changes_report(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should save changes report after restoration."""
        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="2025", release_year="1997"),
        ]
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=tracks)
        mock_track_processor.update_track_async = AsyncMock(return_value=True)

        with (
            patch("app.year_update.save_changes_report") as mock_save,
            patch("app.year_update.get_full_log_path", return_value="/tmp/restore.csv"),
        ):
            await service.run_restore_release_years()

            mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_counts_failed_updates(
        self,
        service: YearUpdateService,
        mock_track_processor: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should count failed updates."""
        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", genre="Rock", year="2025", release_year="1997"),
        ]
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=tracks)
        mock_track_processor.update_track_async = AsyncMock(return_value=False)

        with caplog.at_level(logging.INFO):
            await service.run_restore_release_years()

        assert "0 tracks updated, 1 failed" in caplog.text
