"""Type definitions for testing."""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LoggerLike(Protocol):
    """Protocol for logger-like objects."""

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an informational message."""

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a warning message."""

    def error(self, message: str, *args: Any, exc_info: Any = None, **kwargs: Any) -> None:
        """Log an error message, optionally attaching exception info."""

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a debug message."""

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a critical message."""

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an exception message, capturing the current traceback."""

    def log(self, level: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a message at the provided logging level."""

    def setLevel(self, level: int) -> None:  # noqa: N802 - matches logging.Logger API
        """Set the logging threshold for this logger."""

    def addHandler(self, handler: Any) -> None:  # noqa: N802 - matches logging.Logger API
        """Attach a handler to the logger."""

    def removeHandler(self, handler: Any) -> None:  # noqa: N802 - matches logging.Logger API
        """Detach a handler from the logger."""

    def isEnabledFor(self, level: int) -> bool:  # noqa: N802 - matches logging.Logger API
        """Return True if the logger handles records at the given level."""

    def getEffectiveLevel(self) -> int:  # noqa: N802 - matches logging.Logger API
        """Return the effective logging level for the logger."""


# Type alias for logger-like objects
TestLogger = LoggerLike | logging.Logger
