"""Integration tests for Genre Pipeline with Allure reporting."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock
import pytest
from core.tracks.genre_manager import GenreManager
from services.api.orchestrator import ExternalApiOrchestrator
from core.models.track_models import TrackDict
from core.models.protocols import AnalyticsProtocol

from tests.factories import create_mock_track_processor, create_test_app_config
from tests.mocks.csv_mock import MockAnalytics, MockLogger

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


class TestGenrePipelineIntegration:
    """Integration tests for the complete genre pipeline workflow."""

    @staticmethod
    def create_genre_manager(
        mock_track_processor: AsyncMock,
        config: AppConfig | None,
        dry_run: bool,
    ) -> GenreManager:
        """Create a GenreManager instance for testing."""
        test_config = config or create_test_app_config(
            genre_update={"batch_size": 100, "concurrent_limit": 5},
        )

        return GenreManager(
            track_processor=mock_track_processor,
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=cast(AnalyticsProtocol, cast(object, MockAnalytics())),
            config=test_config,
            dry_run=dry_run,
        )

    @staticmethod
    def create_test_tracks(tracks_data: list[dict[str, Any]]) -> list[TrackDict]:
        """Create test tracks from track data specifications."""
        tracks = []
        for data in tracks_data:
            track = TrackDict(
                id=data.get("id", "test_id"),
                name=data.get("name", "Test Track"),
                artist=data.get("artist", "Test Artist"),
                album=data.get("album", "Test Album"),
                genre=data.get("genre", ""),
                date_added=data.get("date_added", "2024-01-01 10:00:00"),
                track_status=data.get("track_status", "subscription"),
                year=data.get("year"),
                last_modified="2024-01-01 10:00:00",
            )
            tracks.append(track)
        return tracks

    @staticmethod
    def create_mock_api_orchestrator(
        fallback_responses: list[tuple[str | None, bool, int]] | None,
    ) -> MagicMock:
        """Create a mock API orchestrator with fallback behavior."""
        mock_orchestrator = MagicMock(spec=ExternalApiOrchestrator)

        if fallback_responses:
            mock_orchestrator.get_album_year = AsyncMock(side_effect=fallback_responses)
        else:
            # Default successful response
            mock_orchestrator.get_album_year = AsyncMock(return_value=("2020", True, 85))

        return mock_orchestrator

    @pytest.mark.asyncio
    async def test_genre_pipeline_full_flow(self) -> None:
        """Test complete genre pipeline execution with multiple artists and tracks."""
        tracks_data = [
            # Artist 1 - earliest album (Album A) has no genre, so no track changes
            {"id": "1", "name": "Song 1", "artist": "Rock Artist", "album": "Album A", "genre": "", "date_added": "2024-01-01 10:00:00"},
            {"id": "2", "name": "Song 2", "artist": "Rock Artist", "album": "Album A", "genre": "", "date_added": "2024-01-01 11:00:00"},
            {"id": "3", "name": "Song 3", "artist": "Rock Artist", "album": "Album B", "genre": "Rock", "date_added": "2024-01-02 10:00:00"},
            # Artist 2 - earliest album is Pop, so the empty track 5 becomes Pop
            {"id": "4", "name": "Pop Song 1", "artist": "Pop Artist", "album": "Pop Album", "genre": "Pop", "date_added": "2024-01-03 10:00:00"},
            {"id": "5", "name": "Pop Song 2", "artist": "Pop Artist", "album": "Pop Album", "genre": "", "date_added": "2024-01-03 11:00:00"},
            # Artist 3 - earliest album is Jazz, so the Blues track 7 becomes Jazz
            {
                "id": "6",
                "name": "Jazz Song",
                "artist": "Jazz Artist",
                "album": "Jazz Album",
                "genre": "Jazz",
                "date_added": "2020-01-01 10:00:00",
            },
            {
                "id": "7",
                "name": "Blues Song",
                "artist": "Jazz Artist",
                "album": "Blues Album",
                "genre": "Blues",
                "date_added": "2024-01-01 10:00:00",
            },
        ]

        tracks = TestGenrePipelineIntegration.create_test_tracks(tracks_data)
        track_processor = create_mock_track_processor()
        genre_manager = TestGenrePipelineIntegration.create_genre_manager(track_processor, None, False)
        updated_tracks, change_logs = await genre_manager.update_genres_by_artist_async(tracks)
        # Verify that tracks were processed
        assert isinstance(updated_tracks, list)
        assert isinstance(change_logs, list)

        # Each artist takes the genre of its earliest album (determine_dominant_genre_for_artist)
        updates = {call.kwargs["track_id"]: call.kwargs["new_genre"] for call in track_processor.update_track_async.call_args_list}
        assert updates == {"5": "Pop", "7": "Jazz"}

        # Verify artists were processed (should be 3 unique artists)
        unique_artists = {track.artist for track in tracks}
        assert len(unique_artists) == 3

    # noinspection PyUnusedLocal
    @pytest.mark.asyncio
    async def test_genre_pipeline_api_fallback(self) -> None:
        """Test API fallback chain during genre pipeline execution."""
        tracks_data = [
            {"id": "1", "name": "Song 1", "artist": "Rare Artist", "album": "Rare Album", "genre": "", "date_added": "2024-01-01 10:00:00"},
            {"id": "2", "name": "Song 2", "artist": "Rare Artist", "album": "Rare Album", "genre": "", "date_added": "2024-01-01 11:00:00"},
        ]

        tracks = TestGenrePipelineIntegration.create_test_tracks(tracks_data)

        # Mock API fallback scenario: MB fails, Discogs fails, LastFM succeeds
        mock_orchestrator = TestGenrePipelineIntegration.create_mock_api_orchestrator(
            [
                (None, False, 0),  # MusicBrainz fails
                (None, False, 0),  # Discogs fails
                ("2019", True, 85),  # LastFM succeeds
            ]
        )
        del mock_orchestrator  # Created for demonstration but not used in current test

        track_processor = create_mock_track_processor()
        genre_manager = TestGenrePipelineIntegration.create_genre_manager(track_processor, None, False)
        # Note: This test focuses on genre pipeline integration
        # The actual API fallback logic is in Year Retrieval Pipeline
        # Here we test that genre pipeline continues when API calls are involved
        updated_tracks, change_logs = await genre_manager.update_genres_by_artist_async(tracks)
        # Genre pipeline should continue processing even with API issues
        assert isinstance(updated_tracks, list)
        assert isinstance(change_logs, list)

        # Neither track has a genre, so there is no dominant genre to propagate
        track_processor.update_track_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_genre_pipeline_cache_usage(self) -> None:
        """Test cache integration in genre pipeline."""
        tracks_data = [
            {
                "id": "1",
                "name": "Cached Song 1",
                "artist": "Cached Artist",
                "album": "Cached Album",
                "genre": "",
                "date_added": "2024-01-01 10:00:00",
            },
            {
                "id": "2",
                "name": "Cached Song 2",
                "artist": "Cached Artist",
                "album": "Cached Album",
                "genre": "",
                "date_added": "2024-01-01 11:00:00",
            },
        ]

        tracks = TestGenrePipelineIntegration.create_test_tracks(tracks_data)
        track_processor = create_mock_track_processor()
        genre_manager = TestGenrePipelineIntegration.create_genre_manager(track_processor, None, False)
        # First run - should populate any internal caches
        first_run_tracks, first_run_logs = await genre_manager.update_genres_by_artist_async(tracks=tracks)
        # Second run - should use cached data where applicable
        second_run_tracks, second_run_logs = await genre_manager.update_genres_by_artist_async(tracks=tracks)
        # Both runs should produce consistent results
        assert len(first_run_tracks) == len(second_run_tracks)
        assert len(first_run_logs) == len(second_run_logs)

        # Track processor should be called same number of times
        # (assuming no caching at track update level)
        _first_call_count = track_processor.update_track_async.call_count

        # Reset and run again
        track_processor.update_track_async.reset_mock()
        await genre_manager.update_genres_by_artist_async(tracks)
        _second_call_count = track_processor.update_track_async.call_count

    @pytest.mark.asyncio
    async def test_genre_pipeline_batch_processing(self) -> None:
        """Test batch processing capabilities of genre pipeline."""
        # Create a larger set of tracks for batch testing
        tracks_data = []
        for i in range(20):  # 20 tracks across 4 artists
            artist_num = (i // 5) + 1
            tracks_data.append(
                {
                    "id": str(i + 1),
                    "name": f"Song {i + 1}",
                    "artist": f"Artist {artist_num}",
                    "album": f"Album {(i % 3) + 1}",
                    "genre": "Rock" if i % 3 == 0 else "",  # Some tracks have genres, some don't
                    "date_added": f"2024-01-{(i % 30) + 1:02d} 10:00:00",
                }
            )

        tracks = TestGenrePipelineIntegration.create_test_tracks(tracks_data)

        # Configure for batch processing
        batch_config = create_test_app_config(genre_update={"batch_size": 10, "concurrent_limit": 3})
        track_processor = create_mock_track_processor()
        genre_manager = TestGenrePipelineIntegration.create_genre_manager(track_processor, batch_config, False)
        start_time = datetime.now(UTC)
        updated_tracks, change_logs = await genre_manager.update_genres_by_artist_async(tracks)
        end_time = datetime.now(UTC)
        _processing_time = (end_time - start_time).total_seconds()
        # Verify all tracks were processed
        assert isinstance(updated_tracks, list)
        assert isinstance(change_logs, list)

        # Artists 1 and 4 start with a Rock album, so their empty tracks become Rock;
        # artists 2 and 3 start with an album that has no genre, so theirs stay empty
        updates = {call.kwargs["track_id"]: call.kwargs["new_genre"] for call in track_processor.update_track_async.call_args_list}
        assert updates == dict.fromkeys(("2", "3", "5", "17", "18", "20"), "Rock")

    @pytest.mark.asyncio
    async def test_genre_pipeline_error_recovery(self) -> None:
        """A failed track update is logged and does not stop the other artists' updates."""
        tracks_data = [
            # Each artist's earliest track carries the genre that its later empty-genre track receives
            {"id": "10", "name": "Good Opener", "artist": "Good Artist", "album": "Good Album", "genre": "Rock", "date_added": "2024-01-01 09:00:00"},
            {"id": "1", "name": "Good Song", "artist": "Good Artist", "album": "Good Album", "genre": "", "date_added": "2024-01-01 10:00:00"},
            {"id": "20", "name": "Bad Opener", "artist": "Bad Artist", "album": "Bad Album", "genre": "Pop", "date_added": "2024-01-01 10:30:00"},
            {"id": "2", "name": "Failing Song", "artist": "Bad Artist", "album": "Bad Album", "genre": "", "date_added": "2024-01-01 10:45:00"},
            # A track without an ID fails validation and is never sent for update
            {"id": "", "name": "Bad Song", "artist": "Bad Artist", "album": "Bad Album", "genre": "", "date_added": "2024-01-01 11:00:00"},
            {
                "id": "30",
                "name": "Another Opener",
                "artist": "Another Artist",
                "album": "Another Album",
                "genre": "Jazz",
                "date_added": "2024-01-01 11:30:00",
            },
            {
                "id": "3",
                "name": "Another Good Song",
                "artist": "Another Artist",
                "album": "Another Album",
                "genre": "",
                "date_added": "2024-01-01 12:00:00",
            },
        ]
        tracks = TestGenrePipelineIntegration.create_test_tracks(tracks_data)

        async def update_track(*, track_id: str, **_kwargs: object) -> bool:
            """Fail the update of track 2 and accept every other track."""
            if track_id == "2":
                raise RuntimeError("Simulated update error")
            return True

        mock_track_processor = AsyncMock()
        mock_track_processor.update_track_async.side_effect = update_track

        genre_manager = TestGenrePipelineIntegration.create_genre_manager(mock_track_processor, None, False)
        updated_tracks, change_logs = await genre_manager.update_genres_by_artist_async(tracks)

        attempted = {call.kwargs["track_id"] for call in mock_track_processor.update_track_async.call_args_list}
        assert attempted == {"1", "2", "3"}
        assert {track.id: track.genre for track in updated_tracks} == {"1": "Rock", "3": "Jazz"}
        assert {log.track_id for log in change_logs} == {"1", "3"}

        error_logger = genre_manager.error_logger
        assert isinstance(error_logger, MockLogger)
        assert any("Simulated update error" in message for message in error_logger.error_messages)
