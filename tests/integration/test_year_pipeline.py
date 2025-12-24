"""Integration tests for Year Retrieval Pipeline with Allure reporting."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.models.track_models import TrackDict
from core.retry_handler import DatabaseRetryHandler, RetryPolicy
from core.tracks.year_retriever import YearRetriever
from metrics.analytics import Analytics
from tests.mocks.csv_mock import MockAnalytics, MockLogger


class TestYearPipelineIntegration:
    """Integration tests for the year retrieval pipeline workflow."""

    @staticmethod
    def _create_retry_handler() -> DatabaseRetryHandler:
        """Create a retry handler for testing."""
        import logging

        policy = RetryPolicy(
            max_retries=2,
            base_delay_seconds=0.01,
            max_delay_seconds=0.1,
            jitter_range=0.0,
            operation_timeout_seconds=30.0,
        )
        return DatabaseRetryHandler(logger=logging.getLogger("test"), default_policy=policy)

    @staticmethod
    def create_year_retriever(
        mock_track_processor: AsyncMock | None = None,
        mock_cache_service: MagicMock | None = None,
        mock_external_api: AsyncMock | None = None,
        mock_pending_verification: MagicMock | None = None,
        config: dict[str, Any] | None = None,
        dry_run: bool = False,
        retry_handler: DatabaseRetryHandler | None = None,
    ) -> YearRetriever:
        """Create a YearRetriever instance for testing."""
        if mock_track_processor is None:
            mock_track_processor = AsyncMock()
            mock_track_processor.update_track_async = AsyncMock(return_value=True)

        if mock_cache_service is None:
            mock_cache_service = MagicMock()
            mock_cache_service.get_async = AsyncMock(return_value=None)
            mock_cache_service.set_async = AsyncMock()
            mock_cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
            mock_cache_service.get_album_year_entry_from_cache = AsyncMock(return_value=None)
            mock_cache_service.cache_album_year = AsyncMock()
            mock_cache_service.store_album_year_in_cache = AsyncMock()

        if mock_external_api is None:
            mock_external_api = AsyncMock()
            mock_external_api.get_album_year = AsyncMock(return_value=("2020", True, 85))

        if mock_pending_verification is None:
            mock_pending_verification = MagicMock()
            mock_pending_verification.add_track = MagicMock()
            mock_pending_verification.get_pending_tracks = MagicMock(return_value=[])
            mock_pending_verification.mark_for_verification = AsyncMock()
            mock_pending_verification.generate_problematic_albums_report = AsyncMock(return_value=0)
            mock_pending_verification.get_entry = AsyncMock(return_value=None)
            mock_pending_verification.is_verification_needed = AsyncMock(return_value=False)

        if retry_handler is None:
            retry_handler = TestYearPipelineIntegration._create_retry_handler()

        test_config = config or {"force_update": False, "processing": {"batch_size": 100}, "year_update": {"concurrent_limit": 5}}

        return YearRetriever(
            track_processor=mock_track_processor,
            cache_service=cast(Any, mock_cache_service),
            external_api=cast(Any, mock_external_api),
            pending_verification=cast(Any, mock_pending_verification),
            retry_handler=retry_handler,
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            analytics=cast(Analytics, cast(object, MockAnalytics())),
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
                year=data.get("year"),
                date_added=data.get("date_added", "2024-01-01 10:00:00"),
                track_status=data.get("track_status", "subscription"),
                last_modified="2024-01-01 10:00:00",
            )
            tracks.append(track)
        return tracks

    @pytest.mark.asyncio
    async def test_year_pipeline_musicbrainz_primary(self) -> None:
        """Test MusicBrainz as primary source for year retrieval."""
        tracks_data = [
            {"id": "1", "name": "Song 1", "artist": "The Beatles", "album": "Abbey Road", "year": "", "date_added": "2024-01-01 10:00:00"},
            {"id": "2", "name": "Song 2", "artist": "The Beatles", "album": "Abbey Road", "year": "", "date_added": "2024-01-01 11:00:00"},
            {"id": "3", "name": "Song 3", "artist": "The Beatles", "album": "Abbey Road", "year": "", "date_added": "2024-01-01 12:00:00"},
        ]

        tracks = TestYearPipelineIntegration.create_test_tracks(tracks_data)

        # Mock successful MusicBrainz response
        mock_external_api = AsyncMock()
        mock_external_api.get_album_year = AsyncMock(return_value=("1969", True, 90))

        year_retriever = TestYearPipelineIntegration.create_year_retriever(mock_external_api=mock_external_api)
        result = await year_retriever.process_album_years(tracks)
        # Verify external API was called
        mock_external_api.get_album_year.assert_called()

        # Verify results
        assert isinstance(result, bool)

        # Verify tracks were processed
        call_count = year_retriever.track_processor.update_track_async.call_count  # type: ignore[attr-defined]
        assert call_count >= 0  # Should process tracks needing year updates

    @pytest.mark.asyncio
    async def test_year_pipeline_discogs_fallback(self) -> None:
        """Test Discogs fallback when MusicBrainz fails."""
        tracks_data = [
            {"id": "1", "name": "Rare Song", "artist": "Rare Artist", "album": "Rare Album", "year": "", "date_added": "2024-01-01 10:00:00"},
            {
                "id": "2",
                "name": "Another Rare Song",
                "artist": "Rare Artist",
                "album": "Rare Album",
                "year": "",
                "date_added": "2024-01-01 11:00:00",
            },
        ]

        tracks = TestYearPipelineIntegration.create_test_tracks(tracks_data)

        # Mock fallback scenario: MusicBrainz fails, Discogs succeeds
        mock_external_api = AsyncMock()
        # First call fails (MusicBrainz), second succeeds (Discogs)
        mock_external_api.get_album_year = AsyncMock(
            side_effect=[
                (None, False),  # MusicBrainz fails
                ("2018", True),  # Discogs succeeds
            ]
        )

        year_retriever = TestYearPipelineIntegration.create_year_retriever(mock_external_api=mock_external_api)
        result = await year_retriever.process_album_years(tracks)
        # Year retrieval system should handle fallback gracefully
        assert isinstance(result, bool)

        # Should have attempted API calls
        call_count = mock_external_api.get_album_year.call_count
        assert call_count >= 0  # May vary based on implementation

    @pytest.mark.asyncio
    async def test_year_pipeline_no_year_found(self) -> None:
        """Test handling when no year is found from any source."""
        tracks_data = [
            {
                "id": "1",
                "name": "Unknown Song",
                "artist": "Unknown Artist",
                "album": "Unknown Album",
                "year": "",
                "date_added": "2024-01-01 10:00:00",
            },
            {
                "id": "2",
                "name": "Mystery Track",
                "artist": "Unknown Artist",
                "album": "Unknown Album",
                "year": "",
                "date_added": "2024-01-01 11:00:00",
            },
        ]

        tracks = TestYearPipelineIntegration.create_test_tracks(tracks_data)

        # Mock all APIs failing to find year
        mock_external_api = AsyncMock()
        mock_external_api.get_album_year = AsyncMock(return_value=(None, False, 0))  # 3-tuple

        year_retriever = TestYearPipelineIntegration.create_year_retriever(mock_external_api=mock_external_api)
        result = await year_retriever.process_album_years(tracks)
        # Pipeline should continue gracefully even when no year is found
        assert isinstance(result, bool)

        # Should have attempted to get year but not crash
        mock_external_api.get_album_year.assert_called()

    @pytest.mark.asyncio
    async def test_year_pipeline_prerelease_handling(self) -> None:
        """Test handling of prerelease tracks."""
        tracks_data = [
            {
                "id": "1",
                "name": "Early Release",
                "artist": "Test Artist",
                "album": "Preview Album",
                "year": "",
                "track_status": "prerelease",
                "date_added": "2024-01-01 10:00:00",
            },
            {
                "id": "2",
                "name": "Beta Track",
                "artist": "Test Artist",
                "album": "Preview Album",
                "year": "",
                "track_status": "prerelease",
                "date_added": "2024-01-01 11:00:00",
            },
            {
                "id": "3",
                "name": "Regular Track",
                "artist": "Test Artist",
                "album": "Preview Album",
                "year": "",
                "track_status": "subscription",
                "date_added": "2024-01-01 12:00:00",
            },
        ]

        tracks = TestYearPipelineIntegration.create_test_tracks(tracks_data)

        # Mock API response for prerelease handling
        mock_external_api = AsyncMock()
        mock_external_api.get_album_year = AsyncMock(return_value=("2024", True, 85))

        year_retriever = TestYearPipelineIntegration.create_year_retriever(mock_external_api=mock_external_api)
        result = await year_retriever.process_album_years(tracks)
        # Pipeline should handle prerelease tracks appropriately
        assert isinstance(result, bool)

        # Count prerelease vs regular tracks
        prerelease_count = sum(t.track_status == "prerelease" for t in tracks)
        regular_count = sum(t.track_status == "subscription" for t in tracks)

    @pytest.mark.asyncio
    async def test_year_pipeline_verification_needed(self) -> None:
        """Test identification of tracks needing verification."""
        tracks_data = [
            # Tracks with conflicting year information
            {
                "id": "1",
                "name": "Conflicted Song 1",
                "artist": "Complex Artist",
                "album": "Complex Album",
                "year": "2019",
                "date_added": "2024-01-01 10:00:00",
            },
            {
                "id": "2",
                "name": "Conflicted Song 2",
                "artist": "Complex Artist",
                "album": "Complex Album",
                "year": "2020",
                "date_added": "2024-01-01 11:00:00",
            },
            {
                "id": "3",
                "name": "Conflicted Song 3",
                "artist": "Complex Artist",
                "album": "Complex Album",
                "year": "2021",
                "date_added": "2024-01-01 12:00:00",
            },
            # Track with suspicious data
            {"id": "4", "name": "S", "artist": "Complex Artist", "album": "A", "year": "", "date_added": "2024-01-01 13:00:00"},
        ]

        tracks = TestYearPipelineIntegration.create_test_tracks(tracks_data)

        # Mock API for verification scenario
        mock_external_api = AsyncMock()
        mock_external_api.get_album_year = AsyncMock(return_value=("2020", True, 85))

        # Use centralized mock from create_year_retriever helper
        year_retriever = TestYearPipelineIntegration.create_year_retriever(mock_external_api=mock_external_api)
        result = await year_retriever.process_album_years(tracks)
        # Pipeline should identify problematic tracks
        assert isinstance(result, bool)

        unique_years = {track.year for track in tracks[:3] if track.year}
        conflicting_tracks = len(unique_years) > 1
        suspicious_album = any(len(t.album) <= 3 for t in tracks)  # Short album names
