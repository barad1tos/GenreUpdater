"""Utilities for classifying track statuses and editability."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from core.models.track_models import TrackDict

logger = logging.getLogger(__name__)


READ_ONLY_STATUSES: frozenset[str] = frozenset({"prerelease"})
AVAILABLE_STATUSES: frozenset[str] = frozenset(
    {
        "local only",
        "purchased",
        "matched",
        "uploaded",
        "subscription",
        "downloaded",
    }
)

# AppleScript sometimes returns raw enum constants instead of string values
# Map these to their proper status strings
APPLESCRIPT_CONSTANT_MAP: dict[str, str] = {
    "ksub": "subscription",
    "kpre": "prerelease",
    "kloc": "local only",
    "kpur": "purchased",
    "kmat": "matched",
    "kupl": "uploaded",
    "kdwn": "downloaded",
}


def normalize_track_status(status: object) -> str:
    """Normalize track status strings for consistent comparisons.

    Performs runtime type validation to ensure defensive programming,
    even if incorrect types are passed at runtime.

    Handles AppleScript raw enum constants like «constant ****kSub» by
    extracting the 4-character code and mapping to standard status strings.

    Args:
        status: Track status (expected str or None, but validated at runtime)

    Returns:
        Normalized lowercase status string, or empty string for None

    Raises:
        TypeError: If status is not a string or None

    """
    if status is None:
        return ""
    if not isinstance(status, str):
        type_name = type(status).__name__
        msg = f"Expected status to be str or None, got {type_name}"
        logger.warning("normalize_track_status: %s", msg)
        raise TypeError(msg)

    normalized = status.strip().lower()

    # Handle AppleScript raw enum constants like «constant ****kSub»
    if "«constant" in normalized or "constant" in normalized:
        if normalized_status := _extract_status_from_applescript_constant(normalized):
            return normalized_status
        # If we can't parse it, log warning but continue
        logger.warning("Could not parse AppleScript constant: %s", status)

    return normalized


def _extract_status_from_applescript_constant(raw_constant: str) -> str | None:
    """Extract status from AppleScript raw enum constant.

    AppleScript sometimes returns raw constants like «constant ****kSub»
    instead of the string "subscription". This extracts the 4-char code
    and maps it to the proper status string.

    Args:
        raw_constant: Raw AppleScript constant string (already lowercased)

    Returns:
        Mapped status string, or None if not recognized

    """
    return next(
        (status for code, status in APPLESCRIPT_CONSTANT_MAP.items() if code in raw_constant),
        None,
    )


def is_prerelease_status(status: object) -> bool:
    """Check if the status marks the track as read-only prerelease."""
    return normalize_track_status(status) in READ_ONLY_STATUSES


def is_available_status(status: object) -> bool:
    """Determine if the track status allows standard processing."""
    normalized = normalize_track_status(status)
    return normalized in AVAILABLE_STATUSES


def can_edit_metadata(status: object) -> bool:
    """Return True when the track metadata can be edited."""
    return not is_prerelease_status(status)


def filter_available_tracks(tracks: Iterable[TrackDict]) -> list[TrackDict]:
    """Filter tracks that are available for processing based on status."""
    return [track for track in tracks if is_available_status(track.track_status)]
