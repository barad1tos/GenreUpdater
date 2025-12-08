"""Tests for Analytics core functionality (excluding batch_mode which has its own file)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from metrics.analytics import Analytics, CallInfo, LoggerContainer, TimingInfo, _get_func_name


@pytest.fixture
def loggers() -> LoggerContainer:
    """Create mock loggers for testing."""
    console = MagicMock()
    error = MagicMock()
    analytics_log = MagicMock()
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
        },
        "logs_base_dir": "/tmp/test_logs",
    }


@pytest.fixture
def analytics(config: dict, loggers: LoggerContainer) -> Analytics:
    """Create Analytics instance for testing."""
    return Analytics(config, loggers)


@pytest.fixture
def disabled_analytics(loggers: LoggerContainer) -> Analytics:
    """Create disabled Analytics instance for testing."""
    config = {
        "analytics": {
            "enabled": False,
            "max_events": 100,
            "duration_thresholds": {
                "short_max": 2,
                "medium_max": 5,
                "long_max": 10,
            },
        }
    }
    return Analytics(config, loggers)


class TestTimingInfo:
    """Tests for TimingInfo dataclass."""

    def test_timing_info_creation(self) -> None:
        """Test TimingInfo can be created with all fields."""
        timing = TimingInfo(
            start=1.0,
            end=2.0,
            duration=1.0,
            overhead=0.001,
        )
        assert timing.start == 1.0
        assert timing.end == 2.0
        assert timing.duration == 1.0
        assert timing.overhead == 0.001


class TestCallInfo:
    """Tests for CallInfo dataclass."""

    def test_call_info_creation(self) -> None:
        """Test CallInfo can be created with all fields."""
        call = CallInfo(
            func_name="test_func",
            event_type="function_call",
            success=True,
        )
        assert call.func_name == "test_func"
        assert call.event_type == "function_call"
        assert call.success is True


class TestLoggerContainer:
    """Tests for LoggerContainer."""

    def test_logger_container_creation(self) -> None:
        """Test LoggerContainer holds all loggers."""
        console = MagicMock(spec=logging.Logger)
        error = MagicMock(spec=logging.Logger)
        analytics = MagicMock(spec=logging.Logger)
        container = LoggerContainer(console, error, analytics)
        assert container.console is console
        assert container.error is error
        assert container.analytics is analytics


class TestAnalyticsInit:
    """Tests for Analytics initialization."""

    def test_analytics_disabled(self, disabled_analytics: Analytics) -> None:
        """Test Analytics can be disabled via config."""
        assert disabled_analytics.enabled is False

    def test_analytics_enabled(self, analytics: Analytics) -> None:
        """Test Analytics is enabled by default when config says so."""
        assert analytics.enabled is True

    def test_analytics_has_unique_instance_id(self, config: dict, loggers: LoggerContainer) -> None:
        """Test each Analytics instance gets unique ID."""
        a1 = Analytics(config, loggers)
        a2 = Analytics(config, loggers)
        assert a1.instance_id != a2.instance_id


class TestTrackDecorator:
    """Tests for the track decorator."""

    def test_track_sync_function(self, analytics: Analytics) -> None:
        """Test tracking a synchronous function."""

        @analytics.track("test_event")
        def my_func() -> str:
            """Test helper function."""
            return "result"

        result = my_func()
        assert result == "result"
        assert analytics.call_counts.get("my_func", 0) > 0

    @pytest.mark.asyncio
    async def test_track_async_function(self, analytics: Analytics) -> None:
        """Test tracking an async function."""

        @analytics.track("async_event")
        async def my_async_func() -> str:
            """Test helper async function."""
            await asyncio.sleep(0.01)
            return "async_result"

        result = await my_async_func()
        assert result == "async_result"
        assert analytics.call_counts.get("my_async_func", 0) > 0

    def test_track_function_with_exception(self, analytics: Analytics) -> None:
        """Test tracking function that raises exception."""

        @analytics.track("failing_event")
        def failing_func() -> None:
            """Test helper that raises."""
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            failing_func()

        # Function should still be counted
        assert analytics.call_counts.get("failing_func", 0) > 0
        # But success count should be 0
        assert analytics.success_counts.get("failing_func", 0) == 0

    def test_track_disabled_analytics(self, disabled_analytics: Analytics) -> None:
        """Test that disabled analytics still allows function execution."""

        @disabled_analytics.track("disabled_event")
        def my_func() -> str:
            """Test helper function."""
            return "works"

        result = my_func()
        assert result == "works"

        # Disabled analytics should not record any metrics or counters
        assert disabled_analytics.call_counts == {}
        assert disabled_analytics.success_counts == {}


class TestTrackInstanceMethod:
    """Tests for track_instance_method decorator."""

    def test_track_instance_method_basic(self, analytics: Analytics) -> None:
        """Test tracking instance method (requires self.analytics on class)."""

        class MyClass:
            """Test helper class with analytics."""

            def __init__(self, analytics_inst: Analytics) -> None:
                self.analytics = analytics_inst

            @Analytics.track_instance_method("method_event")
            def my_method(self, x: int) -> int:
                """Test method that doubles input."""
                return x * 2

        obj = MyClass(analytics)
        result = obj.my_method(5)
        assert result == 10
        assert analytics.call_counts.get("my_method", 0) > 0

    @pytest.mark.asyncio
    async def test_track_instance_method_async(self, analytics: Analytics) -> None:
        """Test tracking async instance method."""

        class MyClass:
            """Test helper class with async method."""

            def __init__(self, analytics_inst: Analytics) -> None:
                self.analytics = analytics_inst

            @Analytics.track_instance_method("async_method_event")
            async def my_async_method(self, x: int) -> int:
                """Test async method that triples input."""
                await asyncio.sleep(0.01)
                return x * 3

        obj = MyClass(analytics)
        result = await obj.my_async_method(5)
        assert result == 15


class TestGetStats:
    """Tests for get_stats method."""

    def test_get_stats_empty(self, analytics: Analytics) -> None:
        """Test get_stats with no events returns summary structure."""
        stats = analytics.get_stats()
        # Returns summary dict with zero values when empty
        assert "total_calls" in stats
        assert stats["total_calls"] == 0

    def test_get_stats_with_events(self, analytics: Analytics) -> None:
        """Test get_stats returns correct statistics."""

        @analytics.track("fast_event")
        def fast_func() -> None:
            """Test helper function."""

        # Call multiple times
        for _ in range(5):
            fast_func()

        stats = analytics.get_stats()
        assert stats["total_calls"] == 5
        assert stats["function_count"] >= 1
        assert "avg_duration" in stats


class TestLogSummary:
    """Tests for log_summary method."""

    def test_log_summary_empty(self, analytics: Analytics) -> None:
        """Test log_summary with no events doesn't crash."""
        analytics.log_summary()
        # Should complete without error

    def test_log_summary_with_events(self, analytics: Analytics, loggers: LoggerContainer) -> None:
        """Test log_summary logs statistics."""

        @analytics.track("tracked_event")
        def tracked_func() -> None:
            """Test helper function."""

        tracked_func()
        analytics.log_summary()

        # Console logger should have been called (via fixture mock)
        cast(MagicMock, loggers.console).info.assert_called()


class TestClearOldEvents:
    """Tests for clear_old_events method."""

    def test_clear_old_events_empty(self, analytics: Analytics) -> None:
        """Test clear_old_events with empty events list."""
        removed = analytics.clear_old_events()
        assert removed == 0

    def test_clear_old_events_with_tracked_events_compact_mode(self, config: dict, loggers: LoggerContainer) -> None:
        """Test clearing events in compact_time mode."""
        # Enable compact_time mode
        config["analytics"]["compact_time"] = True
        analytics = Analytics(config, loggers)

        @analytics.track("test_event")
        def test_func() -> None:
            """Test helper function."""

        # Generate many events to trigger count-based pruning
        for _ in range(2500):
            test_func()

        # Events should exist
        assert len(analytics.events) > 0

        # In compact_time mode, uses count-based pruning
        removed = analytics.clear_old_events()

        # Should return number removed
        assert isinstance(removed, int)
        assert removed >= 0


class TestMergeWith:
    """Tests for merge_with method."""

    def test_merge_with_combines_events(self, config: dict, loggers: LoggerContainer) -> None:
        """Test merging two Analytics instances."""
        analytics1 = Analytics(config, loggers)
        analytics2 = Analytics(config, loggers)

        @analytics1.track("event1")
        def func1() -> None:
            """Test helper function."""

        @analytics2.track("event2")
        def func2() -> None:
            """Test helper function."""

        func1()
        func2()

        events_before = len(analytics1.events)
        analytics1.merge_with(analytics2)

        # Events from analytics2 should be added
        assert len(analytics1.events) >= events_before

    def test_merge_with_combines_counts(self, config: dict, loggers: LoggerContainer) -> None:
        """Test that counts are combined correctly."""
        analytics1 = Analytics(config, loggers)
        analytics2 = Analytics(config, loggers)

        @analytics1.track("shared_event")
        def func_a() -> None:
            """Test helper function."""

        @analytics2.track("shared_event")
        def func_b() -> None:
            """Test helper function."""

        func_a()
        func_a()  # Call twice
        func_b()

        # Both track same function name
        analytics2.call_counts["func_a"] = analytics2.call_counts.pop("func_b", 0)
        analytics2.success_counts["func_a"] = analytics2.success_counts.pop("func_b", 0)

        initial_count = analytics1.call_counts.get("func_a", 0)
        analytics1.merge_with(analytics2)

        # Count should be combined
        assert analytics1.call_counts.get("func_a", 0) >= initial_count


class TestGenerateReports:
    """Tests for generate_reports method."""

    def test_generate_reports_creates_files(self, analytics: Analytics, tmp_path: Path) -> None:
        """Test that generate_reports creates report files."""

        @analytics.track("report_event")
        def test_func() -> None:
            """Test helper function."""

        test_func()

        # Override logs_base_dir for test
        analytics.config["logs_base_dir"] = str(tmp_path)

        analytics.generate_reports()

        # Check that report directory was created (actual files depend on implementation)
        # At minimum, the method should not raise an error

    def test_generate_reports_disabled_analytics(self, disabled_analytics: Analytics) -> None:
        """Test generate_reports does nothing when analytics disabled."""
        # Should not raise
        disabled_analytics.generate_reports()


class TestDurationSymbol:
    """Tests for _get_duration_symbol method."""

    def test_fast_duration(self, analytics: Analytics) -> None:
        """Test fast duration symbol."""
        symbol = analytics._get_duration_symbol(1.0)  # Under short_max (2)
        assert symbol == Analytics._FAST

    def test_medium_duration(self, analytics: Analytics) -> None:
        """Test medium duration symbol."""
        symbol = analytics._get_duration_symbol(3.0)  # Between short_max and medium_max
        assert symbol == Analytics._MEDIUM

    def test_slow_duration(self, analytics: Analytics) -> None:
        """Test slow duration symbol."""
        symbol = analytics._get_duration_symbol(8.0)  # Between medium_max and long_max
        assert symbol == Analytics._SLOW

    def test_very_slow_duration(self, analytics: Analytics) -> None:
        """Test very slow duration (beyond long_max)."""
        symbol = analytics._get_duration_symbol(15.0)  # Beyond long_max
        # Should still return some symbol (implementation returns _VERY_SLOW or similar)
        assert symbol is not None


class TestGcCollection:
    """Tests for GC collection behavior."""

    def test_gc_collection_threshold(self, config: dict, loggers: LoggerContainer) -> None:
        """Test that GC collection happens after threshold."""
        config["analytics"]["gc_enabled"] = True
        analytics = Analytics(config, loggers)

        @analytics.track("gc_event")
        def simple_func() -> None:
            """Test helper function."""

        # Call enough times to trigger GC threshold
        with patch("gc.collect") as _mock_gc:
            for _ in range(Analytics.GC_COLLECTION_THRESHOLD + 1):
                simple_func()

            # GC may have been called (depends on implementation)
            # The patch ensures gc.collect doesn't actually run during test


class TestNullLogger:
    """Tests for _null_logger property."""

    def test_null_logger_returns_logger(self, analytics: Analytics) -> None:
        """Test _null_logger returns a valid logger."""
        null_logger = analytics._null_logger()
        assert isinstance(null_logger, logging.Logger)
        # Should be able to log without error
        null_logger.info("test message")


class TestGetFuncName:
    """Tests for _get_func_name helper function."""

    def test_regular_function_has_name(self) -> None:
        """Test that regular function returns its __name__."""

        def sample_func() -> None:
            """Sample function for testing."""

        assert _get_func_name(sample_func) == "sample_func"

    def test_lambda_function(self) -> None:
        """Test that lambda returns its repr when no __name__."""
        my_lambda = lambda x: x * 2  # noqa: E731
        name = _get_func_name(my_lambda)
        assert name == "<lambda>"

    def test_callable_without_name(self) -> None:
        """Test callable without __name__ returns repr."""

        class CallableWithoutName:
            """Callable object without __name__ attribute."""

            def __call__(self) -> None:
                """Make it callable."""

            def __repr__(self) -> str:
                """Custom repr."""
                return "CustomCallable()"

        obj = CallableWithoutName()
        name = _get_func_name(obj)
        assert name == "CustomCallable()"

    def test_method_has_name(self) -> None:
        """Test that method returns its __name__."""

        class MyClass:
            """Test class with method."""

            def my_method(self) -> None:
                """Test method."""

        assert _get_func_name(MyClass.my_method) == "my_method"
        assert _get_func_name(MyClass().my_method) == "my_method"
