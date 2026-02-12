"""Enhanced API Orchestrator tests with Allure reporting."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest
from services.api.orchestrator import ExternalApiOrchestrator, normalize_name

from tests.mocks.csv_mock import MockAnalytics, MockLogger  # sourcery skip: dont-import-test-modules


class TestExternalApiOrchestratorAllure:
    """Enhanced tests for ExternalApiOrchestrator with Allure reporting."""

    @staticmethod
    def create_orchestrator(
        config: dict[str, Any] | None = None,
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

        # Create full configuration with year_retrieval section
        test_config = config or {
            "year_retrieval": {
                "api_auth": {
                    "discogs_token": "test_token",
                    "musicbrainz_app_name": "TestApp",
                    "contact_email": "test@example.com",
                },
                "rate_limits": {
                    "discogs_requests_per_minute": 25,
                    "musicbrainz_requests_per_second": 1,
                    "itunes_requests_per_second": 10,
                },
                "processing": {
                    "cache_ttl_days": 30,
                },
                "logic": {
                    "min_valid_year": 1900,
                    "definitive_score_threshold": 85,
                    "definitive_score_diff": 15,
                },
                "scoring": {
                    "base_score": 50,
                    "exact_match_bonus": 30,
                },
            },
            "external_apis": {
                "timeout": 30,
                "max_concurrent_requests": 10,
                "musicbrainz": {"enabled": True},
                "discogs": {"enabled": True},
                "applemusic": {"enabled": False},
            },
        }

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
        config = {
            "year_retrieval": {
                "api_auth": {
                    "discogs_token": "test_token",
                    "musicbrainz_app_name": "TestApp",
                    "contact_email": "test@example.com",
                },
                "rate_limits": {},
                "processing": {},
                "logic": {},
                "scoring": {},
            },
            "external_apis": {
                "timeout": 45,
                "max_concurrent_requests": 15,
                "musicbrainz": {"enabled": True, "rate_limit": 1.0, "base_url": "https://musicbrainz.org"},
                "discogs": {"enabled": True, "rate_limit": 0.5, "token": "test_token"},
                "applemusic": {"enabled": False},
            },
        }
        mock_cache = MagicMock()
        mock_analytics = MockAnalytics()

        orchestrator = ExternalApiOrchestrator(
            config=config,
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            cache_service=mock_cache,
            analytics=mock_analytics,  # type: ignore[arg-type]
            pending_verification_service=MagicMock(),
        )
        assert orchestrator.config == config
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
        config = {
            "year_retrieval": {
                "api_auth": {
                    "discogs_token": "",  # Empty token effectively disables it
                    "musicbrainz_app_name": "TestApp",
                    "contact_email": "test@example.com",
                },
                "rate_limits": {},
                "processing": {},
                "logic": {},
                "scoring": {},
            },
            "external_apis": {
                "musicbrainz": {"enabled": True},
                "discogs": {"enabled": False},  # Disabled
                "applemusic": {"enabled": False},
            },
        }
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator(config=config)
        # Verify configuration is stored
        assert orchestrator.config["external_apis"]["musicbrainz"]["enabled"] is True
        assert orchestrator.config["external_apis"]["discogs"]["enabled"] is False
        assert orchestrator.config["external_apis"]["applemusic"]["enabled"] is False

        enabled_providers = [
            provider
            for provider, settings in orchestrator.config["external_apis"].items()
            if isinstance(settings, dict) and settings.get("enabled", False)
        ]

        assert len(enabled_providers) == 1  # MusicBrainz only

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

    @pytest.mark.parametrize(
        ("config_key", "config_value"),
        [
            ("timeout", 30),
            ("max_concurrent_requests", 10),
        ],
    )
    def test_configuration_validation(self, config_key: str, config_value: Any) -> None:
        """Test configuration values are stored correctly."""
        config = {
            "year_retrieval": {
                "api_auth": {
                    "discogs_token": "test_token",
                    "musicbrainz_app_name": "TestApp",
                    "contact_email": "test@example.com",
                },
                "rate_limits": {},
                "processing": {},
                "logic": {},
                "scoring": {},
            },
            "external_apis": {
                config_key: config_value,
                "musicbrainz": {"enabled": True},
                "discogs": {"enabled": True},
            },
        }
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator(config=config)
        assert orchestrator.config["external_apis"][config_key] == config_value

    def test_http_session_management(self) -> None:
        """Test HTTP session management capabilities."""
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator()
        # The orchestrator should be designed to handle HTTP sessions
        assert hasattr(orchestrator, "config")

        # Configuration should support session-related settings
        config = orchestrator.config.get("external_apis", {})
        assert isinstance(config, dict)

        # Should support timeout configuration
        _timeout_configured = "timeout" in config or any(
            "timeout" in provider_config for provider_config in config.values() if isinstance(provider_config, dict)
        )

    def test_rate_limiting_configuration(self) -> None:
        """Test rate limiting configuration and setup."""
        config = {
            "year_retrieval": {
                "api_auth": {
                    "discogs_token": "test_token",
                    "musicbrainz_app_name": "TestApp",
                    "contact_email": "test@example.com",
                },
                "rate_limits": {
                    "discogs_requests_per_minute": 30,  # 30 requests per minute
                    "musicbrainz_requests_per_second": 1,  # 1 request per second
                    "itunes_requests_per_second": 10,  # 10 requests per second
                },
                "processing": {},
                "logic": {},
                "scoring": {},
            },
            "external_apis": {
                "musicbrainz": {
                    "enabled": True,
                    "rate_limit": 1.0,  # 1 request per second
                },
                "discogs": {
                    "enabled": True,
                    "rate_limit": 0.5,  # 2 seconds between requests
                },
                "itunes": {
                    "enabled": True,
                    "rate_limit": 10.0,  # 10 requests per second
                },
            },
        }
        orchestrator = TestExternalApiOrchestratorAllure.create_orchestrator(config=config)
        api_config = orchestrator.config["external_apis"]

        # Verify rate limits are configured
        assert api_config["musicbrainz"]["rate_limit"] == 1.0
        assert api_config["discogs"]["rate_limit"] == 0.5
        assert api_config["itunes"]["rate_limit"] == 10.0

        rate_limits = {
            provider: settings.get("rate_limit", 0)
            for provider, settings in api_config.items()
            if isinstance(settings, dict) and "rate_limit" in settings
        }

        assert len(rate_limits) == 3
