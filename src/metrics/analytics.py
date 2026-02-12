"""Analytics Module.

Provides performance tracking and analysis for Python applications.
Uses decorators to measure execution time, success rates, and patterns.

Features
--------
- Function performance tracking (sync and async)
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
import inspect
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import TYPE_CHECKING, Any

from core.logger import get_shared_console
from metrics.change_reports import save_html_report

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from core.models.track_models import AppConfig
    from rich.console import Console
    from rich.status import Status


def _get_func_name(func: Callable[..., Any]) -> str:
    """Safely get function name, handling callables without __name__."""
    return getattr(func, "__name__", repr(func))


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
    _FAST = ">>>"
    _MEDIUM = ">>"
    _SLOW = ">"
    _DURATION_FIELD = "Duration (s)"  # Field name for duration in analytics events

    # --- Init ---
    def __init__(
        self,
        config: AppConfig,
        loggers: LoggerContainer,
        max_events: int | None = None,
    ) -> None:
        """Initialize the Analytics instance."""
        Analytics._instances += 1
        self.instance_id = Analytics._instances

        # Check if analytics is enabled (default: True)
        self.enabled = config.analytics.enabled

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
        self.max_events = max_events or config.analytics.max_events
        thresholds = config.analytics.duration_thresholds
        self.short_max = thresholds.short_max
        self.medium_max = thresholds.medium_max
        self.long_max = thresholds.long_max

        # Time formatting
        self.time_format = config.analytics.time_format
        self.compact_time = config.analytics.compact_time

        # Performance options
        self.enable_gc_collect = config.analytics.enable_gc_collect

        # Batch mode state (suppresses console logging, shows spinner instead)
        self._suppress_console_logging = False
        self._batch_call_count = 0
        self._batch_total_duration = 0.0

        # Shared console for coordinated Rich output
        self._console = get_shared_console()

        self.console_logger.debug(f"Analytics #{self.instance_id} initialized")

    # --- Public decorator helpers ---
    def track(self, event_type: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Preferred decorator API - tracks sync/async functions."""
        return self._decorator(event_type)

    # --- Internal decorator factory ---
    def _decorator(self, event_type: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator_function(func: Callable[..., Any]) -> Callable[..., Any]:
            """Create decorator for given function."""
            is_async = inspect.iscoroutinefunction(func)

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

        Note:
            Cannot be called from within an active event loop. If you need to call
            a sync function from async context, call it directly or use an async wrapper.

        """
        try:
            return asyncio.run(self._wrapped_call(func, event_type, False, *args, **kwargs))
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                func_name = _get_func_name(func)
                self.console_logger.warning(f"Cannot track {func_name} with asyncio.run() from within event loop; executing without tracking")
                # Execute function directly without tracking to avoid blocking
                return func(*args, **kwargs)
            raise

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
        func_name = _get_func_name(func)
        decorator_start = time.time()
        func_start = decorator_start
        success = False
        try:
            result = await func(*args, **kwargs) if is_async else func(*args, **kwargs)
            success = True
            return result
        except Exception as exc:
            self.error_logger.exception(f"{func_name}: {exc}")
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
        # Skip recording if analytics is disabled
        if not self.enabled:
            return

        # Prune if exceeding cap (batch size: at least 5 or 10% for efficiency)
        if 0 < self.max_events <= len(self.events):
            prune = max(5, int(self.max_events * 0.1))
            self.events = self.events[prune:]
            self.console_logger.debug(f"Pruned {prune} old events")

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

        # Track batch mode stats
        if self._suppress_console_logging:
            self._batch_call_count += 1
            self._batch_total_duration += timing_info.duration

        # Logging
        symbol = self._get_duration_symbol(timing_info.duration)
        status = "✅" if call_info.success else "❌"
        msg = f"{status} {symbol} {call_info.func_name}({call_info.event_type}) took {timing_info.duration:.3f}s"
        level = "info" if call_info.success and timing_info.duration > self.long_max else "debug"

        # Always log to analytics file
        getattr(self.analytics_logger, level)(msg)

        # Skip console logging in batch mode (spinner shows progress instead)
        if self._suppress_console_logging:
            return

        if call_info.success:
            getattr(self.console_logger, level)(msg)
        else:
            self.console_logger.warning(msg)

    # --- Batch mode context manager ---
    @asynccontextmanager
    async def batch_mode(
        self,
        message: str = "Processing...",
        console: Console | None = None,
    ) -> AsyncIterator[Status]:
        """Context manager that suppresses console logging and shows a spinner instead.

        Use this when executing many tracked operations in a loop to avoid
        flooding the console with individual timing logs.

        Args:
            message: Message to display next to the spinner
            console: Optional Rich Console instance

        Yields:
            Rich Status object that can be updated with progress info

        Example:
            async with analytics.batch_mode("Fetching tracks...") as status:
                for i, batch in enumerate(batches):
                    status.update(f"[cyan]Fetching batch {i+1}/{len(batches)}...[/cyan]")
                    await fetch_batch(batch)
        """
        _console = console or self._console
        self._suppress_console_logging = True
        self._batch_call_count = 0
        self._batch_total_duration = 0.0
        start_time = time.time()

        try:
            with _console.status(f"[cyan]{message}[/cyan]") as status:
                yield status
        finally:
            self._suppress_console_logging = False
            elapsed = time.time() - start_time

            # Log summary after batch completes
            if self._batch_call_count > 0:
                avg_duration = self._batch_total_duration / self._batch_call_count
                self.console_logger.info(
                    "✅ Batch completed: %d calls in %.1fs (avg %.1fs/call)",
                    self._batch_call_count,
                    elapsed,
                    avg_duration,
                )

            # Reset batch stats
            self._batch_call_count = 0
            self._batch_total_duration = 0.0

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
        if not self.enabled:
            return
        stats = self.get_stats()
        self.console_logger.info(
            f"Analytics Summary: {stats['total_calls']} calls | {stats['success_rate']:.1f}% success | avg {stats['avg_duration']:.3f}s",
        )

        dc = stats["duration_counts"]
        total = sum(dc.values()) or 1
        self.console_logger.info(
            f"Performance: "
            f"{self._FAST} {dc['fast'] / total * 100:.0f}% | "
            f"{self._MEDIUM} {dc['medium'] / total * 100:.0f}% | "
            f"{self._SLOW} {dc['slow'] / total * 100:.0f}%",
        )

    # --- Maintenance helpers ---
    def clear_old_events(self, days: int = 7) -> int:
        """Clear old events from the analytics log.

        Args:
            days: Number of days to keep (only applies when compact_time=False)

        Returns:
            Number of events removed

        Note:
            When compact_time=True, uses count-based pruning (removes oldest half or 1000 events)
            instead of age-based pruning, since timestamps don't include dates.

        """
        if not self.events:
            return 0

        if self.compact_time:
            self.console_logger.warning("Age-based pruning not supported in compact_time mode; using count-based pruning")
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

        # Handle events with cap enforcement
        if self.max_events > 0:
            cap = max(0, self.max_events - len(self.events))
            num_to_add = min(cap, len(other.events))
            to_add = other.events[-num_to_add:] if num_to_add > 0 else []
            num_dropped = len(other.events) - num_to_add
            if num_dropped > 0:
                self.console_logger.warning(f"Dropped {num_dropped} events during merge due to max_events={self.max_events} limit")
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
        """Generate analytics reports.

        Args:
            force_mode: Force report generation even if criteria not met

        Note:
            Garbage collection is triggered after report generation if enabled
            via config (analytics.enable_gc_collect) and event count exceeds threshold.

        """
        # Skip report generation if analytics is disabled
        if not self.enabled:
            return

        if not self.events and not self.call_counts:
            self.console_logger.warning("No analytics data; skipping report")
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

        if len(self.events) > self.GC_COLLECTION_THRESHOLD and self.enable_gc_collect:
            gc.collect()

    # --- Utilities ---
    @staticmethod
    def _null_logger() -> logging.Logger:
        logger = logging.getLogger("null")
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        return logger
