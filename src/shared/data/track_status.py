"""Utilities for classifying track statuses and editability."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.shared.data.models import TrackDict


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


def normalize_track_status(status: str | None) -> str:
    """Normalize track status strings for consistent comparisons."""
    return status.strip().lower() if isinstance(status, str) else ""


def is_prerelease_status(status: str | None) -> bool:
    """Check if the status marks the track as read-only prerelease."""
    return normalize_track_status(status) in READ_ONLY_STATUSES


def is_subscription_status(status: str | None) -> bool:
    """Check if the status indicates a subscription track."""
    return normalize_track_status(status) in SUBSCRIPTION_STATUSES


def is_available_status(status: str | None) -> bool:
    """Determine if the track status allows standard processing."""
    normalized = normalize_track_status(status)
    return normalized in AVAILABLE_STATUSES


def can_edit_metadata(status: str | None) -> bool:
    """Return True when the track metadata can be edited."""
    return not is_prerelease_status(status)


def filter_available_tracks(tracks: Iterable[TrackDict]) -> list[TrackDict]:
    """Filter tracks that are available for processing based on status."""
    return [track for track in tracks if is_available_status(track.track_status if isinstance(track.track_status, str) else None)]
