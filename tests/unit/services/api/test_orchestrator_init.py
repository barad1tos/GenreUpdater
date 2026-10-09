"""Tests for ExternalApiOrchestrator initialization and resource cleanup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.api.orchestrator import ExternalApiOrchestrator
from tests.factories import create_test_app_config
from tests.mocks.csv_mock import MockAnalytics, MockLogger

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


def create_test_config() -> AppConfig:
    """Create a standard test AppConfig for the orchestrator.

    Returns:
        AppConfig with all required configuration sections.
    """
    return create_test_app_config()


def create_mock_cache_service() -> MagicMock:
    """Create a mock cache service with required async methods.

    Returns:
        MagicMock configured with async cache methods.
    """
    cache_service = MagicMock()
    cache_service.get_album_year_async = AsyncMock(return_value=None)
    cache_service.set_album_year_async = AsyncMock()
    cache_service.get_async = AsyncMock(return_value=None)
    cache_service.set_async = AsyncMock()
    cache_service.invalidate = MagicMock()
    return cache_service


def create_mock_pending_verification_service() -> MagicMock:
    """Create a mock pending verification service with required async methods.

    Returns:
        MagicMock configured with async verification methods.
    """
    pending_verification_service = MagicMock()
    pending_verification_service.add_track_async = AsyncMock()
    pending_verification_service.get_track_async = AsyncMock(return_value=None)
    pending_verification_service.mark_for_verification = AsyncMock()
    pending_verification_service.remove_from_pending = AsyncMock()
    return pending_verification_service


class TestInitializeClosesSessionOnFailure:
    """Tests for HTTP session cleanup when initialization fails."""

    @pytest.mark.asyncio
    async def test_initialize_closes_session_on_api_client_failure(self) -> None:
        """If _initialize_api_clients raises, HTTP session should be closed."""
        config = create_test_config()
        cache_service = create_mock_cache_service()
        pending_verification_service = create_mock_pending_verification_service()

        orchestrator = ExternalApiOrchestrator(
            config=config,
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=MockAnalytics(),
            cache_service=cache_service,
            pending_verification_service=pending_verification_service,
        )

        # Create a mock session to track close() calls
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()

        # Patch _create_client_session to return our mock
        with (
            patch.object(orchestrator, "_create_client_session", return_value=mock_session),
            patch.object(orchestrator, "_initialize_api_clients", side_effect=ValueError("Test error")),
        ):
            with pytest.raises(ValueError, match="Test error"):
                await orchestrator.initialize(force=True)

            mock_session.close.assert_awaited_once()
            assert orchestrator.session is None

    @pytest.mark.asyncio
    async def test_initialize_closes_session_on_year_coordinator_failure(self) -> None:
        """If _initialize_year_search_coordinator raises, HTTP session should be closed."""
        config = create_test_config()
        cache_service = create_mock_cache_service()
        pending_verification_service = create_mock_pending_verification_service()

        orchestrator = ExternalApiOrchestrator(
            config=config,
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=MockAnalytics(),
            cache_service=cache_service,
            pending_verification_service=pending_verification_service,
        )

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()

        with (
            patch.object(orchestrator, "_create_client_session", return_value=mock_session),
            patch.object(orchestrator, "_initialize_api_clients"),
            patch.object(orchestrator, "_initialize_year_search_coordinator", side_effect=RuntimeError("Coordinator init failed")),
        ):
            with pytest.raises(RuntimeError, match="Coordinator init failed"):
                await orchestrator.initialize(force=True)

            mock_session.close.assert_awaited_once()
            assert orchestrator.session is None

    @pytest.mark.asyncio
    async def test_initialize_success_does_not_close_session(self) -> None:
        """On successful initialization, session should remain open."""
        config = create_test_config()
        cache_service = create_mock_cache_service()
        pending_verification_service = create_mock_pending_verification_service()

        orchestrator = ExternalApiOrchestrator(
            config=config,
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=MockAnalytics(),
            cache_service=cache_service,
            pending_verification_service=pending_verification_service,
        )

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()

        with (
            patch.object(orchestrator, "_create_client_session", return_value=mock_session),
            patch.object(orchestrator, "_initialize_api_clients"),
            patch.object(orchestrator, "_initialize_year_search_coordinator"),
        ):
            await orchestrator.initialize(force=True)

            mock_session.close.assert_not_awaited()
            assert orchestrator.session is mock_session

    @pytest.mark.asyncio
    async def test_initialize_handles_already_closed_session_on_failure(self) -> None:
        """If session is already closed when failure occurs, handle gracefully."""
        config = create_test_config()
        cache_service = create_mock_cache_service()
        pending_verification_service = create_mock_pending_verification_service()

        orchestrator = ExternalApiOrchestrator(
            config=config,
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=MockAnalytics(),
            cache_service=cache_service,
            pending_verification_service=pending_verification_service,
        )

        # Create a mock session that reports as already closed
        mock_session = MagicMock()
        mock_session.closed = True
        mock_session.close = AsyncMock()

        with (
            patch.object(orchestrator, "_create_client_session", return_value=mock_session),
            patch.object(orchestrator, "_initialize_api_clients", side_effect=ValueError("Test error")),
        ):
            with pytest.raises(ValueError, match="Test error"):
                await orchestrator.initialize(force=True)

            # close() should NOT be called since session was already closed
            mock_session.close.assert_not_awaited()
            assert orchestrator.session is None


class TestSecureConfigGuards:
    """Tests for RuntimeError guards when secure_config is None."""

    @staticmethod
    def _create_orchestrator() -> ExternalApiOrchestrator:
        """Create orchestrator without secure_config."""
        config = create_test_config()
        cache_service = create_mock_cache_service()
        pending_verification_service = create_mock_pending_verification_service()

        orchestrator = ExternalApiOrchestrator(
            config=config,
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=MockAnalytics(),
            cache_service=cache_service,
            pending_verification_service=pending_verification_service,
        )
        orchestrator.secure_config = None
        return orchestrator

    def test_decrypt_token_raises_without_secure_config(self) -> None:
        """_decrypt_token raises RuntimeError if secure_config is None."""
        orchestrator = self._create_orchestrator()

        with pytest.raises(RuntimeError, match="secure_config must be initialized"):
            orchestrator._decrypt_token("encrypted_value", "discogs_token")


class TestTokenValuesNotLogged:
    """Loading a token must never write the plaintext or ciphertext to logs."""

    PLAINTEXT_TOKEN = "plain-discogs-token-value"  # noqa: S105

    def test_plaintext_token_is_returned_without_encrypting_or_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        """A plaintext token is used as is: no throwaway encryption, no value in the logs."""
        orchestrator = ExternalApiOrchestrator(
            config=create_test_config(),
            console_logger=logging.getLogger("test.orchestrator_init.console"),
            error_logger=logging.getLogger("test.orchestrator_init.error"),
            analytics=MockAnalytics(),
            cache_service=create_mock_cache_service(),
            pending_verification_service=create_mock_pending_verification_service(),
        )
        secure_config = MagicMock()
        secure_config.is_token_encrypted.return_value = False
        orchestrator.secure_config = secure_config

        with caplog.at_level(logging.DEBUG):
            token = orchestrator._process_token_security(self.PLAINTEXT_TOKEN, "discogs_token")

        assert token == self.PLAINTEXT_TOKEN
        secure_config.encrypt_token.assert_not_called()
        assert self.PLAINTEXT_TOKEN not in caplog.text


class TestYearSearchCoordinatorWiring:
    """The coordinator learns whether Discogs can be queried at all."""

    @pytest.mark.parametrize(("token", "enabled"), [("token-value", True), ("", False)])
    def test_discogs_enabled_follows_the_token(self, token: str, enabled: bool) -> None:
        """Without a Discogs token the coordinator treats Discogs as inactive, not as a failed provider."""
        orchestrator = ExternalApiOrchestrator(
            config=create_test_config(),
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=MockAnalytics(),
            cache_service=create_mock_cache_service(),
            pending_verification_service=create_mock_pending_verification_service(),
        )
        orchestrator.discogs_token = token
        orchestrator.musicbrainz_client = MagicMock()
        orchestrator.discogs_client = MagicMock()
        orchestrator.applemusic_client = MagicMock()

        orchestrator._initialize_year_search_coordinator()

        assert orchestrator.year_search_coordinator.discogs_enabled is enabled
