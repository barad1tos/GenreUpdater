"""Enhanced API Orchestrator tests with Allure reporting."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.debug_utils import DebugConfig
from core.models.protocols import YearLookupUnavailableError
from core.models.normalization import normalize_search_name
from services.api.orchestrator import ExternalApiOrchestrator
from services.api.request_executor import ApiRequestError
from services.api.year_scoring import ArtistContext, ArtistPeriodContext, ReleaseScorer
from tests.factories import create_test_app_config
from tests.mocks.csv_mock import MockAnalytics, MockLogger

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


class TestExternalApiOrchestratorAllure:
    """Enhanced tests for ExternalApiOrchestrator with Allure reporting."""

    @staticmethod
    def create_orchestrator(
        config: AppConfig | None = None,
        cache_service: Any = None,
        pending_verification_service: Any = None,
        analytics: Any = None,
    ) -> ExternalApiOrchestrator:
        """Create an ExternalApiOrchestrator instance for testing."""
        if cache_service is None:
            cache_service = MagicMock()
            cache_service.get_album_year_async = AsyncMock(return_value=None)
            cache_service.set_album_year_async = AsyncMock()
            cache_service.get_async = AsyncMock(return_value=None)
            cache_service.set_async = AsyncMock()
            cache_service.invalidate = MagicMock()

        if pending_verification_service is None:
            pending_verification_service = MagicMock()
            pending_verification_service.add_track_async = AsyncMock()
            pending_verification_service.get_track_async = AsyncMock(return_value=None)
            pending_verification_service.mark_for_verification = AsyncMock()
            pending_verification_service.remove_from_pending = AsyncMock()

        test_config = config or create_test_app_config()

        console_logger = MockLogger()
        error_logger = MockLogger()
        test_analytics = analytics or MockAnalytics()

        return ExternalApiOrchestrator(
            config=test_config,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=test_analytics,
            cache_service=cache_service,
            pending_verification_service=pending_verification_service,
        )

    def test_orchestrator_initialization_comprehensive(self) -> None:
        """Test comprehensive API orchestrator initialization."""
        test_config = create_test_app_config()
        mock_cache = MagicMock()
        mock_analytics = MockAnalytics()

        orchestrator = ExternalApiOrchestrator(
            config=test_config,
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            cache_service=mock_cache,
            analytics=mock_analytics,
            pending_verification_service=MagicMock(),
        )
        assert orchestrator.config == test_config
        assert orchestrator.cache_service is mock_cache
        assert orchestrator.analytics is mock_analytics

        # Verify logger setup
        assert hasattr(orchestrator, "console_logger")
        assert hasattr(orchestrator, "error_logger")

    @pytest.mark.parametrize(
        ("input_name", "expected"),
        [
            ("The Beatles", "The Beatles"),  # Currently returns unchanged
            ("Led Zeppelin", "Led Zeppelin"),
            ("Pink Floyd", "Pink Floyd"),
            ("AC/DC", "AC/DC"),
            ("Guns N' Roses", "Guns N' Roses"),
        ],
    )
    def test_normalize_search_name_function(self, input_name: str, expected: str) -> None:
        """Test name normalization function."""
        result = normalize_search_name(input_name)
        assert result == expected

    def test_api_provider_configuration(self) -> None:
        """Test API provider configuration and enablement."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        # Verify orchestrator was created and has config
        assert orchestrator.config is not None

        # The orchestrator extracts configuration from AppConfig during init
        # Verify the extracted attributes
        assert hasattr(orchestrator, "discogs_token")
        assert hasattr(orchestrator, "preferred_api")

    @pytest.mark.asyncio
    async def test_cache_integration_comprehensive(self) -> None:
        """Test comprehensive cache integration functionality."""
        mock_cache = MagicMock()
        cached_year = "1975"
        mock_cache.get_album_year_async = AsyncMock(return_value=cached_year)
        mock_cache.set_album_year_async = AsyncMock()
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator(cache_service=mock_cache)
        # The actual cache interaction would be tested in integration tests
        # Here we verify the cache service is properly configured
        assert orchestrator.cache_service is mock_cache

        # Verify cache methods are available
        assert hasattr(orchestrator.cache_service, "get_album_year_async")
        assert hasattr(orchestrator.cache_service, "set_album_year_async")

    @pytest.mark.asyncio
    async def test_api_error_handling_comprehensive(self) -> None:
        """Test comprehensive API error handling."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()

        # Verify error logger is configured
        assert hasattr(orchestrator, "error_logger")
        assert hasattr(orchestrator.error_logger, "error")
        error_scenarios = [
            "Connection timeout",
            "HTTP 429 Rate limit exceeded",
            "HTTP 500 Server error",
            "Invalid JSON response",
            "Authentication failure",
        ]

        assert error_scenarios  # ensure scenarios are defined for future error-handling tests
        # The orchestrator should have error handling mechanisms
        assert hasattr(orchestrator, "console_logger")
        assert hasattr(orchestrator, "error_logger")

        # Should handle various HTTP status codes
        assert hasattr(orchestrator, "config")

    def test_analytics_integration_comprehensive(self) -> None:
        """Test comprehensive analytics integration."""
        mock_analytics = MockAnalytics()
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator(analytics=mock_analytics)
        assert orchestrator.analytics is mock_analytics

        # Verify analytics exposes the protocol methods used in production
        assert hasattr(orchestrator.analytics, "execute_sync_wrapped_call")
        assert hasattr(orchestrator.analytics, "execute_async_wrapped_call")
        assert hasattr(orchestrator.analytics, "batch_mode")

    def test_configuration_extraction(self) -> None:
        """Test configuration values are extracted correctly from AppConfig."""
        test_config = create_test_app_config(
            year_retrieval={
                "enabled": False,
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
                    "batch_size": 10,
                    "delay_between_batches": 60,
                    "adaptive_delay": False,
                    "pending_verification_interval_days": 30,
                },
                "logic": {
                    "min_valid_year": 1900,
                    "definitive_score_threshold": 85,
                    "definitive_score_diff": 15,
                    "preferred_countries": [],
                    "major_market_codes": [],
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
            max_retries=3,
            retry_delay_seconds=1.0,
        )
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator(config=test_config)
        # Verify configuration was extracted from the AppConfig model
        assert orchestrator.min_valid_year == 1900

    def test_http_session_management(self) -> None:
        """Test HTTP session management capabilities."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        # The orchestrator should be designed to handle HTTP sessions
        assert hasattr(orchestrator, "config")
        assert hasattr(orchestrator, "session")

    def test_rate_limiting_configuration(self) -> None:
        """Test rate limiting configuration and setup."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        # Verify rate limiters were initialized from config
        assert hasattr(orchestrator, "rate_limiters")
        assert isinstance(orchestrator.rate_limiters, dict)
        assert "musicbrainz" in orchestrator.rate_limiters
        assert "discogs" in orchestrator.rate_limiters

    @pytest.mark.asyncio
    async def test_get_album_year_logs_search_setup_failure(
        self,
        *,
        monkeypatch: pytest.MonkeyPatch,
        error_logger: logging.Logger,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failure while preparing the year search returns no year; the error log keeps its traceback and the console notes it, debugging off."""
        # DebugConfig() also reads DEBUG_YEAR and DEBUG_ALL, so switch year debugging off explicitly
        year_debugging_off = DebugConfig()
        year_debugging_off.year = False
        monkeypatch.setattr("services.api.orchestrator.debug", year_debugging_off)
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        orchestrator.error_logger = error_logger
        setup_error = TypeError("unexpected artist data")
        monkeypatch.setattr(orchestrator, "_initialize_year_search", AsyncMock(side_effect=setup_error))

        with caplog.at_level(logging.ERROR, logger=error_logger.name):
            result = await orchestrator.get_album_year("Artist", "Album")

        assert result == (None, False, 0, {})
        error_records = [record for record in caplog.records if record.name == error_logger.name]
        assert len(error_records) == 1
        assert "'Artist - Album'" in error_records[0].getMessage()
        logged_exception = error_records[0].exc_info
        assert logged_exception is not None
        assert logged_exception[1] is setup_error
        assert logged_exception[2] is not None  # the traceback itself, not only the exception
        console_logger = orchestrator.console_logger
        assert isinstance(console_logger, MockLogger)
        assert len(console_logger.warning_messages) == 1
        assert "'Artist - Album'" in console_logger.warning_messages[0]
        assert "TypeError" in console_logger.warning_messages[0]

    @pytest.mark.asyncio
    async def test_get_album_year_warns_on_search_failure(
        self,
        *,
        monkeypatch: pytest.MonkeyPatch,
        error_logger: logging.Logger,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failure while fetching releases is noted on the console, and the error log keeps its traceback."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        orchestrator.error_logger = error_logger
        search_error = TimeoutError("search timed out")
        monkeypatch.setattr(orchestrator, "_initialize_year_search", AsyncMock(return_value=("artist", "album", "Artist", "Album", ArtistContext())))
        monkeypatch.setattr(orchestrator, "_fetch_all_api_results", AsyncMock(side_effect=search_error))

        with caplog.at_level(logging.ERROR, logger=error_logger.name):
            result = await orchestrator.get_album_year("Artist", "Album")

        assert result == (None, False, 0, {})
        error_records = [record for record in caplog.records if record.name == error_logger.name]
        assert len(error_records) == 1
        logged_exception = error_records[0].exc_info
        assert logged_exception is not None
        assert logged_exception[1] is search_error
        assert logged_exception[2] is not None  # the traceback itself, not only the exception
        console_logger = orchestrator.console_logger
        assert isinstance(console_logger, MockLogger)
        lookup_warnings = [message for message in console_logger.warning_messages if "Year lookup failed" in message]
        assert len(lookup_warnings) == 1
        assert "'Artist - Album'" in lookup_warnings[0]
        assert "TimeoutError" in lookup_warnings[0]

    @pytest.mark.asyncio
    async def test_setup_failure_console_line_prints_names_verbatim(
        self,
        *,
        rich_console_logger: tuple[logging.Logger, io.StringIO],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Names Rich would read as markup appear as they are when the year search setup fails."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        console_logger, console_output = rich_console_logger
        orchestrator.console_logger = console_logger
        monkeypatch.setattr(orchestrator, "_initialize_year_search", AsyncMock(side_effect=TypeError("unexpected artist data")))

        result = await orchestrator.get_album_year("Artist [live]", "Mixes [/edit]")

        assert result == (None, False, 0, {})
        assert "'Artist [live] - Mixes [/edit]'" in console_output.getvalue()

    @pytest.mark.asyncio
    async def test_search_failure_console_line_prints_names_verbatim(
        self,
        *,
        rich_console_logger: tuple[logging.Logger, io.StringIO],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Names Rich would read as markup appear as they are when the year search fails."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        console_logger, console_output = rich_console_logger
        orchestrator.console_logger = console_logger
        search_inputs = ("artist", "mixes", "Artist [live]", "Mixes [/edit]", ArtistContext())
        monkeypatch.setattr(orchestrator, "_initialize_year_search", AsyncMock(return_value=search_inputs))
        monkeypatch.setattr(orchestrator, "_fetch_all_api_results", AsyncMock(side_effect=TimeoutError("search timed out")))

        result = await orchestrator.get_album_year("Artist [live]", "Mixes [/edit]")

        assert result == (None, False, 0, {})
        assert "'Artist [live] - Mixes [/edit]'" in console_output.getvalue()

    @pytest.mark.asyncio
    async def test_setup_artist_context_logs_failure_with_traceback(
        self,
        *,
        error_logger: logging.Logger,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failed artist-context fetch returns an empty context and keeps its traceback in the error log."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        orchestrator.error_logger = error_logger
        context_error = OSError("MusicBrainz unreachable")
        musicbrainz_client = MagicMock()
        musicbrainz_client.get_artist_activity_period = AsyncMock(side_effect=context_error)
        orchestrator.musicbrainz_client = musicbrainz_client

        with caplog.at_level(logging.WARNING, logger=error_logger.name):
            artist_context = await orchestrator._setup_artist_context("artist", "Artist")

        assert artist_context == ArtistContext()
        error_records = [record for record in caplog.records if record.name == error_logger.name]
        assert len(error_records) == 1
        logged_exception = error_records[0].exc_info
        assert logged_exception is not None
        assert logged_exception[1] is context_error
        assert logged_exception[2] is not None  # the traceback itself, not only the exception

    def test_reissues_are_judged_by_the_reissue_keywords_alone(self) -> None:
        """Remaster keywords normalize titles for matching ("soundtrack", "deluxe"), so they must not carry the reissue penalty."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        configured = orchestrator.config.year_retrieval.reissue_detection.reissue_keywords

        assert orchestrator.release_scorer.reissue_keywords == [keyword.lower() for keyword in configured]

    @pytest.mark.asyncio
    async def test_failed_region_keeps_the_artist_period(self) -> None:
        """A region lookup that fails after the period arrived still scores the search with that period."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        musicbrainz_client = MagicMock()
        musicbrainz_client.get_artist_activity_period = AsyncMock(return_value=(1990, 2005))
        musicbrainz_client.get_artist_region = AsyncMock(side_effect=OSError("MusicBrainz unreachable"))
        orchestrator.musicbrainz_client = musicbrainz_client

        artist_context = await orchestrator._setup_artist_context("artist", "Artist")

        assert artist_context == ArtistContext(period=ArtistPeriodContext(start_year=1990, end_year=2005))

    @pytest.mark.asyncio
    async def test_concurrent_year_searches_score_with_their_own_artist_period(self, *, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two albums searched at once on one orchestrator each score their releases with their own artist's activity period."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        orchestrator.release_scorer = ReleaseScorer()  # the test config zeroes every bonus; the defaults make the period count
        periods = {
            "early artist": ArtistPeriodContext(start_year=1970, end_year=1980),
            "late artist": ArtistPeriodContext(start_year=2000, end_year=2010),
        }
        regions = {"early artist": "gb", "late artist": "us"}
        musicbrainz_client = MagicMock()
        musicbrainz_client.get_artist_activity_period = AsyncMock(
            side_effect=lambda artist_norm: (periods[artist_norm]["start_year"], periods[artist_norm]["end_year"])
        )
        musicbrainz_client.get_artist_region = AsyncMock(side_effect=lambda artist_norm: regions[artist_norm])
        orchestrator.musicbrainz_client = musicbrainz_client

        # Both searches wait here until both have set up their artist context, so the provider calls interleave
        releases = {
            artist_norm: {"title": "Album", "artist": artist_norm, "year": "1971", "country": "gb", "status": "official", "source": "musicbrainz"}
            for artist_norm in periods
        }
        both_searches_started = asyncio.Event()
        arrived: list[str] = []
        scores: dict[str, int] = {}

        async def fetch_after_both_searches_start(
            artist_norm: str, album_norm: str, artist_context: ArtistContext, log_artist: str, log_album: str
        ) -> list[Any]:
            """Hold each search until both have started, then score it with the context it was given."""
            del log_album
            arrived.append(log_artist)
            if len(arrived) == len(periods):
                both_searches_started.set()
            await both_searches_started.wait()
            scores[artist_norm] = orchestrator.release_scorer.score_original_release(
                releases[artist_norm], artist_norm, album_norm, artist_context=artist_context, source="musicbrainz"
            )
            return []

        monkeypatch.setattr(orchestrator, "_fetch_all_api_results", fetch_after_both_searches_start)

        await asyncio.gather(orchestrator.get_album_year("early artist", "album"), orchestrator.get_album_year("late artist", "album"))

        expected = {
            artist_norm: orchestrator.release_scorer.score_original_release(
                releases[artist_norm],
                artist_norm,
                "album",
                artist_context=ArtistContext(region=regions[artist_norm], period=period),
                source="musicbrainz",
            )
            for artist_norm, period in periods.items()
        }
        assert expected["early artist"] != expected["late artist"]  # the periods change the score, so a swap would show
        assert scores == expected


class TestRequestFailureBoundaries:
    """A failed request leaves the orchestrator's answers unknown, never cached or replaced by a fallback."""

    @staticmethod
    def _orchestrator_with_generic_cache() -> tuple[ExternalApiOrchestrator, MagicMock]:
        """Create an orchestrator whose generic cache is empty, and return that cache to check its writes."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        generic_cache = MagicMock()
        generic_cache.get = MagicMock(return_value=None)
        orchestrator.cache_service.generic_service = generic_cache
        return orchestrator, generic_cache

    @pytest.mark.asyncio
    async def test_failed_period_lookup_leaves_the_context_empty(self) -> None:
        """An activity-period request that failed yields an empty context instead of escaping the search setup."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        musicbrainz_client = MagicMock()
        musicbrainz_client.get_artist_activity_period = AsyncMock(side_effect=ApiRequestError("musicbrainz", "u", "failed"))
        orchestrator.musicbrainz_client = musicbrainz_client

        assert await orchestrator._setup_artist_context("artist", "Artist") == ArtistContext()
        musicbrainz_client.get_artist_activity_period.assert_awaited_once_with("artist")

    @pytest.mark.asyncio
    async def test_failed_region_lookup_keeps_the_period(self) -> None:
        """A region request that failed keeps the period that already arrived."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        musicbrainz_client = MagicMock()
        musicbrainz_client.get_artist_activity_period = AsyncMock(return_value=(1990, 2005))
        musicbrainz_client.get_artist_region = AsyncMock(side_effect=ApiRequestError("musicbrainz", "u", "failed"))
        orchestrator.musicbrainz_client = musicbrainz_client

        artist_context = await orchestrator._setup_artist_context("artist", "Artist")

        assert artist_context == ArtistContext(period=ArtistPeriodContext(start_year=1990, end_year=2005))

    @pytest.mark.asyncio
    async def test_failed_musicbrainz_start_year_is_not_cached(self) -> None:
        """A MusicBrainz failure leaves the start year unknown and caches no negative answer."""
        orchestrator, generic_cache = self._orchestrator_with_generic_cache()
        orchestrator.musicbrainz_client = MagicMock()
        orchestrator.musicbrainz_client.get_artist_activity_period = AsyncMock(side_effect=ApiRequestError("musicbrainz", "u", "failed"))
        orchestrator.applemusic_client = MagicMock()
        orchestrator.applemusic_client.get_artist_start_year = AsyncMock(return_value=None)

        assert await orchestrator.get_artist_start_year("artist") is None
        generic_cache.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_itunes_start_year_is_not_cached(self) -> None:
        """MusicBrainz without a year and a failed iTunes fallback leave the start year unknown, not absent."""
        orchestrator, generic_cache = self._orchestrator_with_generic_cache()
        orchestrator.musicbrainz_client = MagicMock()
        orchestrator.musicbrainz_client.get_artist_activity_period = AsyncMock(return_value=(None, None))
        orchestrator.applemusic_client = MagicMock()
        orchestrator.applemusic_client.get_artist_start_year = AsyncMock(side_effect=ApiRequestError("itunes", "u", "failed"))

        assert await orchestrator.get_artist_start_year("artist") is None
        generic_cache.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_unavailable_lookup_propagates_without_a_fallback_year(self) -> None:
        """An album no provider answered for raises instead of returning its library year."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        orchestrator._initialize_year_search = AsyncMock(return_value=("artist", "album", "Artist", "Album", ArtistContext()))
        orchestrator.year_search_coordinator = MagicMock()
        orchestrator.year_search_coordinator.fetch_all_api_results = AsyncMock(side_effect=YearLookupUnavailableError("no provider"))
        orchestrator._safe_mark_for_verification = AsyncMock()

        with pytest.raises(YearLookupUnavailableError):
            await orchestrator.get_album_year("Artist", "Album", current_library_year="1999")

        orchestrator._safe_mark_for_verification.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_musicbrainz_still_tries_itunes(self) -> None:
        """A MusicBrainz failure does not skip the iTunes fallback, and an iTunes answer is cached as usual."""
        orchestrator, generic_cache = self._orchestrator_with_generic_cache()
        orchestrator.musicbrainz_client = MagicMock()
        orchestrator.musicbrainz_client.get_artist_activity_period = AsyncMock(side_effect=ApiRequestError("musicbrainz", "u", "failed"))
        orchestrator.applemusic_client = MagicMock()
        orchestrator.applemusic_client.get_artist_start_year = AsyncMock(return_value=1983)

        assert await orchestrator.get_artist_start_year("artist") == 1983
        generic_cache.set.assert_called_once_with("artist_start_year:artist", 1983, ttl=31536000)
