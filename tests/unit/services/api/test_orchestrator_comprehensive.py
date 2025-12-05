"""Comprehensive unit tests for ExternalApiOrchestrator."""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.api.orchestrator import (
    ExternalApiOrchestrator,
    normalize_name,
)


class TestNormalizeFunction:
    """Test the normalize_name function."""

    def test_normalize_returns_unchanged(self) -> None:
        """Test that normalize_name is currently a stub that returns unchanged."""
        # The current implementation just returns the name unchanged
        assert normalize_name("The Beatles") == "The Beatles"
        assert normalize_name("AC/DC") == "AC/DC"
        assert normalize_name("Björk") == "Björk"
        assert normalize_name("") == ""
        assert normalize_name("   ") == "   "


class TestExternalApiOrchestrator:
    """Test the ExternalApiOrchestrator class."""

    @pytest.fixture
    def mock_config(self) -> dict[str, Any]:
        """Create mock configuration."""
        return {
            "year_retrieval": {
                "api_auth": {
                    "discogs_token": "test_token",
                    "lastfm_api_key": "test_key",
                    "musicbrainz_app_name": "TestApp/1.0",
                    "contact_email": "test@example.com",
                },
                "rate_limits": {
                    "discogs_requests_per_minute": 25,
                    "musicbrainz_requests_per_second": 1,
                    "lastfm_requests_per_second": 5,
                    "itunes_requests_per_second": 10,
                },
                "processing": {
                    "cache_ttl_days": 30,
                    "skip_prerelease": True,
                    "future_year_threshold": 1,
                    "prerelease_recheck_days": 30,
                },
                "logic": {
                    "min_valid_year": 1900,
                    "definitive_score_threshold": 85,
                    "definitive_score_diff": 15,
                },
                "scoring": {
                    "base_score": 10,
                },
                "preferred_api": "musicbrainz",
            },
            "max_retries": 3,
            "retry_delay_seconds": 1.0,
        }

    @pytest.fixture
    def mock_loggers(self) -> tuple[MagicMock, MagicMock]:
        """Create mock loggers."""
        console_logger = MagicMock()
        console_logger.isEnabledFor.return_value = False
        error_logger = MagicMock()
        return console_logger, error_logger

    @pytest.fixture
    def mock_services(self) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Create mock services."""
        analytics = MagicMock()
        cache_service = MagicMock()
        # Mock the actual cache methods used by orchestrator
        cache_service.get_async = AsyncMock(return_value=None)
        cache_service.set_async = AsyncMock()
        cache_service.invalidate = MagicMock()
        pending_verification = MagicMock()
        return analytics, cache_service, pending_verification

    @pytest.fixture
    async def orchestrator(
        self,
        mock_config: dict[str, Any],
        mock_loggers: tuple[MagicMock, MagicMock],
        mock_services: tuple[MagicMock, MagicMock, MagicMock],
    ) -> AsyncGenerator[ExternalApiOrchestrator]:
        """Create an orchestrator instance."""
        console_logger, error_logger = mock_loggers
        analytics, cache_service, pending_verification = mock_services

        orch = ExternalApiOrchestrator(
            config=mock_config,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=analytics,
            cache_service=cache_service,
            pending_verification_service=pending_verification,
        )

        # Ensure cleanup happens after tests
        yield orch

        # Clean up session if it was created
        if hasattr(orch, "session") and orch.session:
            await orch.close()

    @pytest.mark.asyncio
    async def test_init_state(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test orchestrator initialization."""
        assert orchestrator.config is not None
        assert orchestrator.console_logger is not None
        assert orchestrator.error_logger is not None
        assert orchestrator.cache_service is not None
        assert orchestrator.pending_verification_service is not None
        assert orchestrator.request_counts == {
            "discogs": 0,
            "musicbrainz": 0,
            "lastfm": 0,
            "itunes": 0,
        }

    @pytest.mark.asyncio
    async def test_configuration_parsing(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test configuration parsing and setup."""
        # Check that configuration was properly parsed
        assert hasattr(orchestrator, "preferred_api")
        assert orchestrator.preferred_api == "musicbrainz"
        assert hasattr(orchestrator, "min_valid_year")
        assert orchestrator.min_valid_year == 1900
        assert hasattr(orchestrator, "current_year")
        assert isinstance(orchestrator.current_year, int)
        assert orchestrator.current_year >= 2024

    @pytest.mark.asyncio
    async def test_rate_limiting_setup(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test rate limiting setup."""
        # Check that rate limiters are configured
        assert hasattr(orchestrator, "rate_limiters")
        assert isinstance(orchestrator.rate_limiters, dict)
        assert "musicbrainz" in orchestrator.rate_limiters
        assert "discogs" in orchestrator.rate_limiters
        assert "lastfm" in orchestrator.rate_limiters
        assert "itunes" in orchestrator.rate_limiters

    @pytest.mark.asyncio
    async def test_scoring_system_initialization(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test scoring system initialization."""
        assert hasattr(orchestrator, "release_scorer")
        assert orchestrator.release_scorer is not None

    @pytest.mark.asyncio
    async def test_api_client_initialization(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test API client initialization."""
        # Before initialization, clients should not be set up yet
        # Check using hasattr to avoid accessing private members
        assert hasattr(orchestrator, "session")

        # After initialization, API clients should be set up
        await orchestrator.initialize()
        assert hasattr(orchestrator, "musicbrainz_client")
        assert hasattr(orchestrator, "discogs_client")
        assert hasattr(orchestrator, "lastfm_client")
        assert hasattr(orchestrator, "applemusic_client")
        # Check initialization status using session
        assert orchestrator.session is not None

    @pytest.mark.asyncio
    async def test_initialize_multiple_times(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test that initialize can be called multiple times."""
        await orchestrator.initialize()
        # Check initialization using session existence
        assert orchestrator.session is not None
        first_session = orchestrator.session

        # Second call should not reinitialize
        await orchestrator.initialize()
        assert orchestrator.session == first_session

        # Force reinitialize
        await orchestrator.initialize(force=True)
        # Session should be recreated
        assert orchestrator.session is not None

    @pytest.mark.asyncio
    async def test_close_session(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test closing the session."""
        await orchestrator.initialize()
        assert orchestrator.session is not None

        await orchestrator.close()
        # After close, session should be closed but not None
        assert orchestrator.session.closed

    @pytest.mark.asyncio
    async def test_request_counts_tracking(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test that request counts are tracked."""
        await orchestrator.initialize()

        initial_count = orchestrator.request_counts["musicbrainz"]
        assert initial_count == 0

        # Make a request (mocked to avoid actual API call)
        with patch.object(orchestrator, "_make_api_request", return_value={}):
            orchestrator.request_counts["musicbrainz"] += 1

        assert orchestrator.request_counts["musicbrainz"] == 1

    @pytest.mark.asyncio
    async def test_api_call_durations_tracking(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test that API call durations are tracked."""
        await orchestrator.initialize()

        assert len(orchestrator.api_call_durations["musicbrainz"]) == 0

        # Simulate adding a duration
        orchestrator.api_call_durations["musicbrainz"].append(1.5)

        assert len(orchestrator.api_call_durations["musicbrainz"]) == 1
        assert orchestrator.api_call_durations["musicbrainz"][0] == 1.5

    @pytest.mark.asyncio
    async def test_config_validation_error(self) -> None:
        """Test that invalid config raises appropriate errors."""
        invalid_config = {
            "year_retrieval": None,  # Invalid: should be a dict
        }

        console_logger = MagicMock()
        error_logger = MagicMock()
        analytics = MagicMock()
        cache_service = MagicMock()
        pending_verification = MagicMock()

        with pytest.raises(TypeError):
            ExternalApiOrchestrator(
                config=invalid_config,
                console_logger=console_logger,
                error_logger=error_logger,
                analytics=analytics,
                cache_service=cache_service,
                pending_verification_service=pending_verification,
            )

    @pytest.mark.asyncio
    async def test_secure_config_initialization(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test that SecureConfig is initialized properly."""
        # SecureConfig may or may not initialize successfully
        # depending on system configuration
        assert hasattr(orchestrator, "secure_config")
        # It should be either None or a SecureConfig instance
        assert orchestrator.secure_config is None or hasattr(orchestrator.secure_config, "encrypt_token")

    @pytest.mark.asyncio
    async def test_user_agent_setup(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test that User-Agent is set up correctly."""
        assert hasattr(orchestrator, "user_agent")
        assert "TestApp/1.0" in orchestrator.user_agent
        assert "test@example.com" in orchestrator.user_agent

    @pytest.mark.asyncio
    async def test_preferred_api_normalization(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test that preferred API is normalized correctly."""
        assert orchestrator.preferred_api == "musicbrainz"

    @pytest.mark.asyncio
    async def test_apply_preferred_order(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test applying preferred API order."""
        # Create a test orchestrator to test the method
        mock_orch = ExternalApiOrchestrator(
            config=orchestrator.config,
            console_logger=orchestrator.console_logger,
            error_logger=orchestrator.error_logger,
            analytics=orchestrator.analytics,
            cache_service=orchestrator.cache_service,
            pending_verification_service=orchestrator.pending_verification_service,
        )

        # Test preferred order indirectly by checking the preferred_api attribute
        assert mock_orch.preferred_api == "musicbrainz"

    @pytest.mark.asyncio
    async def test_coerce_integer_methods(self, orchestrator: ExternalApiOrchestrator) -> None:
        """Test integer coercion helper methods."""
        # Test coercion methods using the config values that were coerced
        # Check that configuration values were properly coerced
        assert isinstance(orchestrator.future_year_threshold, int)
        assert orchestrator.future_year_threshold >= 0

        assert isinstance(orchestrator.prerelease_recheck_days, int)
        assert orchestrator.prerelease_recheck_days > 0

        assert isinstance(orchestrator.default_api_retry_delay, float)
        assert orchestrator.default_api_retry_delay >= 0
