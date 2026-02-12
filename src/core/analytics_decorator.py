"""Standalone analytics decorator for instance methods.

Extracted from ``metrics.analytics.Analytics.track_instance_method`` so that
``core/`` no longer imports the concrete ``Analytics`` class at runtime.
The decorator relies on duck typing (``hasattr``) instead of ``isinstance``
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


def _get_func_name(func: Callable[..., Any]) -> str:
    """Safely get function name, handling callables without ``__name__``."""
    return getattr(func, "__name__", repr(func))


def _has_analytics_method(instance: Any, method_name: str) -> bool:
    """Check if analytics instance has a real (non-mock) method.

    Returns False for None, MagicMock, and objects without the method.
    This prevents MagicMock.execute_async_wrapped_call from being treated
    as a real analytics method (MagicMock auto-creates attributes).
    """
    if instance is None:
        return False
    # Check that the method exists on the actual class, not via __getattr__
    return method_name in type(instance).__dict__ or any(method_name in base.__dict__ for base in type(instance).__mro__[1:])


def _null_logger() -> logging.Logger:
    """Return a silent logger for fallback error reporting."""
    logger = logging.getLogger("null")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def track_instance_method(event_type: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Track instance methods by adding analytics tracking.

    Requires the decorated class to expose ``self.analytics`` (any object
    with ``execute_async_wrapped_call`` / ``execute_sync_wrapped_call``
    methods) and an optional ``self.error_logger``.

    Args:
        event_type: Category name for the tracked event

    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        is_async = inspect.iscoroutinefunction(func)

        @wraps(func)
        async def async_wrapper(self_arg: Any, *args: Any, **kwargs: Any) -> Any:
            analytics_inst = getattr(self_arg, "analytics", None)
            if not _has_analytics_method(analytics_inst, "execute_async_wrapped_call"):
                error_logger = getattr(self_arg, "error_logger", None) or _null_logger()
                error_logger.error(
                    "Analytics missing on %s; %s untracked",
                    self_arg.__class__.__name__,
                    _get_func_name(func),
                )
                return await func(self_arg, *args, **kwargs)

            return await analytics_inst.execute_async_wrapped_call(
                func,
                event_type,
                self_arg,
                *args,
                **kwargs,
            )

        @wraps(func)
        def sync_wrapper(self_arg: Any, *args: Any, **kwargs: Any) -> Any:
            analytics_inst = getattr(self_arg, "analytics", None)
            if not _has_analytics_method(analytics_inst, "execute_sync_wrapped_call"):
                error_logger = getattr(self_arg, "error_logger", None) or _null_logger()
                error_logger.error(
                    "Analytics missing on %s; %s untracked",
                    self_arg.__class__.__name__,
                    _get_func_name(func),
                )
                return func(self_arg, *args, **kwargs)

            return analytics_inst.execute_sync_wrapped_call(
                func,
                event_type,
                self_arg,
                *args,
                **kwargs,
            )

        return async_wrapper if is_async else sync_wrapper

    return decorator
