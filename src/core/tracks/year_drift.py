"""Years that changed outside the tool: finding them and deciding what happens to them."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, NamedTuple

from core.tracks.track_delta import year_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from core.models.track_models import TrackDict


class OutsideYearChange(NamedTuple):
    """A track whose year changed since the last snapshot, other than by the tool's own write."""

    track: TrackDict
    old_year: str
    new_year: str


def find_outside_changes(album_tracks: Sequence[TrackDict], previous_years: Mapping[str, str] | None) -> list[OutsideYearChange]:
    """Return the album's tracks whose year differs from the one the last snapshot held, except the tool's own writes.

    Args:
        album_tracks: The album's tracks as read from Music.app
        previous_years: Track id → the year the last snapshot held, for tracks whose year differs; None or empty when there is no baseline

    Returns:
        One entry per changed track, in album order
    """
    if not previous_years:
        return []
    changes: list[OutsideYearChange] = []
    for track in album_tracks:
        previous = previous_years.get(str(track.id))
        if previous is None:
            continue
        old_year, new_year = year_text(previous), year_text(track.year)
        if old_year == new_year or (track.year_set_by_mgu and new_year == year_text(track.year_set_by_mgu)):
            continue
        changes.append(OutsideYearChange(track, old_year, new_year))
    return changes


def summarize_outside_changes(changes: Sequence[OutsideYearChange]) -> tuple[str, str]:
    """Return the most common old year and the most common new year among the changes (a tie goes to the first seen)."""
    old_year = Counter(change.old_year for change in changes).most_common(1)[0][0]
    new_year = Counter(change.new_year for change in changes).most_common(1)[0][0]
    return old_year, new_year
