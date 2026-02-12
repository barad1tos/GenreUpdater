"""Tests for ExternalApiOrchestrator initialization and resource cleanup."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.api.orchestrator import ExternalApiOrchestrator
from tests.factories import create_test_app_config
from tests.mocks.csv_mock import MockAnalytics, MockLogger  # sourcery skip: dont-import-test-modules

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
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            analytics=MockAnalytics(),  # type: ignore[arg-type]
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
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            analytics=MockAnalytics(),  # type: ignore[arg-type]
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
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            analytics=MockAnalytics(),  # type: ignore[arg-type]
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
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            analytics=MockAnalytics(),  # type: ignore[arg-type]
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
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            analytics=MockAnalytics(),  # type: ignore[arg-type]
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

    def test_encrypt_token_raises_without_secure_config(self) -> None:
        """_encrypt_token_for_future_storage raises if secure_config is None."""
        orchestrator = self._create_orchestrator()

        with pytest.raises(RuntimeError, match="secure_config must be initialized"):
            orchestrator._encrypt_token_for_future_storage("raw_token", "discogs_token")
