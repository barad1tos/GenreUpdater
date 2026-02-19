"""Utilities for incremental track synchronization using CSV deltas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from core.models.track_models import TrackDict

__all__ = [
    "FIELD_SEPARATOR",
    "LINE_SEPARATOR",
    "TrackDelta",
    "compute_track_delta",
    "has_identity_changed",
    "has_track_changed",
    "split_applescript_rows",
]

# AppleScript output delimiters (ASCII Record/Unit Separators)
FIELD_SEPARATOR = "\x1e"  # ASCII 30 - separates fields within a record
LINE_SEPARATOR = "\x1d"  # ASCII 29 - separates records (tracks)


def split_applescript_rows(raw: str, field_separator: str) -> list[str]:
    """Split raw AppleScript output into individual track rows.

    Python's ``str.splitlines()`` treats ``\\x1e`` (our field separator) as a
    line boundary, breaking single-track responses into per-field rows.  This
    helper centralises the split-vs-splitlines decision so both parsers
    (``parse_tracks`` and ``parse_osascript_output``) stay in sync.

    Args:
        raw: Stripped raw AppleScript output.
        field_separator: Detected field separator (FIELD_SEPARATOR or ``\\t``).

    Returns:
        List of raw track row strings.

    """
    if field_separator == FIELD_SEPARATOR:
        return raw.split(LINE_SEPARATOR)
    return raw.splitlines()


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


def has_track_changed(current: TrackDict, stored: TrackDict) -> bool:
    """Check if track metadata has changed between current and stored versions.

    Only checks fields relevant to genre/year processing. Excludes last_modified
    and date_added because they change for many reasons unrelated to our updates
    (e.g., playback, ratings, other metadata edits) and cause false positives.

    Args:
        current: Current track from Apple Music
        stored: Stored track from CSV snapshot

    Returns:
        True if any relevant field has changed (track_status, genre, year)

    """
    stored_track_status = stored.track_status or ""
    current_track_status = current.track_status or ""

    stored_genre = stored.genre or ""
    current_genre = current.genre or ""

    stored_year = str(stored.year or "")
    current_year = str(current.year or "")

    # Only consider track_status change if both stored and current have meaningful values
    # This prevents mass updates on first run after adding track_status field
    track_status_changed = stored_track_status and current_track_status and current_track_status != stored_track_status

    # Check for changes in metadata fields relevant to genre/year processing
    genre_changed = _field_changed(current_genre, stored_genre)
    year_changed = _field_changed(current_year, stored_year)

    return bool(track_status_changed or genre_changed or year_changed)


def has_identity_changed(current: TrackDict, stored: TrackDict) -> bool:
    """Check if track identity (artist or album) has changed.

    When artist or album changes, the API cache for the OLD artist/album
    becomes stale and must be invalidated. This is separate from has_track_changed()
    which only checks fields we manage (genre, year).

    Used for headless daemon mode to automatically detect user edits in Music.app.

    Args:
        current: Current track from Apple Music
        stored: Stored track from snapshot

    Returns:
        True if artist or album changed (requires cache invalidation for old values)

    """
    current_artist = (current.artist or "").strip().lower()
    stored_artist = (stored.artist or "").strip().lower()

    current_album = (current.album or "").strip().lower()
    stored_album = (stored.album or "").strip().lower()

    return current_artist != stored_artist or current_album != stored_album


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

        if has_track_changed(current, stored):
            updated_ids.append(track_id)

    return TrackDelta(new_ids=new_ids, updated_ids=updated_ids, removed_ids=removed_ids)
