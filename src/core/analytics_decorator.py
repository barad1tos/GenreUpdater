"""Standalone analytics decorator for instance methods.

Extracted from ``metrics.analytics.Analytics.track_instance_method`` so that
``core/`` no longer imports the concrete ``Analytics`` class at runtime.
The decorator relies on duck typing instead of ``isinstance``
to remain compatible with any object that exposes
``execute_async_wrapped_call`` / ``execute_sync_wrapped_call``.
"""

from __future__ import annotations

import inspect
import logging
from functools import wraps
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.models.protocols import AnalyticsProtocol


def _get_func_name(func: Callable[..., Any]) -> str:
    """Safely get function name, handling callables without ``__name__``."""
    return getattr(func, "__name__", repr(func))


def _null_logger() -> logging.Logger:
    """Return a silent logger for fallback error reporting."""
    logger = logging.getLogger("null")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def _get_analytics(instance: Any, method_name: str) -> AnalyticsProtocol | None:
    """Return the analytics object if it has a real (non-mock) method.

    Returns None for MagicMock and objects without the method.
    MagicMock auto-creates attributes via ``__getattr__``, so we check
    that the method exists in the actual class ``__dict__`` via MRO.
    """
    analytics_inst = getattr(instance, "analytics", None)
    if analytics_inst is None:
        return None
    has_method = any(method_name in cls.__dict__ for cls in type(analytics_inst).__mro__)
    return analytics_inst if has_method else None


def track_instance_method(event_type: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Track instance methods by adding analytics tracking.

    Requires the decorated class to expose ``self.analytics`` (any object
    with ``execute_async_wrapped_call`` / ``execute_sync_wrapped_call``
    methods) and an optional ``self.error_logger``.

    Args:
        event_type: Category name for the tracked event

    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap function with analytics tracking based on sync/async type."""
        is_async = inspect.iscoroutinefunction(func)

        @wraps(func)
        async def async_wrapper(self_arg: Any, *args: Any, **kwargs: Any) -> Any:
            """Wrap async method with analytics tracking."""
            analytics = _get_analytics(self_arg, "execute_async_wrapped_call")
            if analytics is None:
                error_logger = getattr(self_arg, "error_logger", None) or _null_logger()
                error_logger.error(
                    "Analytics missing on %s; %s untracked",
                    self_arg.__class__.__name__,
                    _get_func_name(func),
                )
                return await func(self_arg, *args, **kwargs)

            return await analytics.execute_async_wrapped_call(
                func,
                event_type,
                self_arg,
                *args,
                **kwargs,
            )

        @wraps(func)
        def sync_wrapper(self_arg: Any, *args: Any, **kwargs: Any) -> Any:
            """Wrap sync method with analytics tracking."""
            analytics = _get_analytics(self_arg, "execute_sync_wrapped_call")
            if analytics is None:
                error_logger = getattr(self_arg, "error_logger", None) or _null_logger()
                error_logger.error(
                    "Analytics missing on %s; %s untracked",
                    self_arg.__class__.__name__,
                    _get_func_name(func),
                )
                return func(self_arg, *args, **kwargs)

            return analytics.execute_sync_wrapped_call(
                func,
                event_type,
                self_arg,
                *args,
                **kwargs,
            )

        return async_wrapper if is_async else sync_wrapper

    return decorator
