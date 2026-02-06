"""Base class for music processing modules.

Provides common functionality and structure for all music processing modules
including genre management, year retrieval, and track processing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging
    from metrics import Analytics


class BaseProcessor:
    """Base class for music processing modules.

    Provides common initialization and dry-run functionality
    that is shared across all processing modules.
    """

    def __init__(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics: Analytics,
        config: dict[str, Any],
        dry_run: bool,
    ) -> None:
        """Initialize the base processor.

        Args:
            console_logger: Logger for console output
            error_logger: Logger for error messages
            analytics: Analytics instance for tracking
            config: Configuration dictionary
            dry_run: Whether to run in dry-run mode

        """
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.analytics = analytics
        self.config = config
        self.dry_run = dry_run
        self._dry_run_actions: list[dict[str, Any]] = []

    def get_dry_run_actions(self) -> list[dict[str, Any]]:
        """Get the list of dry-run actions performed.

        Returns:
            List of dry-run actions with details

        """
        return self._dry_run_actions.copy()

    def clear_dry_run_actions(self) -> None:
        """Clear the list of recorded dry-run actions."""
        self._dry_run_actions.clear()

    def _record_dry_run_action(self, action_type: str, details: dict[str, Any]) -> None:
        """Record a dry-run action for later reporting.

        Args:
            action_type: Type of action performed
            details: Details about the action

        """
        if self.dry_run:
            self._dry_run_actions.append(
                {
                    "type": action_type,
                    "details": details,
                }
            )
