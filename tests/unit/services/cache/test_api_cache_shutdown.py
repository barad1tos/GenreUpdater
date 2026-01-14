"""Tests for ApiCacheService shutdown functionality."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.cache.api_cache import ApiCacheService


class TestApiCacheShutdown:
    """Tests for ApiCacheService shutdown method."""

    @staticmethod
    def create_service(config: dict[str, Any] | None = None) -> ApiCacheService:
        """Create an ApiCacheService instance for testing.

        Args:
            config: Optional configuration overrides.

        Returns:
            Configured ApiCacheService instance with mock logger.
        """
        temp_path = Path(tempfile.mkdtemp(prefix="api-cache-shutdown-test-"))
        log_directory = temp_path / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)

        default_config = {
            "api_cache_file": str(temp_path / "test_cache.json"),
            "log_directory": str(log_directory),
        }
        test_config = {**default_config, **(config or {})}
        mock_logger = MagicMock()
        return ApiCacheService(test_config, mock_logger)

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_background_tasks(self) -> None:
        """Test that shutdown waits for all background tasks to complete."""
        service = TestApiCacheShutdown.create_service()

        # Track task completion
        task_completed = False

        async def slow_task() -> None:
            """Simulate a slow background task."""
            nonlocal task_completed
            await asyncio.sleep(0.1)
            task_completed = True

        # Add a background task
        task = asyncio.create_task(slow_task())
        service._background_tasks.add(task)
        task.add_done_callback(service._background_tasks.discard)

        # Shutdown should wait for task to complete
        await service.shutdown()

        assert task_completed, "Shutdown should wait for background tasks to complete"
        assert len(service._background_tasks) == 0, "Background tasks should be cleared after shutdown"

    @pytest.mark.asyncio
    async def test_shutdown_handles_task_exceptions(self) -> None:
        """Test that shutdown handles exceptions in background tasks gracefully."""
        service = TestApiCacheShutdown.create_service()

        async def failing_task() -> None:
            """Simulate a task that raises an exception."""
            await asyncio.sleep(0.01)
            raise ValueError("Simulated task failure")

        async def successful_task() -> None:
            """Simulate a successful task."""
            await asyncio.sleep(0.01)

        # Add both failing and successful tasks
        failing = asyncio.create_task(failing_task())
        successful = asyncio.create_task(successful_task())

        service._background_tasks.add(failing)
        service._background_tasks.add(successful)

        # Shutdown should not raise even with failing tasks
        # return_exceptions=True in gather handles this
        await service.shutdown()

        assert len(service._background_tasks) == 0, "Background tasks should be cleared even with exceptions"

    @pytest.mark.asyncio
    async def test_shutdown_handles_empty_tasks(self) -> None:
        """Test that shutdown handles empty task set gracefully."""
        service = TestApiCacheShutdown.create_service()

        # Ensure no background tasks
        assert len(service._background_tasks) == 0

        # Shutdown should complete without error
        await service.shutdown()

        # Verify early return logged nothing (debug logs only when tasks exist)
        # Just ensure no exception was raised
        assert len(service._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self) -> None:
        """Test that calling shutdown() multiple times is safe.

        Idempotent shutdown ensures that even if close() is called multiple
        times (e.g., from different error handlers), the service remains
        in a consistent state without raising exceptions.
        """
        service = TestApiCacheShutdown.create_service()

        # Add a background task
        task_completed = False

        async def background_task() -> None:
            """Simulate a background task that completes after a short delay."""
            nonlocal task_completed
            await asyncio.sleep(0.05)
            task_completed = True

        task = asyncio.create_task(background_task())
        service._background_tasks.add(task)
        task.add_done_callback(service._background_tasks.discard)

        # First shutdown should wait for task and complete
        await service.shutdown()
        assert task_completed, "First shutdown should complete the task"
        assert len(service._background_tasks) == 0
        assert service._shutting_down is True

        # Second shutdown should be a no-op (idempotent)
        await service.shutdown()
        assert len(service._background_tasks) == 0

        # Third shutdown should also be safe
        await service.shutdown()
        assert len(service._background_tasks) == 0
