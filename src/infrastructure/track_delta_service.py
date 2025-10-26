"""Utilities for incremental track synchronization using CSV deltas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.shared.data.models import TrackDict

__all__ = [
    "TrackDelta",
    "compute_track_delta",
]

FIELD_SEPARATOR = "\x1e"  # ASCII 30
LINE_SEPARATOR = "\x1d"  # ASCII 29
EXPECTED_FIELD_COUNT = 4  # track_id, date_added, last_modified, track_status


@dataclass(slots=True)
class TrackDelta:
    """Delta between CSV snapshot and current Music.app library."""

    new_ids: list[str]
    updated_ids: list[str]
    removed_ids: list[str]

    def has_updates(self) -> bool:
        """Return True when new or modified tracks are present."""

        return bool(self.new_ids or self.updated_ids)

    def has_removals(self) -> bool:
        """Return True when tracks were removed from Music.app."""

        return bool(self.removed_ids)

    def is_empty(self) -> bool:
        """Return True when no changes were detected."""

        return not (self.new_ids or self.updated_ids or self.removed_ids)


def _field_changed(current: str, stored: str) -> bool:
    """Check if a field value has changed.

    Args:
        current: Current field value
        stored: Stored field value

    Returns:
        True if field has a meaningful value and it differs from stored

    """
    return bool(current and current != stored)


def _has_track_changed(current: TrackDict, stored: TrackDict) -> bool:
    """Check if track metadata has changed between current and stored versions.

    Args:
        current: Current track from Apple Music
        stored: Stored track from CSV snapshot

    Returns:
        True if any tracked field has changed

    """
    # Compare the same fields that TrackSummary would compare
    stored_last_modified = getattr(stored, "last_modified", "") or ""
    current_last_modified = current.last_modified or ""

    stored_date_added = stored.date_added or ""
    current_date_added = current.date_added or ""

    stored_track_status = stored.track_status or ""
    current_track_status = current.track_status or ""

    stored_genre = stored.genre or ""
    current_genre = current.genre or ""

    stored_year = str(stored.year or "")
    current_year = str(current.year or "")

    # Only consider track_status change if both stored and current have meaningful values
    # This prevents mass updates on first run after adding track_status field
    track_status_changed = stored_track_status and current_track_status and current_track_status != stored_track_status

    # Check for changes in metadata fields
    last_modified_changed = _field_changed(current_last_modified, stored_last_modified)
    date_added_changed = _field_changed(current_date_added, stored_date_added)
    genre_changed = _field_changed(current_genre, stored_genre)
    year_changed = _field_changed(current_year, stored_year)

    return bool(last_modified_changed or date_added_changed or track_status_changed or genre_changed or year_changed)


def compute_track_delta(
    current_tracks: Iterable[TrackDict],
    existing_map: dict[str, TrackDict],
) -> TrackDelta:
    """Compute track delta given current TrackDict objects and CSV snapshot."""
    current_map: dict[str, TrackDict] = {str(track.id): track for track in current_tracks}
    current_ids = set(current_map.keys())
    existing_ids = set(existing_map.keys())

    new_ids = sorted(current_ids - existing_ids)
    removed_ids = sorted(existing_ids - current_ids)

    updated_ids: list[str] = []
    for track_id in sorted(current_ids & existing_ids):
        current = current_map[track_id]
        stored = existing_map[track_id]

        if _has_track_changed(current, stored):
            updated_ids.append(track_id)

    return TrackDelta(new_ids=new_ids, updated_ids=updated_ids, removed_ids=removed_ids)
