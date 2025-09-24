"""Global debug utilities for centralized debug control.

This module provides centralized debug logging functionality that can be
globally enabled/disabled via configuration.
"""

import logging
from typing import Any


# Debug state container to avoid global statement
class _DebugState:
    """Container for debug mode state."""

    enabled: bool = False


_debug_state = _DebugState()


def init_debug_mode(enabled: bool) -> None:
    """Initialize debug mode from configuration.

    Args:
        enabled: Whether debug mode should be enabled

    """
    _debug_state.enabled = enabled


def is_debug_enabled() -> bool:
    """Check if debug mode is enabled.

    Returns:
        True if debug logging should be performed

    """
    return _debug_state.enabled


def debug_log(logger: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
    """Log a debug message only if debug mode is enabled.

    Args:
        logger: Logger instance to use
        message: Message to log (with format placeholders)
        *args: Arguments for message formatting
        **kwargs: Additional logger kwargs

    """
    if _debug_state.enabled:
        logger.info(message, *args, **kwargs)


def debug_info(logger: logging.Logger, tag: str, message: str, *args: Any, **kwargs: Any) -> None:
    """Log a tagged debug message only if debug mode is enabled.

    Args:
        logger: Logger instance to use
        tag: Debug tag (e.g., "YEAR_DEBUG", "CACHE_DEBUG")
        message: Message to log (with format placeholders)
        *args: Arguments for message formatting
        **kwargs: Additional logger kwargs

    """
    if _debug_state.enabled:
        tagged_message = f"[{tag}] {message}"
        logger.info(tagged_message, *args, **kwargs)


def debug_error(logger: logging.Logger, tag: str, message: str, *args: Any, **kwargs: Any) -> None:
    """Log a tagged debug error message only if debug mode is enabled.

    Args:
        logger: Logger instance to use
        tag: Debug tag (e.g., "YEAR_DEBUG", "CACHE_DEBUG")
        message: Message to log (with format placeholders)
        *args: Arguments for message formatting
        **kwargs: Additional logger kwargs

    """
    if _debug_state.enabled:
        tagged_message = f"[{tag}] {message}"
        logger.error(tagged_message, *args, **kwargs)
