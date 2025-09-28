"""Mock classes for CSV and file operations in incremental filtering tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.shared.data.models import TrackDict


class MockLoadTrackList:
    """Mock for src.shared.monitoring.reports.load_track_list function."""

    def __init__(self, tracks_to_return: list[TrackDict] | None = None) -> None:
        """Initialize with tracks that should be 'loaded' from CSV."""
        self.tracks_to_return = tracks_to_return or []
        self.load_called = False
        self.csv_path_requested: str | None = None

    def __call__(self, csv_path: str) -> dict[str, TrackDict]:
        """Mock implementation that returns predefined tracks."""
        self.load_called = True
        self.csv_path_requested = csv_path

        # Convert list to dict with track_id as key
        result: dict[str, TrackDict] = {}
        for track in self.tracks_to_return:
            if track_id := str(track.id):
                result[track_id] = track

        return result


class MockGetFullLogPath:
    """Mock for src.shared.core.logger.get_full_log_path function."""

    def __init__(self, path_to_return: str = "/fake/path/track_list.csv") -> None:
        """Initialize with path that should be returned."""
        self.path_to_return = path_to_return
        self.get_called = False
        self.config_requested: dict[str, Any] | None = None
        self.key_requested: str | None = None
        self.default_requested: str | None = None

    def __call__(self, config: dict[str, Any], key: str, default: str) -> str:
        """Mock implementation that returns predefined path."""
        self.get_called = True
        self.config_requested = config
        self.key_requested = key
        self.default_requested = default
        return self.path_to_return


# noinspection PyPep8Naming
class MockLogger:
    """Mock logger for testing with full logging.Logger compatibility."""

    def __init__(self, name: str = "mock") -> None:
        """Initialize mock logger."""
        self.name = name
        self.level = 0
        self.handlers: list[Any] = []
        self.parent = None
        self.propagate = True

        # Message collections for testing
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []
        self.error_messages: list[str] = []
        self.debug_messages: list[str] = []
        self.critical_messages: list[str] = []
        self.exception_messages: list[str] = []

    @staticmethod
    def _format_message(message: str, *args: Any) -> str:
        """Format message with args."""
        if args:
            try:
                return message % args
            except (TypeError, ValueError):
                return message
        return message

    def info(self, message: str, *args: Any, **_kwargs: Any) -> None:
        """Mock info logging."""
        formatted = self._format_message(message, *args)
        self.info_messages.append(formatted)

    def warning(self, message: str, *args: Any, **_kwargs: Any) -> None:
        """Mock warning logging."""
        formatted = self._format_message(message, *args)
        self.warning_messages.append(formatted)

    def error(self, message: str, *args: Any, **_kwargs: Any) -> None:
        """Mock error logging."""
        formatted = self._format_message(message, *args)
        self.error_messages.append(formatted)

    def debug(self, message: str, *args: Any, **_kwargs: Any) -> None:
        """Mock debug logging."""
        formatted = self._format_message(message, *args)
        self.debug_messages.append(formatted)

    def critical(self, message: str, *args: Any, **_kwargs: Any) -> None:
        """Mock critical logging."""
        formatted = self._format_message(message, *args)
        self.critical_messages.append(formatted)

    def exception(self, message: str, *args: Any, **_kwargs: Any) -> None:
        """Mock exception logging."""
        formatted = self._format_message(message, *args)
        self.exception_messages.append(formatted)
        self.error_messages.append(formatted)  # Exception also goes to error

    def log(self, level: int, message: str, *args: Any, **_kwargs: Any) -> None:
        """Mock general log method."""
        # Map numeric levels to method calls
        if level >= 50:  # CRITICAL
            self.critical(message, *args, **_kwargs)
        elif level >= 40:  # ERROR
            self.error(message, *args, **_kwargs)
        elif level >= 30:  # WARNING
            self.warning(message, *args, **_kwargs)
        elif level >= 20:  # INFO
            self.info(message, *args, **_kwargs)
        else:  # DEBUG
            self.debug(message, *args, **_kwargs)

    def set_level(self, level: int) -> None:
        """Mock set_level method."""
        self.level = level

    # Keep compatibility with logging.Logger API
    def setLevel(self, level: int) -> None:  # noqa: N802
        """Mock setLevel method for logging.Logger compatibility."""
        self.set_level(level)

    def add_handler(self, handler: Any) -> None:
        """Mock add_handler method."""
        self.handlers.append(handler)

    # Keep compatibility with logging.Logger API
    def addHandler(self, handler: Any) -> None:  # noqa: N802
        """Mock addHandler method for logging.Logger compatibility."""
        self.add_handler(handler)

    def remove_handler(self, handler: Any) -> None:
        """Mock remove_handler method."""
        if handler in self.handlers:
            self.handlers.remove(handler)

    # Keep compatibility with logging.Logger API
    def removeHandler(self, handler: Any) -> None:  # noqa: N802
        """Mock removeHandler method for logging.Logger compatibility."""
        self.remove_handler(handler)

    def is_enabled_for(self, level: int) -> bool:
        """Mock is_enabled_for method."""
        return level >= self.level

    # Keep compatibility with logging.Logger API
    def isEnabledFor(self, level: int) -> bool:  # noqa: N802
        """Mock isEnabledFor method for logging.Logger compatibility."""
        return self.is_enabled_for(level)

    def get_effective_level(self) -> int:
        """Mock get_effective_level method."""
        return self.level

    # Keep compatibility with logging.Logger API
    def getEffectiveLevel(self) -> int:  # noqa: N802
        """Mock getEffectiveLevel method for logging.Logger compatibility."""
        return self.get_effective_level()


class MockAnalytics:
    """Mock analytics for testing."""

    def __init__(self) -> None:
        """Initialize mock analytics."""
        self.events: list[dict[str, Any]] = []

    def track_event(self, event: dict[str, Any]) -> None:
        """Mock event tracking."""
        self.events.append(event)
