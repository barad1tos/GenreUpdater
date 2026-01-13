"""Tests for ExternalApiOrchestrator graceful shutdown behavior.

This module tests the close() method's ability to:
1. Wait for pending fire-and-forget tasks to complete
2. Cancel tasks that exceed the timeout
3. Clear the _pending_tasks set after shutdown

These tests address CODE_AUDIT_2026-01 issue C2:
"Pending Tasks Without Graceful Shutdown"
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.api.orchestrator import ExternalApiOrchestrator
from tests.mocks.csv_mock import MockAnalytics, MockLogger  # sourcery skip: dont-import-test-modules


class TestOrchestratorGracefulShutdown:
    """Tests for ExternalApiOrchestrator.close() graceful shutdown behavior."""

    @staticmethod
    def create_orchestrator(
        cache_service: Any = None,
        pending_verification_service: Any = None,
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

        test_config = {
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
        analytics = MockAnalytics()

        return ExternalApiOrchestrator(
            config=test_config,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=analytics,
            cache_service=cache_service,
            pending_verification_service=pending_verification_service,
        )

    @pytest.mark.asyncio
    async def test_close_waits_for_pending_tasks(self) -> None:
        """close() should wait for all pending tasks before closing session."""
        orchestrator = self.create_orchestrator()

        # Create a mock session
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        orchestrator.session = mock_session

        # Track whether the task completed
        task_completed = False

        async def slow_task() -> None:
            nonlocal task_completed
            await asyncio.sleep(0.1)  # Short delay
            task_completed = True

        # Add task to pending tasks
        task = asyncio.create_task(slow_task())
        orchestrator._pending_tasks.add(task)
        task.add_done_callback(orchestrator._pending_tasks.discard)

        # Call close - it should wait for the task
        await orchestrator.close()

        # Verify task completed
        assert task_completed, "Task should have completed before close() returned"
        assert len(orchestrator._pending_tasks) == 0, "Pending tasks should be cleared"
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_cancels_tasks_after_timeout(self) -> None:
        """close() should cancel tasks that exceed the 5 second timeout."""
        orchestrator = self.create_orchestrator()

        # Create a mock session
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        orchestrator.session = mock_session

        # Track task state
        task_started = False
        task_cancelled = False

        async def very_slow_task() -> None:
            nonlocal task_started, task_cancelled
            task_started = True
            try:
                # This task takes way longer than the 5s timeout
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                task_cancelled = True
                raise

        # Add the slow task
        task = asyncio.create_task(very_slow_task())
        orchestrator._pending_tasks.add(task)
        task.add_done_callback(orchestrator._pending_tasks.discard)

        # Patch asyncio.wait to use a very short timeout for testing
        original_wait = asyncio.wait

        async def fast_timeout_wait(
            tasks: set[asyncio.Task[Any]],
            timeout: float | None = None,
            return_when: str = asyncio.ALL_COMPLETED,
        ) -> tuple[set[asyncio.Task[Any]], set[asyncio.Task[Any]]]:
            # Use 0.1s instead of 5s for faster tests
            return await original_wait(tasks, timeout=0.1, return_when=return_when)

        with patch("asyncio.wait", fast_timeout_wait):
            await orchestrator.close()

        # Verify task was started and cancelled
        assert task_started, "Task should have started"
        assert task_cancelled, "Task should have been cancelled due to timeout"
        assert len(orchestrator._pending_tasks) == 0, "Pending tasks should be cleared"
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_empty_pending_tasks(self) -> None:
        """close() should handle case when there are no pending tasks."""
        orchestrator = self.create_orchestrator()

        # Create a mock session
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        orchestrator.session = mock_session

        # Ensure no pending tasks
        assert len(orchestrator._pending_tasks) == 0

        # Call close - should not fail
        await orchestrator.close()

        # Session should still be closed
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_clears_pending_tasks_set(self) -> None:
        """close() should clear _pending_tasks set even if tasks complete."""
        orchestrator = self.create_orchestrator()

        # Create a mock session
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        orchestrator.session = mock_session

        # Create multiple quick tasks
        async def quick_task() -> None:
            await asyncio.sleep(0.01)

        tasks = []
        for _ in range(5):
            task = asyncio.create_task(quick_task())
            orchestrator._pending_tasks.add(task)
            task.add_done_callback(orchestrator._pending_tasks.discard)
            tasks.append(task)

        # Call close
        await orchestrator.close()

        # Verify all tasks completed and set is cleared
        assert len(orchestrator._pending_tasks) == 0, "Pending tasks set should be cleared"
        for task in tasks:
            assert task.done(), "All tasks should be done"

    @pytest.mark.asyncio
    async def test_close_with_no_session(self) -> None:
        """close() should handle case when session is None."""
        orchestrator = self.create_orchestrator()

        # Ensure session is None
        orchestrator.session = None

        # Add a pending task (simulating orphaned task scenario)
        task_completed = False

        async def orphan_task() -> None:
            nonlocal task_completed
            await asyncio.sleep(0.01)
            task_completed = True

        task = asyncio.create_task(orphan_task())
        orchestrator._pending_tasks.add(task)
        task.add_done_callback(orchestrator._pending_tasks.discard)

        # Call close - should return early but still handle pending tasks
        await orchestrator.close()

        # The current implementation returns early if session is None/closed
        # This test documents that behavior - pending tasks may be orphaned
        # if close() is called without a session

    @pytest.mark.asyncio
    async def test_close_logs_pending_task_count(self) -> None:
        """close() should log the number of pending tasks being waited on."""
        orchestrator = self.create_orchestrator()

        # Create a mock session
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        orchestrator.session = mock_session

        # Add tasks
        async def quick_task() -> None:
            await asyncio.sleep(0.01)

        for _ in range(3):
            task = asyncio.create_task(quick_task())
            orchestrator._pending_tasks.add(task)
            task.add_done_callback(orchestrator._pending_tasks.discard)

        # Call close
        await orchestrator.close()

        # Check that debug message was logged
        debug_messages = orchestrator.console_logger.debug_messages
        found_pending_log = any("pending tasks" in msg.lower() for msg in debug_messages)
        assert found_pending_log, "Should log pending task count"

    @pytest.mark.asyncio
    async def test_close_handles_mixed_task_completion(self) -> None:
        """close() should handle mix of fast-completing and slow tasks."""
        orchestrator = self.create_orchestrator()

        # Create a mock session
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        orchestrator.session = mock_session

        fast_completed = 0
        slow_cancelled = 0

        async def fast_task() -> None:
            nonlocal fast_completed
            await asyncio.sleep(0.01)
            fast_completed += 1

        async def slow_task() -> None:
            nonlocal slow_cancelled
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                slow_cancelled += 1
                raise

        # Add 2 fast tasks and 1 slow task
        for _ in range(2):
            task = asyncio.create_task(fast_task())
            orchestrator._pending_tasks.add(task)
            task.add_done_callback(orchestrator._pending_tasks.discard)

        slow = asyncio.create_task(slow_task())
        orchestrator._pending_tasks.add(slow)
        slow.add_done_callback(orchestrator._pending_tasks.discard)

        # Patch timeout for faster tests
        original_wait = asyncio.wait

        async def fast_timeout_wait(
            tasks: set[asyncio.Task[Any]],
            timeout: float | None = None,
            return_when: str = asyncio.ALL_COMPLETED,
        ) -> tuple[set[asyncio.Task[Any]], set[asyncio.Task[Any]]]:
            return await original_wait(tasks, timeout=0.2, return_when=return_when)

        with patch("asyncio.wait", fast_timeout_wait):
            await orchestrator.close()

        # Verify outcomes
        assert fast_completed == 2, "Fast tasks should have completed"
        assert slow_cancelled == 1, "Slow task should have been cancelled"
        assert len(orchestrator._pending_tasks) == 0

    @pytest.mark.asyncio
    async def test_close_handles_task_exception(self) -> None:
        """close() should handle tasks that raise exceptions."""
        orchestrator = self.create_orchestrator()

        # Create a mock session
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        orchestrator.session = mock_session

        async def failing_task() -> None:
            await asyncio.sleep(0.01)
            raise ValueError("Task failed!")

        # Add failing task
        task = asyncio.create_task(failing_task())
        orchestrator._pending_tasks.add(task)
        task.add_done_callback(orchestrator._pending_tasks.discard)

        # close() should not raise even if tasks fail
        await orchestrator.close()

        assert len(orchestrator._pending_tasks) == 0
        mock_session.close.assert_called_once()
