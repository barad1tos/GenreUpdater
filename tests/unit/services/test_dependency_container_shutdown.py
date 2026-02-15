"""Tests for DependencyContainer shutdown and error-handling paths.

Verifies that services are closed in the correct order to prevent
API orchestrator from writing to a closed cache during shutdown,
and that directory validation handles filesystem errors gracefully.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from services.dependency_container import DependencyContainer


class TestDependencyContainerShutdown:
    """Tests for DependencyContainer.close() shutdown order."""

    @pytest.fixture
    def mock_loggers(self) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
        """Create mock loggers for container initialization."""
        console_logger = MagicMock(spec=logging.Logger)
        error_logger = MagicMock(spec=logging.Logger)
        analytics_logger = MagicMock(spec=logging.Logger)
        db_verify_logger = MagicMock(spec=logging.Logger)
        return console_logger, error_logger, analytics_logger, db_verify_logger

    @pytest.fixture
    def container(self, mock_loggers: tuple[MagicMock, MagicMock, MagicMock, MagicMock]) -> DependencyContainer:
        """Create a DependencyContainer instance with mocked loggers."""
        console, error, analytics, db_verify = mock_loggers
        return DependencyContainer(
            config_path="config.yaml",
            console_logger=console,
            error_logger=error,
            analytics_logger=analytics,
            db_verify_logger=db_verify,
            skip_api_validation=True,
        )

    @pytest.mark.asyncio
    async def test_close_shuts_down_api_before_cache(self, container: DependencyContainer) -> None:
        """API orchestrator should be closed before cache service.

        This ensures that any pending API operations can flush to cache
        before the cache service is shut down.
        """
        # Track the order of close calls
        close_order: list[str] = []

        # Create mock API orchestrator
        mock_api_orchestrator = MagicMock()

        async def api_close() -> None:
            """Mock API close that tracks call order."""
            close_order.append("api_close")

        mock_api_orchestrator.close = api_close

        # Create mock cache service
        mock_cache_service = MagicMock()

        async def cache_save() -> None:
            """Mock cache save that tracks call order."""
            close_order.append("cache_save")

        async def cache_shutdown() -> None:
            """Mock cache shutdown that tracks call order."""
            close_order.append("cache_shutdown")

        mock_cache_service.save_all_to_disk = cache_save
        mock_cache_service.shutdown = cache_shutdown

        # Inject mocked services into container
        container._api_orchestrator = mock_api_orchestrator
        container._cache_service = mock_cache_service

        # Execute close
        await container.close()

        # Verify order: API should be closed BEFORE cache operations
        assert close_order == ["api_close", "cache_save", "cache_shutdown"], f"Expected API to close before cache, but got order: {close_order}"

    @pytest.mark.asyncio
    async def test_close_handles_api_close_failure_gracefully(self, container: DependencyContainer) -> None:
        """Cache should still be saved even if API close fails."""
        close_order: list[str] = []

        # Create mock API orchestrator that fails
        mock_api_orchestrator = MagicMock()

        async def api_close_fails() -> None:
            """Mock API close that fails to test error handling."""
            close_order.append("api_close_attempted")
            raise RuntimeError("API close failed")

        mock_api_orchestrator.close = api_close_fails

        # Create mock cache service
        mock_cache_service = MagicMock()

        async def cache_save() -> None:
            """Mock cache save that tracks call order."""
            close_order.append("cache_save")

        async def cache_shutdown() -> None:
            """Mock cache shutdown that tracks call order."""
            close_order.append("cache_shutdown")

        mock_cache_service.save_all_to_disk = cache_save
        mock_cache_service.shutdown = cache_shutdown

        # Inject mocked services
        container._api_orchestrator = mock_api_orchestrator
        container._cache_service = mock_cache_service

        # Execute close - should not raise
        await container.close()

        # Cache operations should still happen after API failure
        assert "cache_save" in close_order, "Cache save should still be called after API failure"
        assert "cache_shutdown" in close_order, "Cache shutdown should still be called after API failure"

    @pytest.mark.asyncio
    async def test_close_handles_none_services(self, container: DependencyContainer) -> None:
        """close() should handle None services gracefully."""
        # Services are None by default in a fresh container
        assert container._api_orchestrator is None
        assert container._cache_service is None

        # Should not raise
        await container.close()

    @pytest.mark.asyncio
    async def test_close_handles_cache_save_failure(self, container: DependencyContainer) -> None:
        """Cache shutdown should still be called even if save fails."""
        close_order: list[str] = []

        # Create mock API orchestrator
        mock_api_orchestrator = MagicMock()

        async def api_close() -> None:
            """Mock API close that tracks call order."""
            close_order.append("api_close")

        mock_api_orchestrator.close = api_close

        # Create mock cache service where save fails
        mock_cache_service = MagicMock()

        async def cache_save_fails() -> None:
            """Mock cache save that fails to test error handling."""
            close_order.append("cache_save_attempted")
            raise OSError("Disk full")

        async def cache_shutdown() -> None:
            """Mock cache shutdown that tracks call order."""
            close_order.append("cache_shutdown")

        mock_cache_service.save_all_to_disk = cache_save_fails
        mock_cache_service.shutdown = cache_shutdown

        # Inject mocked services
        container._api_orchestrator = mock_api_orchestrator
        container._cache_service = mock_cache_service

        # Execute close - should not raise
        await container.close()

        # Shutdown should still be called after save failure
        assert "cache_shutdown" in close_order, "Cache shutdown should be called even if save fails"

    @pytest.mark.asyncio
    async def test_close_handles_cache_shutdown_failure(self, container: DependencyContainer) -> None:
        """Cache shutdown OSError is caught without propagating."""
        mock_cache_service = MagicMock()

        async def cache_save() -> None:
            """No-op mock for cache save."""

        async def cache_shutdown_fails() -> None:
            """Mock cache shutdown that raises OSError."""
            raise OSError("connection reset")

        mock_cache_service.save_all_to_disk = cache_save
        mock_cache_service.shutdown = cache_shutdown_fails

        container._cache_service = mock_cache_service

        # Should not raise — the except (OSError, RuntimeError, asyncio.CancelledError) catches it
        await container.close()


class TestLogAppleScriptsDir:
    """Tests for DependencyContainer._log_apple_scripts_dir error paths."""

    @pytest.fixture
    def mock_loggers(self) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
        """Create mock loggers for container initialization."""
        console_logger = MagicMock(spec=logging.Logger)
        error_logger = MagicMock(spec=logging.Logger)
        analytics_logger = MagicMock(spec=logging.Logger)
        db_verify_logger = MagicMock(spec=logging.Logger)
        return console_logger, error_logger, analytics_logger, db_verify_logger

    @pytest.fixture
    def container(self, mock_loggers: tuple[MagicMock, MagicMock, MagicMock, MagicMock]) -> DependencyContainer:
        """Create a DependencyContainer instance with mocked loggers."""
        console, error, analytics, db_verify = mock_loggers
        return DependencyContainer(
            config_path="config.yaml",
            console_logger=console,
            error_logger=error,
            analytics_logger=analytics,
            db_verify_logger=db_verify,
            skip_api_validation=True,
        )

    def test_catches_oserror_from_is_dir(
        self,
        container: DependencyContainer,
        mock_loggers: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """OSError during Path.is_dir() is caught by the (OSError, ValueError) handler."""
        console_logger = mock_loggers[0]
        mock_client = MagicMock()
        mock_client.apple_scripts_dir = "/some/broken/path"
        container._app_config = MagicMock()

        with patch("services.dependency_container.Path") as mock_path_cls:
            mock_path_cls.return_value.is_dir.side_effect = OSError("I/O error")
            container._log_apple_scripts_dir(mock_client, is_dry_run=False)

        console_logger.exception.assert_called_once()
