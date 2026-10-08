"""Integration tests for Year Retrieval Pipeline with Allure reporting."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.track_models import TrackDict
from core.retry_handler import DatabaseRetryHandler, RetryPolicy
from core.tracks.year_retriever import YearRetriever
from core.models.protocols import AnalyticsProtocol
from tests.factories import create_mock_track_processor, create_test_app_config
from tests.mocks.csv_mock import MockAnalytics, MockLogger
from tests.mocks.protocol_mocks import MockExternalApiService, MockPendingVerificationService

# An album whose tracks have no year yet; the tentative-year tests differ only in the API response
RARE_ALBUM_TRACKS: list[dict[str, Any]] = [
    {"id": "1", "name": "Rare Song", "artist": "Rare Artist", "album": "Rare Album", "year": "", "date_added": "2024-01-01 10:00:00"},
    {"id": "2", "name": "Another Rare Song", "artist": "Rare Artist", "album": "Rare Album", "year": "", "date_added": "2024-01-01 11:00:00"},
]


def _written_years(track_processor: AsyncMock) -> list[tuple[str, str]]:
    """Return the (track_id, new_year) of every update_track_async call, sorted; duplicates stay so a double write fails."""
    return sorted((call.kwargs["track_id"], call.kwargs["new_year"]) for call in track_processor.update_track_async.call_args_list)


def _failures_logged(year_retriever: YearRetriever) -> list[str]:
    """Return the warning-or-higher messages of the retriever's error logger; console warnings are not included.

    With albums processed concurrently, as this config sets up, the year batch logs an album
    that raised as a warning and carries on, so a crash shows up here while
    process_album_years still returns True.
    """
    error_logger = year_retriever.error_logger
    assert isinstance(error_logger, MockLogger)
    # MockLogger.exception records into error_messages as well
    return [*error_logger.warning_messages, *error_logger.error_messages, *error_logger.critical_messages]


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
        *,
        mock_external_api: MockExternalApiService,
        mock_track_processor: AsyncMock | None = None,
        mock_cache_service: MagicMock | None = None,
        mock_pending_verification: MockPendingVerificationService | None = None,
        dry_run: bool = False,
        retry_handler: DatabaseRetryHandler | None = None,
    ) -> YearRetriever:
        """Create a YearRetriever instance for testing."""
        if mock_track_processor is None:
            mock_track_processor = create_mock_track_processor()

        if mock_cache_service is None:
            mock_cache_service = MagicMock()
            mock_cache_service.get_async = AsyncMock(return_value=None)
            mock_cache_service.set_async = AsyncMock()
            mock_cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
            mock_cache_service.get_album_year_entry_from_cache = AsyncMock(return_value=None)
            mock_cache_service.cache_album_year = AsyncMock()
            mock_cache_service.store_album_year_in_cache = AsyncMock()

        if mock_pending_verification is None:
            mock_pending_verification = MockPendingVerificationService()

        if retry_handler is None:
            retry_handler = TestYearPipelineIntegration._create_retry_handler()

        test_config = create_test_app_config(
            year_retrieval={
                "enabled": True,
                "preferred_api": "musicbrainz",
                "api_auth": {
                    "discogs_token": "test-token",
                    "musicbrainz_app_name": "TestApp/1.0",
                    "contact_email": "test@example.com",
                },
                "rate_limits": {
                    "discogs_requests_per_minute": 25,
                    "musicbrainz_requests_per_second": 1,
                    "concurrent_api_calls": 3,
                },
                "processing": {
                    "batch_size": 100,
                    "delay_between_batches": 60,
                    "adaptive_delay": False,
                    "cache_ttl_days": 30,
                    "pending_verification_interval_days": 30,
                    "prerelease_handling": "process_editable",
                    # Off the default of 30, so a mark shows whether the configured value reached it
                    "prerelease_recheck_days": 7,
                },
                "logic": {
                    "min_valid_year": 1900,
                    "definitive_score_threshold": 85,
                    "definitive_score_diff": 15,
                    "preferred_countries": [],
                    "major_market_codes": [],
                    # Off the default of 30, so the low-confidence test shows the configured floor is used
                    "min_confidence_for_new_year": 40,
                },
                "reissue_detection": {"reissue_keywords": []},
                "scoring": {
                    "base_score": 0,
                    "artist_exact_match_bonus": 0,
                    "album_exact_match_bonus": 0,
                    "perfect_match_bonus": 0,
                    "album_variation_bonus": 0,
                    "album_substring_penalty": 0,
                    "album_unrelated_penalty": 0,
                    "mb_release_group_match_bonus": 0,
                    "type_album_bonus": 0,
                    "type_ep_single_penalty": 0,
                    "type_compilation_live_penalty": 0,
                    "status_official_bonus": 0,
                    "status_bootleg_penalty": 0,
                    "status_promo_penalty": 0,
                    "reissue_penalty": 0,
                    "year_diff_penalty_scale": 0,
                    "year_diff_max_penalty": 0,
                    "year_before_start_penalty": 0,
                    "year_after_end_penalty": 0,
                    "year_near_start_bonus": 0,
                    "country_artist_match_bonus": 0,
                    "country_major_market_bonus": 0,
                    "source_mb_bonus": 0,
                    "source_discogs_bonus": 0,
                },
            },
        )

        return YearRetriever(
            track_processor=mock_track_processor,
            cache_service=cast(Any, mock_cache_service),
            external_api=mock_external_api,
            pending_verification=mock_pending_verification,
            retry_handler=retry_handler,
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
                year=data.get("year"),
                date_added=data.get("date_added", "2024-01-01 10:00:00"),
                track_status=data.get("track_status", "subscription"),
                last_modified="2024-01-01 10:00:00",
            )
            tracks.append(track)
        return tracks

    @pytest.mark.asyncio
    async def test_year_pipeline_applies_definitive_year(self) -> None:
        """A definitive API year fills every track of the album."""
        tracks_data = [
            {"id": "1", "name": "Song 1", "artist": "The Beatles", "album": "Abbey Road", "year": "", "date_added": "2024-01-01 10:00:00"},
            {"id": "2", "name": "Song 2", "artist": "The Beatles", "album": "Abbey Road", "year": "", "date_added": "2024-01-01 11:00:00"},
            {"id": "3", "name": "Song 3", "artist": "The Beatles", "album": "Abbey Road", "year": "", "date_added": "2024-01-01 12:00:00"},
        ]
        tracks = TestYearPipelineIntegration.create_test_tracks(tracks_data)

        external_api = MockExternalApiService()
        external_api.get_album_year_response = ("1969", True, 90, {"1969": 90})
        track_processor = create_mock_track_processor()

        year_retriever = TestYearPipelineIntegration.create_year_retriever(mock_track_processor=track_processor, mock_external_api=external_api)
        result = await year_retriever.process_album_years(tracks)

        assert result is True
        # (artist, album, current library year, year the earliest track was added)
        assert external_api.get_album_year_calls == [("The Beatles", "Abbey Road", None, 2024)]
        assert _written_years(track_processor) == [("1", "1969"), ("2", "1969"), ("3", "1969")]
        assert _failures_logged(year_retriever) == []

    @pytest.mark.asyncio
    async def test_year_pipeline_applies_tentative_year(self) -> None:
        """A non-definitive API year still fills an album whose tracks have no year."""
        tracks = TestYearPipelineIntegration.create_test_tracks(RARE_ALBUM_TRACKS)

        # The orchestrator returns this when YearScoreResolver rejects a lone candidate over 3 years old scoring
        # under MIN_CONFIDENT_SCORE_THRESHOLD (85). It also queues the album for a recheck itself; this fake does
        # not, so the empty marks below cover only the year pipeline
        external_api = MockExternalApiService()
        external_api.get_album_year_response = ("2018", False, 80, {"2018": 80})
        track_processor = create_mock_track_processor()
        pending_verification = MockPendingVerificationService()

        year_retriever = TestYearPipelineIntegration.create_year_retriever(
            mock_track_processor=track_processor,
            mock_external_api=external_api,
            mock_pending_verification=pending_verification,
        )
        result = await year_retriever.process_album_years(tracks)

        assert result is True
        assert external_api.get_album_year_calls == [("Rare Artist", "Rare Album", None, 2024)]
        assert _written_years(track_processor) == [("1", "2018"), ("2", "2018")]
        assert pending_verification.marked_albums == []
        assert _failures_logged(year_retriever) == []

    @pytest.mark.asyncio
    async def test_year_pipeline_defers_low_confidence_year(self) -> None:
        """A non-definitive year under min_confidence_for_new_year is not written to an album with no year.

        The album is queued for a recheck instead.
        """
        tracks = TestYearPipelineIntegration.create_test_tracks(RARE_ALBUM_TRACKS)

        external_api = MockExternalApiService()
        # 35 clears the default floor of 30 but not the configured 40
        external_api.get_album_year_response = ("2018", False, 35, {"2018": 35})
        track_processor = create_mock_track_processor()
        pending_verification = MockPendingVerificationService()

        year_retriever = TestYearPipelineIntegration.create_year_retriever(
            mock_track_processor=track_processor,
            mock_external_api=external_api,
            mock_pending_verification=pending_verification,
        )
        result = await year_retriever.process_album_years(tracks)

        assert result is True
        assert external_api.get_album_year_calls == [("Rare Artist", "Rare Album", None, 2024)]
        assert _written_years(track_processor) == []
        assert pending_verification.marked_albums == [
            (
                "Rare Artist",
                "Rare Album",
                "very_low_confidence_no_existing",
                {"proposed_year": "2018", "confidence_score": 35, "threshold": 40},
                None,
            ),
        ]
        assert _failures_logged(year_retriever) == []

    @pytest.mark.asyncio
    async def test_year_pipeline_no_year_found(self) -> None:
        """When no source finds a year, the album is left unchanged and the run still succeeds."""
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

        external_api = MockExternalApiService()
        external_api.get_album_year_response = (None, False, 0, {})
        track_processor = create_mock_track_processor()
        pending_verification = MockPendingVerificationService()

        year_retriever = TestYearPipelineIntegration.create_year_retriever(
            mock_track_processor=track_processor,
            mock_external_api=external_api,
            mock_pending_verification=pending_verification,
        )
        result = await year_retriever.process_album_years(tracks)

        assert result is True
        assert external_api.get_album_year_calls == [("Unknown Artist", "Unknown Album", None, 2024)]
        assert _written_years(track_processor) == []
        # The orchestrator queues such an album itself; a second mark here would count each attempt twice
        assert pending_verification.marked_albums == []
        assert _failures_logged(year_retriever) == []

    @pytest.mark.asyncio
    async def test_year_pipeline_prerelease_handling(self) -> None:
        """Prerelease tracks are read-only, so only the subscription track gets the year, and the album is queued for a recheck."""
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

        external_api = MockExternalApiService()
        external_api.get_album_year_response = ("2024", True, 85, {"2024": 85})
        track_processor = create_mock_track_processor()
        pending_verification = MockPendingVerificationService()

        year_retriever = TestYearPipelineIntegration.create_year_retriever(
            mock_track_processor=track_processor,
            mock_external_api=external_api,
            mock_pending_verification=pending_verification,
        )
        result = await year_retriever.process_album_years(tracks)

        assert result is True
        assert external_api.get_album_year_calls == [("Test Artist", "Preview Album", None, 2024)]
        assert _written_years(track_processor) == [("3", "2024")]
        assert pending_verification.marked_albums == [
            (
                "Test Artist",
                "Preview Album",
                "prerelease",
                {"track_count": "3", "prerelease_count": "2", "editable_count": "1", "mixed_album": "true"},
                7,
            ),
        ]
        assert _failures_logged(year_retriever) == []

    @pytest.mark.asyncio
    async def test_year_pipeline_unifies_conflicting_years(self) -> None:
        """A definitive API year replaces conflicting track years across the album."""
        tracks_data = [
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
        ]
        tracks = TestYearPipelineIntegration.create_test_tracks(tracks_data)

        external_api = MockExternalApiService()
        external_api.get_album_year_response = ("2020", True, 85, {"2020": 85})
        track_processor = create_mock_track_processor()
        pending_verification = MockPendingVerificationService()

        year_retriever = TestYearPipelineIntegration.create_year_retriever(
            mock_track_processor=track_processor,
            mock_external_api=external_api,
            mock_pending_verification=pending_verification,
        )
        result = await year_retriever.process_album_years(tracks)

        assert result is True
        # The only test with an existing library year: each year appears once, and the tie goes to
        # the first track's 2019, since get_most_common_year keeps the order tracks were counted in
        assert external_api.get_album_year_calls == [("Complex Artist", "Complex Album", "2019", 2024)]
        # Track 2 already has 2020; the other two move to it, and nothing is queued for a recheck
        assert _written_years(track_processor) == [("1", "2020"), ("3", "2020")]
        assert pending_verification.marked_albums == []
        assert _failures_logged(year_retriever) == []
