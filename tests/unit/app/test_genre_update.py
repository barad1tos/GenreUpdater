"""Tests for GenreUpdateService."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.genre_update import GenreUpdateService
from core.models.track_models import TrackDict
from tests.factories import create_test_app_config  # sourcery skip: dont-import-test-modules

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


@pytest.fixture
def mock_track_processor() -> MagicMock:
    """Create mock track processor."""
    return MagicMock()


@pytest.fixture
def mock_genre_manager() -> MagicMock:
    """Create mock genre manager."""
    return MagicMock()


@pytest.fixture
def mock_config() -> AppConfig:
    """Create mock config."""
    return create_test_app_config()


@pytest.fixture
def service(
    mock_track_processor: MagicMock,
    mock_genre_manager: MagicMock,
    mock_config: AppConfig,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> GenreUpdateService:
    """Create GenreUpdateService instance."""
    return GenreUpdateService(
        track_processor=mock_track_processor,
        genre_manager=mock_genre_manager,
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
            genre="Pop",
            year="2021",
        ),
    ]


class TestGenreUpdateServiceInit:
    """Tests for GenreUpdateService initialization."""

    def test_stores_track_processor(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should store track processor."""
        assert service._track_processor is mock_track_processor

    def test_stores_genre_manager(
        self,
        service: GenreUpdateService,
        mock_genre_manager: MagicMock,
    ) -> None:
        """Should store genre manager."""
        assert service._genre_manager is mock_genre_manager

    def test_stores_config(
        self,
        service: GenreUpdateService,
        mock_config: AppConfig,
    ) -> None:
        """Should store config."""
        assert service._config is mock_config

    def test_stores_optional_cleaning_service(
        self,
        mock_track_processor: MagicMock,
        mock_genre_manager: MagicMock,
        mock_config: AppConfig,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should store optional cleaning service."""
        mock_cleaning = MagicMock()
        service = GenreUpdateService(
            track_processor=mock_track_processor,
            genre_manager=mock_genre_manager,
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            cleaning_service=mock_cleaning,
        )
        assert service._cleaning_service is mock_cleaning

    def test_stores_optional_artist_renamer(
        self,
        mock_track_processor: MagicMock,
        mock_genre_manager: MagicMock,
        mock_config: AppConfig,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should store optional artist renamer."""
        mock_renamer = MagicMock()
        service = GenreUpdateService(
            track_processor=mock_track_processor,
            genre_manager=mock_genre_manager,
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            artist_renamer=mock_renamer,
        )
        assert service._artist_renamer is mock_renamer

    def test_test_artists_defaults_to_none(
        self,
        service: GenreUpdateService,
    ) -> None:
        """Should default test_artists to None."""
        assert service._test_artists is None


class TestSetTestArtists:
    """Tests for set_test_artists method."""

    def test_set_test_artists_stores_set(
        self,
        service: GenreUpdateService,
    ) -> None:
        """Should store test artists set."""
        test_artists = {"Artist1", "Artist2"}
        service.set_test_artists(test_artists)
        assert service._test_artists == test_artists

    def test_set_test_artists_can_set_none(
        self,
        service: GenreUpdateService,
    ) -> None:
        """Should allow setting None to disable filtering."""
        service.set_test_artists({"Artist1"})
        service.set_test_artists(None)
        assert service._test_artists is None

    def test_set_test_artists_empty_set(
        self,
        service: GenreUpdateService,
    ) -> None:
        """Should handle empty set."""
        service.set_test_artists(set())
        assert service._test_artists == set()


class TestGetTracksForGenreUpdate:
    """Tests for get_tracks_for_genre_update method."""

    @pytest.mark.asyncio
    async def test_returns_tracks_when_found(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should return tracks when found."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=sample_tracks)

        result = await service.get_tracks_for_genre_update(artist="Artist")

        assert result == sample_tracks

    @pytest.mark.asyncio
    async def test_returns_none_when_no_tracks(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should return None when no tracks found."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=[])

        result = await service.get_tracks_for_genre_update(artist="Unknown")

        assert result is None

    @pytest.mark.asyncio
    async def test_fetches_all_artists_when_none(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should fetch all artists when artist is None using batch fetcher."""
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)

        await service.get_tracks_for_genre_update(artist=None)

        mock_track_processor.fetch_tracks_in_batches.assert_called_once()

    @pytest.mark.asyncio
    async def test_filters_by_test_artists_when_set(
        self,
        service: GenreUpdateService,
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
        result = await service.get_tracks_for_genre_update(artist=None)

        assert result is not None
        assert len(result) == 2
        assert all(t.get("artist") in {"Artist1", "Artist2"} for t in result)

    @pytest.mark.asyncio
    async def test_does_not_filter_when_test_artists_none(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should not filter tracks when test_artists is None."""
        all_tracks: list[TrackDict] = [
            TrackDict(id="1", name="Track 1", artist="Artist1", album="A", genre="Rock", year="2020"),
            TrackDict(id="2", name="Track 2", artist="Artist2", album="B", genre="Pop", year="2021"),
        ]
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=all_tracks)

        service.set_test_artists(None)
        result = await service.get_tracks_for_genre_update(artist=None)

        assert result is not None
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_test_artists_filters_all(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
    ) -> None:
        """Should return None when test_artists filters all tracks."""
        all_tracks: list[TrackDict] = [
            TrackDict(id="1", name="Track 1", artist="OtherArtist", album="A", genre="Rock", year="2020"),
        ]
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=all_tracks)

        service.set_test_artists({"NonExistent"})
        result = await service.get_tracks_for_genre_update(artist=None)

        assert result is None


class TestRunUpdateGenres:
    """Tests for run_update_genres method."""

    @pytest.mark.asyncio
    async def test_logs_completion_when_successful(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
        mock_genre_manager: MagicMock,
        sample_tracks: list[TrackDict],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log completion when update completes."""
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)
        mock_genre_manager.update_genres_by_artist_async = AsyncMock()

        with caplog.at_level(logging.INFO):
            await service.run_update_genres(artist=None, force=False)

        assert "Genre update operation completed" in caplog.text

    @pytest.mark.asyncio
    async def test_returns_early_when_no_tracks(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
        mock_genre_manager: MagicMock,
    ) -> None:
        """Should return early when no tracks found."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=[])

        await service.run_update_genres(artist="Unknown", force=False)

        mock_genre_manager.update_genres_by_artist_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_force_flag(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
        mock_genre_manager: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should pass force flag to genre manager."""
        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)
        mock_genre_manager.update_genres_by_artist_async = AsyncMock()

        await service.run_update_genres(artist=None, force=True)

        mock_genre_manager.update_genres_by_artist_async.assert_called_once_with(
            sample_tracks,
            force=True,
        )

    @pytest.mark.asyncio
    async def test_calls_cleaning_service_when_provided(
        self,
        mock_track_processor: MagicMock,
        mock_genre_manager: MagicMock,
        mock_config: AppConfig,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should call cleaning service when provided."""
        mock_cleaning = MagicMock()
        mock_cleaning.clean_all_metadata_with_logs = AsyncMock()

        service = GenreUpdateService(
            track_processor=mock_track_processor,
            genre_manager=mock_genre_manager,
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            cleaning_service=mock_cleaning,
        )

        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)
        mock_genre_manager.update_genres_by_artist_async = AsyncMock()

        await service.run_update_genres(artist=None, force=False)

        mock_cleaning.clean_all_metadata_with_logs.assert_called_once_with(sample_tracks)

    @pytest.mark.asyncio
    async def test_calls_artist_renamer_when_provided(
        self,
        mock_track_processor: MagicMock,
        mock_genre_manager: MagicMock,
        mock_config: AppConfig,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should call artist renamer when provided and has mapping."""
        mock_renamer = MagicMock()
        mock_renamer.has_mapping = True
        mock_renamer.rename_tracks = AsyncMock()

        service = GenreUpdateService(
            track_processor=mock_track_processor,
            genre_manager=mock_genre_manager,
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            artist_renamer=mock_renamer,
        )

        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)
        mock_genre_manager.update_genres_by_artist_async = AsyncMock()

        await service.run_update_genres(artist=None, force=False)

        mock_renamer.rename_tracks.assert_called_once_with(sample_tracks)

    @pytest.mark.asyncio
    async def test_skips_artist_renamer_when_no_mapping(
        self,
        mock_track_processor: MagicMock,
        mock_genre_manager: MagicMock,
        mock_config: AppConfig,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should skip artist renamer when it has no mapping."""
        mock_renamer = MagicMock()
        mock_renamer.has_mapping = False
        mock_renamer.rename_tracks = AsyncMock()

        service = GenreUpdateService(
            track_processor=mock_track_processor,
            genre_manager=mock_genre_manager,
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            artist_renamer=mock_renamer,
        )

        mock_track_processor.fetch_tracks_in_batches = AsyncMock(return_value=sample_tracks)
        mock_genre_manager.update_genres_by_artist_async = AsyncMock()

        await service.run_update_genres(artist=None, force=False)

        mock_renamer.rename_tracks.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetches_specific_artist_when_provided(
        self,
        service: GenreUpdateService,
        mock_track_processor: MagicMock,
        mock_genre_manager: MagicMock,
        sample_tracks: list[TrackDict],
    ) -> None:
        """Should fetch specific artist when provided."""
        mock_track_processor.fetch_tracks_async = AsyncMock(return_value=sample_tracks)
        mock_genre_manager.update_genres_by_artist_async = AsyncMock()

        await service.run_update_genres(artist="Test Artist", force=False)

        mock_track_processor.fetch_tracks_async.assert_called_once_with(artist="Test Artist")
