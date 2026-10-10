"""One-time move of the saved track list from Music.app's renumbered `id` to its stable `persistent ID`.

Music.app can renumber its track ids; only the persistent ID is guaranteed to stay. A track list written with the old
ids names other tracks after a renumbering, so it is rebuilt from the library once, keeping each track's year history
where the old rows describe it unambiguously.
"""

from __future__ import annotations

import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from core.logger import get_full_log_path
from metrics.track_sync import load_track_list, save_track_map_to_csv

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterable

    from core.models.track_models import AppConfig, TrackDict
    from core.tracks.track_processor import TrackProcessor
    from services.cache.snapshot import LibrarySnapshotService

BACKUP_NAME = "track_list.pre-persistent-id.csv"
_PERSISTENT_ID = re.compile(r"[0-9A-F]{16}")


def is_persistent_id(value: str) -> bool:
    """Return True for a Music.app persistent ID: 16 uppercase hex characters, which may all be digits."""
    return _PERSISTENT_ID.fullmatch(value) is not None


def needs_migration(rows: dict[str, TrackDict]) -> bool:
    """Return True when the saved track list still holds Music.app ids instead of persistent IDs."""
    return any(not is_persistent_id(track_id) for track_id in rows)


def carry_year_history(old_rows: Iterable[TrackDict], tracks: list[TrackDict]) -> int:
    """Copy year history from old rows to the tracks they describe, matched by artist, album and name.

    A track gets history only when every old row with its artist, album and name carries the same history.

    Args:
        old_rows: Rows of the old track list; their ids are not used
        tracks: Tracks read from the library, updated in place

    Returns:
        How many tracks received history
    """
    histories: dict[tuple[str, str, str], set[tuple[str, str]]] = defaultdict(set)
    for row in old_rows:
        histories[row.artist or "", row.album or "", row.name or ""].add((row.year_before_mgu or "", row.year_set_by_mgu or ""))

    carried = 0
    for track in tracks:
        history = histories.get((track.artist or "", track.album or "", track.name or ""), set())
        if len(history) != 1:
            continue
        year_before_mgu, year_set_by_mgu = next(iter(history))
        if year_before_mgu or year_set_by_mgu:
            track.year_before_mgu = year_before_mgu or None
            track.year_set_by_mgu = year_set_by_mgu or None
            carried += 1
    return carried


async def migrate_to_persistent_ids(
    *,
    config: AppConfig,
    track_processor: TrackProcessor,
    snapshot_service: LibrarySnapshotService | None,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> bool:
    """Rebuild the saved track list keyed by persistent ID, once.

    Nothing on disk changes unless the library fetch returned tracks keyed by persistent ID. The fetch itself saves a
    fresh library snapshot; the delta state, which records old ids, is cleared.

    Args:
        config: Application configuration, for the track list path and batch size
        track_processor: Reads the whole library
        snapshot_service: Holds the delta state to clear, when snapshots are enabled
        console_logger: Logger for progress
        error_logger: Logger for a failed fetch

    Returns:
        True when the track list was migrated
    """
    csv_path = Path(get_full_log_path(config, "csv_output_file", "csv/track_list.csv"))
    old_rows = load_track_list(str(csv_path)) if csv_path.exists() else {}
    if not needs_migration(old_rows):
        return False

    console_logger.info("The saved track list uses Music.app ids, which Music.app can renumber; moving it to persistent IDs")
    tracks = await track_processor.fetch_tracks_in_batches(config.batch_processing.batch_size, skip_snapshot_check=True)
    if not tracks or not all(is_persistent_id(str(track.id)) for track in tracks):
        error_logger.error("Persistent ID migration skipped: the library fetch returned no tracks keyed by persistent ID; it runs again next time")
        return False

    backup_path = csv_path.with_name(BACKUP_NAME)
    shutil.copy2(csv_path, backup_path)
    carried = carry_year_history(old_rows.values(), tracks)
    save_track_map_to_csv({str(track.id): track for track in tracks}, str(csv_path), console_logger, error_logger)
    if snapshot_service is not None:
        snapshot_service.clear_delta()

    console_logger.info(
        "Moved the track list to persistent IDs: %d tracks, %d with their year history; the old list is kept at %s",
        len(tracks),
        carried,
        backup_path,
    )
    return True
