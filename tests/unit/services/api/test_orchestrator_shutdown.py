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
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.api.orchestrator import ExternalApiOrchestrator
from tests.factories import create_test_app_config
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

        test_config = create_test_app_config()

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
            """Simulate a slow-running task that takes 0.1s to complete."""
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
        task_canceled = False

        async def very_slow_task() -> None:
            """Simulate a task that takes longer than the timeout."""
            nonlocal task_started, task_canceled
            task_started = True
            try:
                # This task takes way longer than the 5s timeout
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                task_canceled = True
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
            """Wrapper that uses 0.1s timeout instead of the real timeout.

            Args:
                tasks: Set of tasks to wait for.
                timeout: Original timeout (ignored, uses 0.1s for fast tests).
                return_when: Original return condition (ignored).

            Returns:
                Tuple of (done, pending) task sets.
            """
            # Mark parameters as intentionally unused (interface compatibility)
            _ = timeout, return_when
            return await original_wait(tasks, timeout=0.1, return_when=asyncio.ALL_COMPLETED)

        with patch("asyncio.wait", fast_timeout_wait):
            await orchestrator.close()

        # Verify task was started and canceled
        assert task_started, "Task should have started"
        assert task_canceled, "Task should have been canceled due to timeout"
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
            """Simulate a fast task that completes in 0.01s."""
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
        """close() should handle pending tasks even when session is None.

        The close() method processes pending tasks BEFORE checking session state,
        ensuring proper cleanup regardless of session status.
        """
        orchestrator = self.create_orchestrator()

        # Ensure session is None
        orchestrator.session = None

        # Track task completion
        task_completed = False

        async def orphan_task() -> None:
            """Simulate an orphaned task that runs without active session."""
            nonlocal task_completed
            await asyncio.sleep(0.01)
            task_completed = True

        task = asyncio.create_task(orphan_task())
        orchestrator._pending_tasks.add(task)
        task.add_done_callback(orchestrator._pending_tasks.discard)

        # Call close - should handle pending tasks before checking session
        await orchestrator.close()

        # Verify pending tasks were properly handled
        assert task_completed, "Pending tasks should complete even without session"
        assert len(orchestrator._pending_tasks) == 0, "Pending tasks should be cleared"
        assert task.done(), "Task should be done after close()"

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
            """Simulate a quick task for logging test."""
            await asyncio.sleep(0.01)

        for _ in range(3):
            task = asyncio.create_task(quick_task())
            orchestrator._pending_tasks.add(task)
            task.add_done_callback(orchestrator._pending_tasks.discard)

        # Call close
        await orchestrator.close()

        # Check that debug message was logged
        mock_logger = cast(MockLogger, orchestrator.console_logger)
        found_pending_log = any("pending tasks" in msg.lower() for msg in mock_logger.debug_messages)
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
        slow_canceled = 0

        async def fast_task() -> None:
            """Simulate a fast task that completes quickly."""
            nonlocal fast_completed
            await asyncio.sleep(0.01)
            fast_completed += 1

        async def slow_task() -> None:
            """Simulate a slow task that will be canceled."""
            nonlocal slow_canceled
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                slow_canceled += 1
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
            """Wrapper that uses 0.2s timeout instead of the real timeout.

            Args:
                tasks: Set of tasks to wait for.
                timeout: Original timeout (ignored, uses 0.2s for fast tests).
                return_when: Original return condition (ignored).

            Returns:
                Tuple of (done, pending) task sets.
            """
            # Mark parameters as intentionally unused (interface compatibility)
            _ = timeout, return_when
            return await original_wait(tasks, timeout=0.2, return_when=asyncio.ALL_COMPLETED)

        with patch("asyncio.wait", fast_timeout_wait):
            await orchestrator.close()

        # Verify outcomes
        assert fast_completed == 2, "Fast tasks should have completed"
        assert slow_canceled == 1, "Slow task should have been canceled"
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
            """Simulate a task that raises an exception."""
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
