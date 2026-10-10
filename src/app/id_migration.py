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


class TrackListMigrationError(RuntimeError):
    """The saved track list still needs the move to persistent IDs, and the library could not be read whole."""


_PERSISTENT_ID = re.compile(r"[0-9A-F]{16}")


def is_persistent_id(value: str) -> bool:
    """Return True for a Music.app persistent ID: 16 uppercase hex characters, which may all be digits."""
    return _PERSISTENT_ID.fullmatch(value) is not None


def _sample(track_ids: set[str], limit: int = 10) -> str:
    """Return up to `limit` of the ids, sorted, for a log line."""
    return ", ".join(sorted(track_ids)[:limit]) or "none"


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

    The track list, its backup and the delta state change only when the fetch returned the whole library keyed by
    persistent ID: the fetched ids must equal the ids Music.app lists, so a fetch cut short by a failed batch is not
    written as the new list. The fetch itself saves a library snapshot when snapshots are enabled; the delta state, which
    still lists old ids, is cleared once the new list is on disk. An existing backup is kept, so a second migration
    cannot replace the original list.

    Args:
        config: Application configuration, for the track list path and batch size
        track_processor: Reads the whole library
        snapshot_service: Holds the delta state to clear, when snapshots are enabled
        console_logger: Logger for progress
        error_logger: Logger for a failed fetch

    Returns:
        True when the track list was migrated, False when it needed no migration

    Raises:
        TrackListMigrationError: The list needs migrating and the library could not be read whole, or the new list could
            not be written; the run must stop, because readers of the old list (auto-verify) would drop its rows
    """
    csv_path = Path(get_full_log_path(config, "csv_output_file", "csv/track_list.csv"))
    old_rows = load_track_list(str(csv_path)) if csv_path.exists() else {}
    if not needs_migration(old_rows):
        return False

    console_logger.info("The saved track list uses Music.app ids, which Music.app can renumber; moving it to persistent IDs")
    tracks = await track_processor.fetch_tracks_in_batches(config.batch_processing.batch_size, skip_snapshot_check=True)
    fetched_ids = {str(track.id) for track in tracks}
    # A track validation rejects reads back the same way on every run, so it cannot hold the migration up
    library_ids = set(await track_processor.ap_client.fetch_all_track_ids()) - track_processor.rejected_track_ids
    if not tracks or not all(is_persistent_id(track_id) for track_id in fetched_ids) or fetched_ids != library_ids:
        error_logger.error(
            "Persistent ID migration stopped the run: read %d tracks, Music.app lists %d; the track list was not changed and the next run "
            "retries. Not read: %s; not listed: %s. Tracks that stay in these lists on every run need a look in Music.app",
            len(fetched_ids),
            len(library_ids),
            _sample(library_ids - fetched_ids),
            _sample(fetched_ids - library_ids),
        )
        message = "the library could not be read whole for the persistent ID migration"
        raise TrackListMigrationError(message)

    backup_path = csv_path.with_name(BACKUP_NAME)
    if not backup_path.exists():
        shutil.copy2(csv_path, backup_path)
    carried = carry_year_history(old_rows.values(), tracks)
    save_track_map_to_csv({str(track.id): track for track in tracks}, str(csv_path), console_logger, error_logger)
    if needs_migration(load_track_list(str(csv_path))):
        message = f"the track list keyed by persistent ID could not be written to {csv_path}"
        raise TrackListMigrationError(message)
    if snapshot_service is not None:
        snapshot_service.clear_delta()

    console_logger.info(
        "Moved the track list to persistent IDs: %d tracks, %d with their year history; the old list is kept at %s",
        len(tracks),
        carried,
        backup_path,
    )
    return True
