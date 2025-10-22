"""Utilities for classifying track statuses and editability."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.shared.data.models import TrackDict

logger = logging.getLogger(__name__)


READ_ONLY_STATUSES: frozenset[str] = frozenset({"prerelease"})
SUBSCRIPTION_STATUSES: frozenset[str] = frozenset({"subscription"})
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


def normalize_track_status(status: object) -> str:
    """Normalize track status strings for consistent comparisons.

    Performs runtime type validation to ensure defensive programming,
    even if incorrect types are passed at runtime.

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
    return status.strip().lower()


def is_prerelease_status(status: object) -> bool:
    """Check if the status marks the track as read-only prerelease."""
    return normalize_track_status(status) in READ_ONLY_STATUSES


def is_subscription_status(status: object) -> bool:
    """Check if the status indicates a subscription track."""
    return normalize_track_status(status) in SUBSCRIPTION_STATUSES


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
