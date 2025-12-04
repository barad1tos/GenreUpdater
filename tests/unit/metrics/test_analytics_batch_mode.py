"""Tests for Analytics batch_mode context manager."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.metrics.analytics import Analytics, CallInfo, LoggerContainer, TimingInfo

if TYPE_CHECKING:
    pass


@pytest.fixture
def loggers() -> LoggerContainer:
    """Create mock loggers for testing."""
    console = MagicMock(spec=logging.Logger)
    error = MagicMock(spec=logging.Logger)
    analytics_log = MagicMock(spec=logging.Logger)
    return LoggerContainer(console, error, analytics_log)


@pytest.fixture
def config() -> dict:
    """Create minimal config for testing."""
    return {
        "analytics": {
            "enabled": True,
            "max_events": 1000,
            "duration_thresholds": {
                "short_max": 2,
                "medium_max": 5,
                "long_max": 10,
            },
        }
    }


@pytest.fixture
def analytics(config: dict, loggers: LoggerContainer) -> Analytics:
    """Create Analytics instance for testing."""
    return Analytics(config, loggers)


class TestBatchModeInit:
    """Tests for batch mode initialization."""

    def test_suppress_console_logging_default_false(self, analytics: Analytics) -> None:
        """Batch mode should be disabled by default."""
        assert analytics._suppress_console_logging is False

    def test_batch_call_count_default_zero(self, analytics: Analytics) -> None:
        """Batch call count should start at zero."""
        assert analytics._batch_call_count == 0

    def test_batch_total_duration_default_zero(self, analytics: Analytics) -> None:
        """Batch total duration should start at zero."""
        assert analytics._batch_total_duration == 0.0


class TestBatchModeContextManager:
    """Tests for batch_mode context manager behavior."""

    @pytest.mark.asyncio
    async def test_enables_suppress_logging_inside_context(self, analytics: Analytics) -> None:
        """Should enable console logging suppression inside context."""
        assert analytics._suppress_console_logging is False

        async with analytics.batch_mode("Test"):
            assert analytics._suppress_console_logging is True

        assert analytics._suppress_console_logging is False

    @pytest.mark.asyncio
    async def test_resets_batch_stats_on_entry(self, analytics: Analytics) -> None:
        """Should reset batch stats when entering context."""
        analytics._batch_call_count = 5
        analytics._batch_total_duration = 100.0

        async with analytics.batch_mode("Test"):
            assert analytics._batch_call_count == 0
            assert analytics._batch_total_duration == 0.0

    @pytest.mark.asyncio
    async def test_resets_batch_stats_on_exit(self, analytics: Analytics) -> None:
        """Should reset batch stats when exiting context."""
        async with analytics.batch_mode("Test"):
            analytics._batch_call_count = 10
            analytics._batch_total_duration = 50.0

        assert analytics._batch_call_count == 0
        assert analytics._batch_total_duration == 0.0

    @pytest.mark.asyncio
    async def test_yields_status_object(self, analytics: Analytics) -> None:
        """Should yield a Rich Status object."""
        async with analytics.batch_mode("Test") as status:
            assert status is not None
            # Status should have update method
            assert hasattr(status, "update")

    @pytest.mark.asyncio
    async def test_status_can_be_updated(self, analytics: Analytics) -> None:
        """Should allow updating status message."""
        async with analytics.batch_mode("Initial") as status:
            # This should not raise
            status.update("[cyan]Updated message[/cyan]")

    @pytest.mark.asyncio
    async def test_logs_summary_on_exit_with_calls(
        self, analytics: Analytics, loggers: LoggerContainer
    ) -> None:
        """Should log summary when exiting with recorded calls."""
        async with analytics.batch_mode("Test"):
            # Simulate recorded calls
            analytics._batch_call_count = 5
            analytics._batch_total_duration = 25.0

        # Should have logged summary
        loggers.console.info.assert_called()
        # Check that the format string and args contain batch info
        call_args = loggers.console.info.call_args
        format_str = call_args[0][0]
        assert "Batch completed" in format_str
        # Check the args contain the call count (5)
        assert call_args[0][1] == 5

    @pytest.mark.asyncio
    async def test_no_summary_log_when_no_calls(
        self, analytics: Analytics, loggers: LoggerContainer
    ) -> None:
        """Should not log summary when no calls were recorded."""
        loggers.console.info.reset_mock()

        async with analytics.batch_mode("Test"):
            pass  # No calls recorded

        # Should not have logged batch summary (batch_call_count == 0)
        for call in loggers.console.info.call_args_list:
            assert "Batch completed" not in str(call)

    @pytest.mark.asyncio
    async def test_restores_state_on_exception(self, analytics: Analytics) -> None:
        """Should restore state even if exception occurs inside context."""
        with pytest.raises(ValueError):
            async with analytics.batch_mode("Test"):
                analytics._batch_call_count = 5
                raise ValueError("Test error")

        assert analytics._suppress_console_logging is False
        assert analytics._batch_call_count == 0


class TestRecordFunctionCallInBatchMode:
    """Tests for _record_function_call behavior in batch mode."""

    def test_tracks_batch_stats_when_suppressed(self, analytics: Analytics) -> None:
        """Should track batch stats when console logging is suppressed."""
        analytics._suppress_console_logging = True

        call_info = CallInfo("test_func", "test_event", True)
        timing_info = TimingInfo(0, 15, 15.0, 0.001)

        analytics._record_function_call(call_info, timing_info)

        assert analytics._batch_call_count == 1
        assert analytics._batch_total_duration == 15.0

    def test_accumulates_batch_stats(self, analytics: Analytics) -> None:
        """Should accumulate batch stats across multiple calls."""
        analytics._suppress_console_logging = True

        for i in range(3):
            call_info = CallInfo("test_func", "test_event", True)
            timing_info = TimingInfo(0, 10, 10.0, 0.001)
            analytics._record_function_call(call_info, timing_info)

        assert analytics._batch_call_count == 3
        assert analytics._batch_total_duration == 30.0

    def test_skips_console_logging_when_suppressed(
        self, analytics: Analytics, loggers: LoggerContainer
    ) -> None:
        """Should skip console logging when in batch mode."""
        analytics._suppress_console_logging = True
        loggers.console.info.reset_mock()
        loggers.console.debug.reset_mock()

        # Long duration that would normally log to console
        call_info = CallInfo("test_func", "test_event", True)
        timing_info = TimingInfo(0, 15, 15.0, 0.001)

        analytics._record_function_call(call_info, timing_info)

        # Console logger should not have been called
        loggers.console.info.assert_not_called()
        loggers.console.debug.assert_not_called()

    def test_still_logs_to_analytics_file_when_suppressed(
        self, analytics: Analytics, loggers: LoggerContainer
    ) -> None:
        """Should still log to analytics file even in batch mode."""
        analytics._suppress_console_logging = True

        call_info = CallInfo("test_func", "test_event", True)
        timing_info = TimingInfo(0, 15, 15.0, 0.001)

        analytics._record_function_call(call_info, timing_info)

        # Analytics logger should still be called
        assert loggers.analytics.info.called or loggers.analytics.debug.called

    def test_still_records_events_when_suppressed(self, analytics: Analytics) -> None:
        """Should still record events to events list in batch mode."""
        analytics._suppress_console_logging = True

        call_info = CallInfo("test_func", "test_event", True)
        timing_info = TimingInfo(0, 15, 15.0, 0.001)

        analytics._record_function_call(call_info, timing_info)

        assert len(analytics.events) == 1
        assert analytics.events[0]["Function"] == "test_func"

    def test_normal_logging_when_not_suppressed(
        self, analytics: Analytics, loggers: LoggerContainer
    ) -> None:
        """Should log normally when not in batch mode."""
        assert analytics._suppress_console_logging is False

        # Long duration that triggers info logging
        call_info = CallInfo("test_func", "test_event", True)
        timing_info = TimingInfo(0, 15, 15.0, 0.001)

        analytics._record_function_call(call_info, timing_info)

        # Console logger should have been called
        assert loggers.console.info.called or loggers.console.debug.called


class TestBatchModeIntegration:
    """Integration tests for batch_mode with real tracked calls."""

    @pytest.mark.asyncio
    async def test_full_batch_workflow(self, analytics: Analytics) -> None:
        """Test complete batch mode workflow with tracked functions."""

        @analytics.track("test_operation")
        async def slow_operation() -> str:
            await asyncio.sleep(0.01)
            return "done"

        async with analytics.batch_mode("Processing...") as status:
            for i in range(3):
                status.update(f"[cyan]Step {i + 1}/3[/cyan]")
                await slow_operation()

        # Events should be recorded
        assert len(analytics.events) >= 3

        # Batch stats should be reset after context
        assert analytics._batch_call_count == 0
        assert analytics._batch_total_duration == 0.0

    @pytest.mark.asyncio
    async def test_nested_batch_mode_not_supported(self, analytics: Analytics) -> None:
        """Nested batch_mode should work but outer stats get reset."""
        async with analytics.batch_mode("Outer"):
            analytics._batch_call_count = 5

            async with analytics.batch_mode("Inner"):
                # Inner resets stats
                assert analytics._batch_call_count == 0

            # After inner exits, stats are reset again
            assert analytics._batch_call_count == 0
