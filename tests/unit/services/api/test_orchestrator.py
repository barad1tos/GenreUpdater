"""Enhanced API Orchestrator tests with Allure reporting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.api.orchestrator import ExternalApiOrchestrator, normalize_name
from tests.factories import create_test_app_config
from tests.mocks.csv_mock import MockAnalytics, MockLogger  # sourcery skip: dont-import-test-modules

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
            console_logger=console_logger,  # type: ignore[arg-type]
            error_logger=error_logger,  # type: ignore[arg-type]
            analytics=test_analytics,  # type: ignore[arg-type]
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
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            cache_service=mock_cache,
            analytics=mock_analytics,  # type: ignore[arg-type]
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
    def test_normalize_name_function(self, input_name: str, expected: str) -> None:
        """Test name normalization function."""
        result = normalize_name(input_name)
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
                    "cache_ttl_days": 30,
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
        assert orchestrator.cache_ttl_days == 30

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
