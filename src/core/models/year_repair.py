"""Generic repair and revert utilities.

Provides helpers to revert year changes using either a changes report
or a backup CSV of the track list. Designed to be artist/album agnostic.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.logger import get_full_log_path

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass
class RevertTarget:
    """Describes a track that should have its year reverted."""

    track_id: str | None
    track_name: str
    album: str | None
    old_year: str


def _changes_row_to_target(
    row: dict[str, str],
    *,
    artist: str,
    album: str | None,
) -> RevertTarget | None:
    """Convert a changes report row into a ``RevertTarget`` if it matches."""
    change_type = (row.get("change_type", "") or "").lower()
    if change_type not in {"year", "year_update"}:
        return None

    row_artist = (row.get("artist", "") or "").strip()
    if row_artist.lower() != artist.lower():
        return None

    row_album = (row.get("album", "") or "").strip()
    if album is not None and row_album != album:
        return None

    track_name = (row.get("track_name", "") or "").strip()
    old_year = (row.get("old_year", "") or "").strip()
    if not track_name or not old_year:
        return None

    return RevertTarget(track_id=None, track_name=track_name, album=row_album or None, old_year=old_year)


def _read_changes_report(
    config: dict[str, Any],
    artist: str,
    album: str | None,
) -> list[RevertTarget]:
    """Build revert targets from the changes_report.csv for an artist/album.

    Matches by track name within optional album scope.
    """
    changes_path = get_full_log_path(config, "changes_report_file", "csv/changes_report.csv")
    path = Path(changes_path)
    if not path.exists():
        return []

    targets: list[RevertTarget] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            target = _changes_row_to_target(row, artist=artist, album=album)
            if target is not None:
                targets.append(target)
    return targets


def _choose_backup_year(row: dict[str, str]) -> str:
    """Choose year value from a backup CSV row.

    Priority: 'year' column if present -> 'old_year' -> 'new_year'.
    """
    for key in ("year", "old_year", "new_year"):
        v = (row.get(key, "") or "").strip()
        if v.isdigit():
            return v
    return ""


def _read_backup_csv(
    backup_csv_path: str,
    artist: str,
    album: str | None,
) -> list[RevertTarget]:
    """Build revert targets from a backup track_list CSV.

    Matches by track ID primarily, with optional album filter.
    """
    path = Path(backup_csv_path)
    if not path.exists():
        return []

    targets: list[RevertTarget] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_artist = (row.get("artist", "") or "").strip()
            if row_artist.lower() != artist.lower():
                continue
            row_album = (row.get("album", "") or "").strip()
            if album is not None and row_album != album:
                continue
            if year_val := _choose_backup_year(row):
                targets.append(
                    RevertTarget(
                        track_id=(row.get("id", "") or "").strip() or None,
                        track_name=(row.get("name", "") or "").strip(),
                        album=row_album or None,
                        old_year=year_val,
                    )
                )
    return targets


def _normalize_text(value: Any) -> str:
    """Normalize raw track metadata values to stripped strings."""

    return str(value or "").strip()


def _build_track_lookups(
    current_tracks: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], dict[tuple[str, str], Any], dict[str, Any]]:
    """Create lookup dictionaries for current tracks."""

    by_id: dict[str, Any] = {}
    by_album_track: dict[tuple[str, str], Any] = {}
    by_name: dict[str, Any] = {}

    for track in current_tracks:
        if track_id := _normalize_text(track.get("id")):
            by_id[track_id] = track

        album_name = _normalize_text(track.get("album"))
        track_name = _normalize_text(track.get("name"))
        if album_name and track_name:
            by_album_track[(album_name, track_name)] = track
        if track_name and track_name.lower() not in by_name:
            by_name[track_name.lower()] = track

    return by_id, by_album_track, by_name


def _find_track_for_target(
    target: RevertTarget,
    *,
    by_id: dict[str, Any],
    by_album_track: dict[tuple[str, str], Any],
    by_name: dict[str, Any],
) -> Any | None:
    """Locate the best matching track for a revert target."""

    if target.track_id and target.track_id in by_id:
        return by_id[target.track_id]

    if target.album:
        # Normalize both album and track name to match dict keys created in _build_track_lookups
        normalized_album = _normalize_text(target.album)
        normalized_track = _normalize_text(target.track_name)
        track_key = (normalized_album, normalized_track)
        if track_key in by_album_track:
            return by_album_track[track_key]

    return by_name.get(target.track_name.lower())


def _build_year_change_entry(track: dict[str, Any], reverted_year: str) -> dict[str, str]:
    """Create a change log entry after a successful revert."""

    return {
        "timestamp": "",
        "change_type": "year_update",
        "artist": _normalize_text(track.get("artist")),
        "album": _normalize_text(track.get("album")),
        "album_name": _normalize_text(track.get("album")),
        "track_name": _normalize_text(track.get("name")),
        "old_year": _normalize_text(track.get("year")),
        "new_year": reverted_year,
    }


async def _revert_track_year(
    track_processor: Any,
    *,
    track: dict[str, Any],
    track_id: str,
    reverted_year: str,
) -> bool:
    """Call into ``track_processor`` to perform the year revert."""

    result = await track_processor.update_track_async(
        track_id=track_id,
        new_year=reverted_year,
        track_status=_normalize_text(track.get("track_status")),
        original_artist=_normalize_text(track.get("artist")),
        original_album=_normalize_text(track.get("album")),
        original_track=_normalize_text(track.get("name")),
    )
    return bool(result)


def build_revert_targets(
    *,
    config: dict[str, Any],
    artist: str,
    album: str | None,
    backup_csv_path: str | None = None,
) -> list[RevertTarget]:
    """Build revert targets from either a backup CSV or the changes report.

    If backup_csv_path is provided and readable, it is used; otherwise
    the changes_report.csv is used.
    """
    if backup_csv_path and (targets := _read_backup_csv(backup_csv_path, artist, album)):
        return targets
    return _read_changes_report(config, artist, album)


async def apply_year_reverts(
    *,
    track_processor: Any,
    artist: str,
    targets: Iterable[RevertTarget],
) -> tuple[int, int, list[dict[str, str]]]:
    """Apply year reverts to Music.app given revert targets.

    Returns (updated_count, missing_count, change_log_entries).
    """
    current_tracks = await track_processor.fetch_tracks_async(artist=artist, ignore_test_filter=True)
    by_id, by_album_track, by_name = _build_track_lookups(current_tracks)

    updated = 0
    missing = 0
    change_log: list[dict[str, str]] = []

    for target in targets:
        track = _find_track_for_target(
            target,
            by_id=by_id,
            by_album_track=by_album_track,
            by_name=by_name,
        )
        if track is None:
            missing += 1
            continue

        track_id = _normalize_text(track.get("id"))
        if not track_id:
            missing += 1
            continue

        if not await _revert_track_year(track_processor, track=track, track_id=track_id, reverted_year=target.old_year):
            continue

        updated += 1
        change_log.append(_build_year_change_entry(track, target.old_year))

    return updated, missing, change_log
