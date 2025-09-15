"""Generic repair and revert utilities.

Provides helpers to revert year changes using either a changes report
or a backup CSV of the track list. Designed to be artist/album agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from src.utils.core.logger import get_full_log_path


@dataclass
class RevertTarget:
    track_id: str | None
    track_name: str
    album: str | None
    old_year: str


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
            if ((row.get("change_type", "") or "").lower() not in {"year", "year_update"}):
                continue
            row_artist = (row.get("artist", "") or "").strip()
            if row_artist.lower() != artist.lower():
                continue
            row_album = (row.get("album", "") or "").strip()
            if album is not None and row_album != album:
                continue
            track_name = (row.get("track_name", "") or "").strip()
            old_year = (row.get("old_year", "") or "").strip()
            if not track_name or not old_year:
                continue
            targets.append(
                RevertTarget(track_id=None, track_name=track_name, album=row_album or None, old_year=old_year)
            )
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
            year_val = _choose_backup_year(row)
            if not year_val:
                continue
            targets.append(
                RevertTarget(
                    track_id=(row.get("id", "") or "").strip() or None,
                    track_name=(row.get("name", "") or "").strip(),
                    album=row_album or None,
                    old_year=year_val,
                )
            )
    return targets


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
    if backup_csv_path:
        targets = _read_backup_csv(backup_csv_path, artist, album)
        if targets:
            return targets
    return _read_changes_report(config, artist, album)


async def apply_year_reverts(
    *,
    track_processor: Any,
    console_logger: Any,
    error_logger: Any,
    artist: str,
    album: str | None,
    targets: Iterable[RevertTarget],
) -> tuple[int, int, list[dict[str, str]]]:
    """Apply year reverts to Music.app given revert targets.

    Returns (updated_count, missing_count, change_log_entries).
    """
    # Fetch current tracks for artist
    current_tracks = await track_processor.fetch_tracks_async(artist=artist, ignore_test_filter=True)

    # Build lookup: by id and by (album, name)
    by_id = {str(t.get("id") or ""): t for t in current_tracks if t.get("id")}
    by_album_name: dict[tuple[str, str], Any] = {}
    for t in current_tracks:
        alb = (t.get("album") or "").strip()
        nm = (t.get("name") or "").strip()
        if not alb or not nm:
            continue
        by_album_name[(alb, nm)] = t

    updated = 0
    missing = 0
    change_log: list[dict[str, str]] = []

    for tgt in targets:
        track = None
        if tgt.track_id and tgt.track_id in by_id:
            track = by_id[tgt.track_id]
        elif tgt.album and (tgt.album, tgt.track_name) in by_album_name:
            track = by_album_name[(tgt.album, tgt.track_name)]
        else:
            # fallback: search only by name if album not provided
            if tgt.album is None:
                for (alb, nm), t in by_album_name.items():
                    if nm.lower() == tgt.track_name.lower():
                        track = t
                        break

        if not track:
            missing += 1
            continue

        track_id = str(track.get("id") or "")
        if not track_id:
            missing += 1
            continue

        success = await track_processor.update_track_async(
            track_id=track_id,
            new_year=tgt.old_year,
            track_status=(track.get("track_status") or ""),
            original_artist=str(track.get("artist") or ""),
            original_album=str(track.get("album") or ""),
            original_track=str(track.get("name") or ""),
        )
        if success:
            updated += 1
            change_log.append(
                {
                    "timestamp": "",
                    "change_type": "year_update",
                    "artist": str(track.get("artist") or ""),
                    "album": str(track.get("album") or ""),
                    "album_name": str(track.get("album") or ""),
                    "track_name": str(track.get("name") or ""),
                    "old_year": str(track.get("year") or ""),
                    "new_year": tgt.old_year,
                }
            )

    return updated, missing, change_log

