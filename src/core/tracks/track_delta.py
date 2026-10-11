"""Utilities for incremental track synchronization using CSV deltas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from core.models.track_models import TrackDict

__all__ = [
    "FIELD_SEPARATOR",
    "LINE_SEPARATOR",
    "TrackDelta",
    "carry_tool_record",
    "compute_track_delta",
    "find_changed_years",
    "has_identity_changed",
    "has_track_changed",
    "parse_id_year_records",
    "split_applescript_rows",
    "year_text",
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


def year_text(value: str | int | None) -> str:
    """Return the year as the library reports it: "" for an unset year (None, "", "0" or 0)."""
    text = "" if value is None else str(value).strip()
    return "" if text == "0" else text


def parse_id_year_records(raw: str) -> dict[str, str]:
    """Parse `fetch_track_ids.applescript` output (persistent ID, year per record) into id → year text.

    Records are separated by LINE_SEPARATOR, the two fields by FIELD_SEPARATOR; a record without an id is dropped.
    """
    years: dict[str, str] = {}
    for record in raw.split(LINE_SEPARATOR):
        raw_id, _, year = record.partition(FIELD_SEPARATOR)
        if track_id := raw_id.strip():
            years[track_id] = year_text(year)
    return years


@dataclass(slots=True)
class TrackDelta:
    """Delta between CSV snapshot and current Music.app library."""

    new_ids: list[str]
    updated_ids: list[str]
    removed_ids: list[str]
    changed_years: dict[str, str] = field(default_factory=dict)
    """Track id → the year the snapshot held, for the tracks in `updated_ids` whose library year differs from it."""

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
    # A year Apple emptied is a change too, or the clear would never open the incremental gate
    year_changed = year_text(current_year) != year_text(stored_year)

    return bool(track_status_changed or genre_changed or year_changed)


def has_identity_changed(current: TrackDict, stored: TrackDict) -> bool:
    """Check if track identity (artist, album, or album artist) has changed.

    When any of them changes, the API cache for the OLD album
    becomes stale and must be invalidated. This is separate from has_track_changed()
    which only checks fields we manage (genre, year).

    Used for headless daemon mode to automatically detect user edits in Music.app.

    Args:
        current: Current track from Apple Music
        stored: Stored track from snapshot

    Returns:
        True if artist, album, or album artist changed (requires cache invalidation for old values)

    """
    names = (
        (current.artist, stored.artist),
        (current.album, stored.album),
        (current.album_artist, stored.album_artist),
    )
    return any((now or "").strip().lower() != (before or "").strip().lower() for now, before in names)


def find_changed_years(previous_years: Mapping[str, str | None], current_years: Mapping[str, str | None]) -> dict[str, str]:
    """Return id → previous year text for every track in both maps whose year text differs."""
    return {
        track_id: year_text(previous)
        for track_id, previous in previous_years.items()
        if track_id in current_years and year_text(previous) != year_text(current_years[track_id])
    }


def carry_tool_record(current: TrackDict, previous: TrackDict) -> TrackDict:
    """Return `current` with the year record of its previous snapshot entry when a fresh read of Music.app lacks it.

    `year_set_by_mgu` and `year_before_mgu` are the tool's own fields; Music.app never returns them, so a re-fetched track
    would forget that the tool wrote its year.
    """
    if current.year_set_by_mgu or not previous.year_set_by_mgu:
        return current
    return current.copy(year_set_by_mgu=previous.year_set_by_mgu, year_before_mgu=current.year_before_mgu or previous.year_before_mgu)


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
