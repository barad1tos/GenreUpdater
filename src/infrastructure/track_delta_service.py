"""Utilities for incremental track synchronization using CSV deltas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.shared.data.models import TrackDict

FIELD_SEPARATOR = "\x1e"  # ASCII 30
LINE_SEPARATOR = "\x1d"  # ASCII 29
EXPECTED_FIELD_COUNT = 4  # track_id, date_added, last_modified, track_status


@dataclass(slots=True)
class TrackSummary:
    """Lightweight track metadata used for incremental comparisons."""

    track_id: str
    date_added: str
    last_modified: str
    track_status: str


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


def parse_track_summaries(raw_output: str) -> list[TrackSummary]:
    """Parse the raw AppleScript output containing track summaries."""

    if not raw_output:
        return []

    summaries: list[TrackSummary] = []
    for line in raw_output.split(LINE_SEPARATOR):
        if not line:
            continue
        parts = line.split(FIELD_SEPARATOR)
        # Normalise length
        while len(parts) < EXPECTED_FIELD_COUNT:
            parts.append("")
        if track_id := parts[0].strip():
            summaries.append(
                TrackSummary(
                    track_id=track_id,
                    date_added=parts[1].strip(),
                    last_modified=parts[2].strip(),
                    track_status=parts[3].strip(),
                )
            )
    return summaries


def compute_track_delta(
    summaries: Iterable[TrackSummary],
    existing_map: dict[str, TrackDict],
) -> TrackDelta:
    """Compute track delta given current summaries and CSV snapshot."""

    summary_map: dict[str, TrackSummary] = {summary.track_id: summary for summary in summaries}
    summary_ids = set(summary_map.keys())
    existing_ids = set(existing_map.keys())

    new_ids = sorted(summary_ids - existing_ids)
    removed_ids = sorted(existing_ids - summary_ids)

    updated_ids: list[str] = []
    for track_id in sorted(summary_ids & existing_ids):
        summary = summary_map[track_id]
        stored = existing_map[track_id]
        stored_last_modified = getattr(stored, "last_modified", "") or ""
        summary_last_modified = summary.last_modified or ""

        stored_date_added = stored.date_added or ""
        summary_date_added = summary.date_added or ""

        stored_track_status = getattr(stored, "track_status", "")
        summary_track_status = summary.track_status or ""

        # Only consider track_status change if both stored and summary have meaningful values
        # This prevents mass updates on first run after adding track_status field
        track_status_changed = (
            stored_track_status and summary_track_status and
            summary_track_status != stored_track_status
        )

        if (summary_last_modified and summary_last_modified != stored_last_modified) or (
            summary_date_added and summary_date_added != stored_date_added
        ) or track_status_changed:
            updated_ids.append(track_id)

    return TrackDelta(new_ids=new_ids, updated_ids=updated_ids, removed_ids=removed_ids)


def apply_track_delta_to_map(
    track_map: dict[str, TrackDict],
    updated_tracks: Iterable[TrackDict],
    summary_lookup: dict[str, TrackSummary],
    removed_ids: Iterable[str],
) -> None:
    """Apply delta changes to an in-memory track map."""

    for track_id in removed_ids:
        track_map.pop(track_id, None)

    for track in updated_tracks:
        track_id = track.id
        if not track_id:
            continue
        summary = summary_lookup.get(track_id)
        if summary is not None:
            track.last_modified = summary.last_modified or None
            if summary.date_added:
                track.date_added = summary.date_added
            if summary.track_status:
                track.track_status = summary.track_status
        track_map[track_id] = track
