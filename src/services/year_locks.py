"""Year locks: albums whose year a person chose, kept in csv/year_locks.csv.

The year step restores a locked year and never asks the providers about the album.
The file is plain CSV so it can be edited by hand: it is read at start and again before every change, and rewritten whole,
rows the tool cannot read included, so nothing a person typed is lost. A file that cannot be read at all is never rewritten.
"""

from __future__ import annotations

import asyncio
import csv
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core.logger import PLAIN_TEXT, get_full_log_path
from core.models.cache_types import LockSource, YearLock

if TYPE_CHECKING:
    import logging

    from core.models.track_models import AppConfig

FIELDNAMES = ["artist", "album", "year", "locked_at", "source"]
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

Row = dict[str, str]


def album_key(artist: str, album: str) -> tuple[str, str]:
    """Return the key a lock is stored under: the album artist and album name, trimmed, as the year step groups albums.

    The pipeline cleans album names before the year step; lock_year and unlock_year try a typed name as given and cleaned.
    """
    return artist.strip(), album.strip()


class YearLockService:
    """Reads, answers and writes the year locks.

    Args:
        config: Typed application configuration (`logging.year_locks_file`)
        console_logger: Logger for lock and unlock messages and for rows the file cannot use
        error_logger: Logger for a file that cannot be read
    """

    def __init__(self, config: AppConfig, console_logger: logging.Logger, error_logger: logging.Logger) -> None:
        self.config = config
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.locks_file_path = get_full_log_path(config, "year_locks_file", "csv/year_locks.csv", error_logger)
        self._locks: dict[tuple[str, str], YearLock] = {}
        # Rows the tool cannot read are written back as they were: the file is the person's
        self._unreadable_rows: list[Row] = []
        self._read_failed = False
        self._guard = asyncio.Lock()

    async def initialize(self) -> None:
        """Load the locks from disk (a missing file means no locks; a file that cannot be read means no locks and no rewrites)."""
        await asyncio.to_thread(self._load)
        self.console_logger.info("Year locks loaded: %d", len(self._locks))

    def get_locked_year(self, artist: str, album: str) -> str | None:
        """Return the album's locked year, or None when it is not locked."""
        lock = self._locks.get(album_key(artist, album))
        return lock.year if lock else None

    def all_locks(self) -> list[YearLock]:
        """Return every lock, in file order."""
        return list(self._locks.values())

    async def lock(self, artist: str, album: str, year: str, *, source: LockSource) -> YearLock:
        """Record (or replace) the album's lock and write the file.

        Args:
            artist: Album artist
            album: Album name
            year: The year to keep
            source: Dialog answer or command

        Returns:
            The lock as recorded; a lock that could not be read back (blank names, a year that is not four digits) is refused with
            ValueError, and a file that could not be read is not rewritten (OSError)
        """
        key = album_key(artist, album)
        lock = YearLock(artist=key[0], album=key[1], year=year.strip(), locked_at=datetime.now(UTC), source=source)
        async with self._guard:
            # A hand edit made since the last read must survive the rewrite
            await asyncio.to_thread(self._load)
            self._locks[key] = lock
            await asyncio.to_thread(self._write)
        self.console_logger.info("Locked '%s - %s' at %s (%s)", lock.artist, lock.album, lock.year, source.value, extra=PLAIN_TEXT)
        return lock

    async def unlock(self, artist: str, album: str) -> bool:
        """Remove the album's lock; return False when there was none (a file that could not be read is not rewritten: OSError)."""
        key = album_key(artist, album)
        async with self._guard:
            await asyncio.to_thread(self._load)
            if key not in self._locks:
                return False
            del self._locks[key]
            await asyncio.to_thread(self._write)
        self.console_logger.info("Unlocked '%s - %s'", key[0], key[1], extra=PLAIN_TEXT)
        return True

    def _load(self) -> None:
        path = Path(self.locks_file_path)
        self._locks, self._unreadable_rows, self._read_failed = {}, [], False
        if not path.exists():
            return
        try:
            # utf-8-sig: Numbers and Excel save a byte order mark, which would otherwise hide the first column
            with path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, UnicodeDecodeError, csv.Error) as error:
            self.error_logger.exception("Year locks file %s could not be read", path)
            self._refuse_rewrites(path, error)
            return
        for line_number, row in enumerate(rows, start=2):
            lock = self._parse_row(row, line_number)
            if lock is None:
                self._unreadable_rows.append(row)
            else:
                self._locks[album_key(lock.artist, lock.album)] = lock

    def _refuse_rewrites(self, path: Path, error: Exception) -> None:
        """Run without locks and never rewrite a file that could not be read; the console hears why."""
        self._read_failed = True
        self.console_logger.error("%s could not be read (%s); running without locks and not rewriting it", path, error, extra=PLAIN_TEXT)

    def _parse_row(self, row: Row, line_number: int) -> YearLock | None:
        artist = (row.get("artist") or "").strip()
        album = (row.get("album") or "").strip()
        year = (row.get("year") or "").strip()
        source_text = (row.get("source") or "").strip() or LockSource.COMMAND.value
        locked_at_text = (row.get("locked_at") or "").strip()
        try:
            # A row added by hand carries no time: it is locked from now
            locked_at = datetime.strptime(locked_at_text, TIMESTAMP_FORMAT).replace(tzinfo=UTC) if locked_at_text else datetime.now(UTC)
            return YearLock(artist=artist, album=album, year=year, locked_at=locked_at, source=LockSource(source_text))
        except ValueError as error:
            self.console_logger.warning("Skipping year_locks.csv line %d (%s); the row is kept as it is", line_number, error, extra=PLAIN_TEXT)
            return None

    def _write(self) -> None:
        path = Path(self.locks_file_path)
        if self._read_failed:
            msg = f"{path} could not be read; not rewriting it"
            raise OSError(msg)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".csv.tmp")
        with temp_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            for lock in self._locks.values():
                writer.writerow(
                    {
                        "artist": lock.artist,
                        "album": lock.album,
                        "year": lock.year,
                        "locked_at": lock.locked_at.strftime(TIMESTAMP_FORMAT),
                        "source": lock.source.value,
                    }
                )
            writer.writerows(self._unreadable_rows)
            # The new file must be on disk before it replaces the old one, or a power loss leaves an empty lock file
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
