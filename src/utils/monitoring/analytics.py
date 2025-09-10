"""Analytics Module.

Provides performance tracking and analysis for Python applications.
Uses decorators to measure execution time, success rates, and patterns.

Features
--------
- Function performance tracking (sync & async)
- Success and failure monitoring
- Duration categorization (fast / medium / slow)
- HTML report generation (via utils.reports.save_html_report)
- Memory-safe event storage with pruning
- Aggregated statistics and filtering
- Merging data from multiple Analytics instances
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time

from datetime import datetime, timedelta, UTC
from functools import wraps
from typing import TYPE_CHECKING, Any

from src.utils.monitoring.reports import save_html_report

if TYPE_CHECKING:
    from collections.abc import Callable

class TimingInfo:
    """Container for timing-related data."""

    def __init__(self, start: float, end: float, duration: float, overhead: float) -> None:
        """Initialize timing info."""
        self.start = start
        self.end = end
        self.duration = duration
        self.overhead = overhead


class CallInfo:
    """Container for function call metadata."""

    def __init__(self, func_name: str, event_type: str, success: bool) -> None:
        """Initialize call info."""
        self.func_name = func_name
        self.event_type = event_type
        self.success = success


class LoggerContainer:
    """Container for all loggers used by Analytics."""

    def __init__(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics_logger: logging.Logger,
    ) -> None:
        """Initialize logger container."""
        self.console = console_logger
        self.error = error_logger
        self.analytics = analytics_logger


class Analytics:
    """Tracks function performance, success rates, and execution patterns.

    Attributes
    ----------
    instance_id      : unique identifier for this Analytics instance
    events           : list of tracked call events
    call_counts      : dict[func, int] - total calls
    success_counts   : dict[func, int] - successful calls
    decorator_overhead: dict[func, float] - seconds of wrapper overhead
    max_events       : in-memory cap for events (pruned oldest when exceeded)

    """

    # Class-level counter for unique IDs
    _instances = 0

    # Threshold after which GC is suggested post-report
    GC_COLLECTION_THRESHOLD = 5_000

    # Symbols for duration buckets
    _FAST = "⚡"
    _MEDIUM = "⏱️"
    _SLOW = "🐢"
    _DURATION_FIELD = "Duration (s)"  # Field name for duration in analytics events

    # --- Init ---
    def __init__(
        self,
        config: dict[str, Any],
        loggers: LoggerContainer,
        max_events: int | None = None,
    ) -> None:
        """Initialize the Analytics instance."""
        Analytics._instances += 1
        self.instance_id = Analytics._instances

        # Data stores
        self.events: list[dict[str, Any]] = []
        self.call_counts: dict[str, int] = {}
        self.success_counts: dict[str, int] = {}
        self.decorator_overhead: dict[str, float] = {}

        # Config & loggers
        self.config = config
        self.console_logger = loggers.console
        self.error_logger = loggers.error
        self.analytics_logger = loggers.analytics

        # Limits & thresholds
        self.max_events = max_events or config.get("analytics", {}).get("max_events", 10_000)
        thresholds = config.get("analytics", {}).get("duration_thresholds", {})
        self.short_max = thresholds.get("short_max", 2)
        self.medium_max = thresholds.get("medium_max", 5)
        self.long_max = thresholds.get("long_max", 10)

        # Time formatting
        self.time_format = config.get("analytics", {}).get("time_format", "%Y-%m-%d %H:%M:%S")
        self.compact_time = config.get("analytics", {}).get("compact_time", False)

        self.console_logger.debug(f"📊 Analytics #{self.instance_id} initialised")

    # --- Public decorator helpers ---
    def track(self, event_type: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Preferred decorator API - tracks sync/async functions."""
        return self._decorator(event_type)

    @classmethod
    def track_instance_method(
        cls: type[Analytics], event_type: str
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Track instance methods by adding analytics tracking.

        Requires the decorated class to expose `self.analytics` and optional `self.error_logger`.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            """Decorate instance methods."""
            is_async = asyncio.iscoroutinefunction(func)

            @wraps(func)
            async def async_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                """Wrap async functions with analytics tracking."""
                analytics_inst: Analytics | None = getattr(self, "analytics", None)
                if not isinstance(analytics_inst, Analytics):
                    (getattr(self, "error_logger", None) or cls._null_logger()).error(
                        f"Analytics missing on {self.__class__.__name__}; {func.__name__} untracked",
                    )
                    return await func(self, *args, **kwargs)

                return await analytics_inst.execute_async_wrapped_call(
                    func,
                    event_type,
                    self,
                    *args,
                    **kwargs,
                )

            @wraps(func)
            def sync_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                """Wrap sync functions with analytics tracking."""
                analytics_inst: Analytics | None = getattr(self, "analytics", None)
                if not isinstance(analytics_inst, Analytics):
                    (getattr(self, "error_logger", None) or cls._null_logger()).error(
                        f"Analytics missing on {self.__class__.__name__}; {func.__name__} untracked",
                    )
                    return func(self, *args, **kwargs)

                return analytics_inst.execute_sync_wrapped_call(
                    func,
                    event_type,
                    self,
                    *args,
                    **kwargs,
                )

            return async_wrapper if is_async else sync_wrapper

        return decorator

    # --- Internal decorator factory ---
    def _decorator(self, event_type: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator_function(func: Callable[..., Any]) -> Callable[..., Any]:
            """Create decorator for given function."""
            is_async = asyncio.iscoroutinefunction(func)

            if is_async:

                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    """Wrap async functions with tracking."""
                    return await self.execute_async_wrapped_call(func, event_type, *args, **kwargs)

                return wraps(func)(async_wrapper)

            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                """Wrap sync functions with tracking."""
                return self.execute_sync_wrapped_call(func, event_type, *args, **kwargs)

            return wraps(func)(sync_wrapper)

        return decorator_function

    # --- Public API for wrapped calls ---
    async def execute_async_wrapped_call(
        self,
        func: Callable[..., Any],
        event_type: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute an async wrapped function call with analytics tracking.

        Args:
            func: Async function to execute
            event_type: Type of event for tracking
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        """
        return await self._wrapped_call(func, event_type, True, *args, **kwargs)

    def execute_sync_wrapped_call(
        self,
        func: Callable[..., Any],
        event_type: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute a sync wrapped function call with analytics tracking.

        Args:
            func: Sync function to execute
            event_type: Type of event for tracking
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        """
        return asyncio.run(self._wrapped_call(func, event_type, False, *args, **kwargs))

    async def execute_wrapped_call(
        self,
        func: Callable[..., Any],
        event_type: str,
        is_async: bool,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute a wrapped function call with analytics tracking.

        Args:
            func: Function to execute
            event_type: Type of event for tracking
            is_async: Whether the function is async
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        """
        if is_async:
            return await self.execute_async_wrapped_call(func, event_type, *args, **kwargs)
        return self.execute_sync_wrapped_call(func, event_type, *args, **kwargs)

    # --- Core wrapper executor ---
    async def _wrapped_call(
        self,
        func: Callable[..., Any],
        event_type: str,
        is_async: bool,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        func_name = func.__name__
        decorator_start = time.time()
        func_start = decorator_start
        success = False
        try:
            result = await func(*args, **kwargs) if is_async else func(*args, **kwargs)
            success = True
            return result
        except Exception as exc:
            self.error_logger.exception(f"❌ {func_name}: {exc}")
            raise
        finally:
            func_end = time.time()
            decorator_end = func_end
            duration = func_end - func_start
            overhead = decorator_end - decorator_start - duration
            call_info = CallInfo(func_name, event_type, success)
            timing_info = TimingInfo(func_start, func_end, duration, overhead)
            self._record_function_call(call_info, timing_info)

    # --- Event recording & memory management ---
    def _get_duration_symbol(self, duration: float) -> str:
        """Get the appropriate symbol for a given duration.

        Args:
            duration: The duration in seconds

        Returns:
            str: Symbol representing the duration category (fast/medium/slow)

        """
        if duration <= self.short_max:
            return self._FAST
        return self._MEDIUM if duration <= self.medium_max else self._SLOW

    def _record_function_call(
        self,
        call_info: CallInfo,
        timing_info: TimingInfo,
    ) -> None:
        # Prune if exceeding cap
        if 0 < self.max_events <= len(self.events):
            prune = max(1, int(self.max_events * 0.1))
            self.events = self.events[prune:]
            self.console_logger.debug(f"📊 Pruned {prune} old events")

        # Timestamps
        if self.compact_time:
            fmt = "%H:%M:%S"
            start_str = datetime.fromtimestamp(timing_info.start).strftime(fmt)
            end_str = datetime.fromtimestamp(timing_info.end).strftime(fmt)
        else:
            start_str = datetime.fromtimestamp(timing_info.start).strftime(self.time_format)
            end_str = datetime.fromtimestamp(timing_info.end).strftime(self.time_format)

        # Store event
        self.events.append(
            {
                "Function": call_info.func_name,
                "Event Type": call_info.event_type,
                "Start Time": start_str,
                "End Time": end_str,
                self._DURATION_FIELD: round(timing_info.duration, 4),
                "Success": call_info.success,
            },
        )

        # Counters & overhead
        self.call_counts[call_info.func_name] = self.call_counts.get(call_info.func_name, 0) + 1
        if call_info.success:
            self.success_counts[call_info.func_name] = self.success_counts.get(call_info.func_name, 0) + 1
        self.decorator_overhead[call_info.func_name] = self.decorator_overhead.get(call_info.func_name, 0.0) + timing_info.overhead

        # Logging
        symbol = self._get_duration_symbol(timing_info.duration)
        status = "✅" if call_info.success else "❌"
        msg = f"{status} {symbol} {call_info.func_name}({call_info.event_type}) took {timing_info.duration:.3f}s"
        level = "info" if call_info.success and timing_info.duration > self.long_max else "debug"
        getattr(self.analytics_logger, level)(msg)
        if call_info.success:
            getattr(self.console_logger, level)(msg)
        else:
            self.console_logger.warning(msg)

    # --- Stats & summaries ---
    def get_stats(self, function_filter: str | list[str] | None = None) -> dict[str, Any]:
        """Get statistics for analytics data."""
        if function_filter:
            names = {function_filter} if isinstance(function_filter, str) else set(function_filter)
            events = [e for e in self.events if e["Function"] in names]
        else:
            names = set(self.call_counts.keys())
            events = self.events

        total_calls = sum(self.call_counts.get(fn, 0) for fn in names)
        total_success = sum(self.success_counts.get(fn, 0) for fn in names)
        total_time = sum(e[self._DURATION_FIELD] for e in events)
        success_rate = (total_success / total_calls * 100) if total_calls else 0
        avg_duration = (total_time / len(events)) if events else 0

        slowest = max(events, key=lambda e: e[self._DURATION_FIELD], default=None)
        fastest = min(events, key=lambda e: e[self._DURATION_FIELD], default=None)

        duration_counts = {
            "fast": len([e for e in events if e[self._DURATION_FIELD] <= self.short_max]),
            "medium": len([e for e in events if self.short_max < e[self._DURATION_FIELD] <= self.medium_max]),
            "slow": len([e for e in events if e[self._DURATION_FIELD] > self.medium_max]),
        }

        return {
            "total_calls": total_calls,
            "total_success": total_success,
            "success_rate": success_rate,
            "total_time": total_time,
            "avg_duration": avg_duration,
            "slowest": slowest,
            "fastest": fastest,
            "duration_counts": duration_counts,
            "function_count": len(names),
            "event_count": len(events),
        }

    def log_summary(self) -> None:
        """Log a summary of analytics data."""
        stats = self.get_stats()
        self.console_logger.info(
            f"📊 Analytics Summary: {stats['total_calls']} calls | {stats['success_rate']:.1f}% success | avg {stats['avg_duration']:.3f}s",
        )

        dc = stats["duration_counts"]
        total = sum(dc.values()) or 1
        self.console_logger.info(
            f"📊 Performance: "
            f"{self._FAST} {dc['fast'] / total * 100:.0f}% | "
            f"{self._MEDIUM} {dc['medium'] / total * 100:.0f}% | "
            f"{self._SLOW} {dc['slow'] / total * 100:.0f}%",
        )

    # --- Maintenance helpers ---
    def clear_old_events(self, days: int = 7) -> int:
        """Clear old events from the analytics log."""
        if not self.events:
            return 0

        if self.compact_time:
            prune = min(len(self.events) // 2, 1_000)
            self.events = self.events[prune:]
            return prune

        cutoff = datetime.now(UTC) - timedelta(days=days)
        original = len(self.events)
        self.events = [e for e in self.events if datetime.strptime(e["Start Time"], self.time_format) >= cutoff]
        return original - len(self.events)

    def merge_with(self, other: Analytics) -> None:
        """Merge analytics data from another Analytics instance."""
        if other is self:
            return

        # Handle events
        if self.max_events > 0:
            cap = max(0, self.max_events - len(self.events))
            to_add = other.events[: min(cap, len(other.events))]
        else:
            to_add = other.events
        self.events.extend(to_add)

        # Merge call_counts (int values)
        for func_name, count in other.call_counts.items():
            self.call_counts[func_name] = self.call_counts.get(func_name, 0) + count

        # Merge success_counts (int values)
        for func_name, count in other.success_counts.items():
            self.success_counts[func_name] = self.success_counts.get(func_name, 0) + count

        # Merge decorator_overhead (float values)
        for func_name, overhead in other.decorator_overhead.items():
            current_overhead: float = self.decorator_overhead.get(func_name, 0.0)
            self.decorator_overhead[func_name] = current_overhead + float(overhead)

        other.events.clear()
        other.call_counts.clear()
        other.success_counts.clear()
        other.decorator_overhead.clear()

    # --- Reports ---
    def generate_reports(self, force_mode: bool = False) -> None:
        """Generate analytics reports."""
        if not self.events and not self.call_counts:
            self.console_logger.warning("📊 No analytics data; skipping report")
            return

        self.log_summary()

        save_html_report(
            self.events,
            self.call_counts,
            self.success_counts,
            self.decorator_overhead,
            self.config,
            self.console_logger,
            self.error_logger,
            group_successful_short_calls=True,
            force_mode=force_mode,
        )

        if len(self.events) > self.GC_COLLECTION_THRESHOLD:
            gc.collect()

    # --- Utilities ---
    @staticmethod
    def _null_logger() -> logging.Logger:
        logger = logging.getLogger("null")
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        return logger
