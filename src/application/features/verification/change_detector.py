"""Change Detection Module for Smart Update System.

This module implements intelligent change detection to avoid redundant updates
and respect manually set values. Use Strategy pattern for different change types.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class ChangeType(Enum):
    """Types of changes that can be detected."""

    NO_CHANGE = "no_change"
    VALUE_CHANGE = "value_change"
    MANUAL_OVERRIDE = "manual_override"
    FORCED_UPDATE = "forced_update"
    INITIAL_SET = "initial_set"


@dataclass
class ChangeResult:
    """Result of change detection."""

    should_update: bool
    change_type: ChangeType
    old_value: Any
    new_value: Any
    reason: str
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Ensure metadata is initialized."""
        if self.metadata is None:
            self.metadata = {}
        # Add creation timestamp
        self.metadata["created_at"] = datetime.now(UTC).isoformat()


class ChangeStrategy(ABC):
    """Abstract base class for change detection strategies."""

    @abstractmethod
    def detect_change(self, old_value: Any, new_value: Any, context: dict[str, Any] | None = None) -> ChangeResult:
        """Detect if a change should be applied."""

    @abstractmethod
    def normalize_value(self, value: Any) -> Any:
        """Normalize value for comparison."""


class GenreChangeStrategy(ChangeStrategy):
    """Strategy for detecting genre changes."""

    def normalize_value(self, value: Any) -> str:
        """Normalize genre string for comparison."""
        if value is None:
            return ""

        # Convert to string and strip whitespace
        normalized = str(value).strip()

        # Normalize separators (both / and, are used)
        normalized = normalized.replace(" / ", "/").replace(", ", "/")

        # Sort genre components for consistent comparison
        components = [c.strip() for c in normalized.split("/") if c.strip()]
        components.sort()

        return "/".join(components).lower()

    def detect_change(self, old_value: Any, new_value: Any, context: dict[str, Any] | None = None) -> ChangeResult:
        """Detect if the genre should be updated."""
        context = context or {}
        force_mode = context.get("force_mode", False)
        is_manual = context.get("is_manual", False)

        # Normalize values for comparison
        old_normalized = self.normalize_value(old_value)
        new_normalized = self.normalize_value(new_value)

        # Check if values are the same
        if old_normalized == new_normalized and not force_mode:
            return ChangeResult(
                should_update=False,
                change_type=ChangeType.NO_CHANGE,
                old_value=old_value,
                new_value=new_value,
                reason="Genre values are identical",
            )

        # Handle empty old value (initial set)
        if not old_normalized:
            return ChangeResult(
                should_update=True,
                change_type=ChangeType.INITIAL_SET,
                old_value=old_value,
                new_value=new_value,
                reason="Setting initial genre value",
            )

        # Handle manual override protection
        if is_manual and not force_mode:
            return ChangeResult(
                should_update=False,
                change_type=ChangeType.MANUAL_OVERRIDE,
                old_value=old_value,
                new_value=new_value,
                reason="Preserving manually set genre",
                metadata={"manual_value": old_value},
            )

        # Force mode - always update
        if force_mode:
            return ChangeResult(
                should_update=True,
                change_type=ChangeType.FORCED_UPDATE,
                old_value=old_value,
                new_value=new_value,
                reason="Force mode - updating regardless of current value",
            )

        # Normal update - values are different
        return ChangeResult(
            should_update=True,
            change_type=ChangeType.VALUE_CHANGE,
            old_value=old_value,
            new_value=new_value,
            reason=f"Genre change detected: '{old_value}' → '{new_value}'",
        )


class YearChangeStrategy(ChangeStrategy):
    """Strategy for detecting year changes."""

    def normalize_value(self, value: Any) -> int | None:
        """Normalize year value for comparison."""
        if value is None or value == "":
            return None

        try:
            # Handle string years - extract year from strings like "2023" or "Released: 2023"
            if isinstance(value, str) and (match := re.search(r"\b(19\d{2}|20\d{2})\b", value)):
                return int(match[1])

            # Direct integer conversion
            return int(value)
        except (ValueError, TypeError):
            logger.warning("Could not normalize year value: %s", value)
            return None

    def detect_change(self, old_value: Any, new_value: Any, context: dict[str, Any] | None = None) -> ChangeResult:
        """Detect if the year should be updated."""
        context = context or {}
        force_mode = context.get("force_mode", False)

        # Normalize values
        old_normalized = self.normalize_value(old_value)
        new_normalized = self.normalize_value(new_value)

        # Both None - no change
        if old_normalized is None and new_normalized is None:
            return ChangeResult(
                should_update=False,
                change_type=ChangeType.NO_CHANGE,
                old_value=old_value,
                new_value=new_value,
                reason="No year value to set",
            )

        # Values are the same
        if old_normalized == new_normalized and not force_mode:
            return ChangeResult(
                should_update=False,
                change_type=ChangeType.NO_CHANGE,
                old_value=old_value,
                new_value=new_value,
                reason="Year values are identical",
            )

        # Initial set
        if old_normalized is None and new_normalized is not None:
            return ChangeResult(
                should_update=True,
                change_type=ChangeType.INITIAL_SET,
                old_value=old_value,
                new_value=new_value,
                reason="Setting initial year value",
            )

        # Force mode
        if force_mode and new_normalized is not None:
            return ChangeResult(
                should_update=True,
                change_type=ChangeType.FORCED_UPDATE,
                old_value=old_value,
                new_value=new_value,
                reason="Force mode - updating year",
            )

        # Normal update
        if old_normalized != new_normalized and new_normalized is not None:
            return ChangeResult(
                should_update=True,
                change_type=ChangeType.VALUE_CHANGE,
                old_value=old_value,
                new_value=new_value,
                reason=f"Year change detected: {old_value} → {new_value}",
            )

        return ChangeResult(
            should_update=False,
            change_type=ChangeType.NO_CHANGE,
            old_value=old_value,
            new_value=new_value,
            reason="No year update needed",
        )


# noinspection PyUnboundLocalVariable
class ChangeDetector:
    """Main change detection coordinator."""

    def __init__(self) -> None:
        """Initialize the change detector with strategies."""
        self.strategies: dict[str, ChangeStrategy] = {
            "genre": GenreChangeStrategy(),
            "year": YearChangeStrategy(),
        }
        self.change_history: list[ChangeResult] = []
        self.stats = {
            "total_checks": 0,
            "updates_needed": 0,
            "updates_skipped": 0,
            "manual_preserved": 0,
            "forced_updates": 0,
            "initial_sets": 0,
        }

    def detect_change(
        self,
        field_type: str,
        old_value: Any,
        new_value: Any,
        context: dict[str, Any] | None = None,
    ) -> ChangeResult:
        """Detect if a field should be updated."""
        match field_type:
            case "genre" | "year":
                strategy = self.strategies[field_type]
            case _:
                msg = f"Unknown field type: {field_type}"
                raise ValueError(msg)

        result = strategy.detect_change(old_value, new_value, context)

        # Update statistics
        self.stats["total_checks"] += 1
        if result.should_update:
            self.stats["updates_needed"] += 1
            match result.change_type:
                case ChangeType.FORCED_UPDATE:
                    self.stats["forced_updates"] += 1
                case ChangeType.INITIAL_SET:
                    self.stats["initial_sets"] += 1
        else:
            self.stats["updates_skipped"] += 1
            if result.change_type == ChangeType.MANUAL_OVERRIDE:
                self.stats["manual_preserved"] += 1

        # Add timestamp to metadata
        #  is guaranteed to be non-None after __post_init__
        if result.metadata is not None:
            result.metadata["timestamp"] = datetime.now(UTC).isoformat()

        # Store in history
        self.change_history.append(result)

        # Log the decision
        match result.should_update:
            case True:
                logger.info("Change detection for %s: %s", field_type, result.reason)
            case False:
                logger.debug("Change detection for %s: %s", field_type, result.reason)

        return result

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of change detection statistics."""
        return {
            "statistics": self.stats.copy(),
            "change_types": self._count_change_types(),
            "efficiency": self._calculate_efficiency(),
        }

    def _count_change_types(self) -> dict[str, int]:
        """Count occurrences of each change type."""
        counts: dict[str, int] = {}
        for result in self.change_history:
            change_type = result.change_type.value
            counts[change_type] = counts.get(change_type, 0) + 1
        return counts

    def _calculate_efficiency(self) -> dict[str, Any]:
        """Calculate efficiency metrics."""
        total = self.stats["total_checks"]
        if total == 0:
            return {"skip_rate": 0, "update_rate": 0}

        return {
            "skip_rate": round(self.stats["updates_skipped"] / total * 100, 2),
            "update_rate": round(self.stats["updates_needed"] / total * 100, 2),
            "manual_preservation_rate": round(self.stats["manual_preserved"] / total * 100, 2),
        }

    def reset_stats(self) -> None:
        """Reset statistics and history."""
        self.stats = {
            "total_checks": 0,
            "updates_needed": 0,
            "updates_skipped": 0,
            "manual_preserved": 0,
            "forced_updates": 0,
            "initial_sets": 0,
        }
        self.change_history.clear()


def _create_detector_getter() -> Callable[[], ChangeDetector]:
    """Create a change detector getter function with a closure-based singleton."""
    instance: ChangeDetector | None = None

    def _get_detector() -> ChangeDetector:
        """Get or create the singleton ChangeDetector instance."""
        nonlocal instance
        if instance is None:
            instance = ChangeDetector()
        return instance

    return _get_detector


# Create the actual function that external code will use
get_change_detector = _create_detector_getter()
