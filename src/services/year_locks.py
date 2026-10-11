"""Year locks: albums whose year a person chose, kept in csv/year_locks.csv.

The year step restores a locked year and never asks the providers about the album.
The file is plain CSV so it can be edited by hand: it is read at start and again before every change, and rewritten whole,
rows the tool cannot read included, so nothing a person typed is lost. A file that cannot be read at all is never rewritten.
"""

from __future__ import annotations

import asyncio
import csv
import fcntl
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from core.logger import PLAIN_TEXT, get_full_log_path
from core.models.cache_types import LockSource, YearLock

if TYPE_CHECKING:
    import logging

    from core.models.track_models import AppConfig

FIELDNAMES = ["artist", "album", "year", "locked_at", "source"]
KEY_COLUMNS = frozenset({"artist", "album", "year"})
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

AlbumKey = tuple[str, str]


class UnreadableRow(NamedTuple):
    """A row the tool could not turn into a lock: written back as it was, and named once."""

    line_number: int
    fields: list[str]
    reason: str


class LockFile(NamedTuple):
    """What a read of the file gave: the locks by key and the rows that are not locks."""

    locks: dict[AlbumKey, YearLock]
    unreadable: list[UnreadableRow]


def album_key(artist: str, album: str) -> AlbumKey:
    """Return the key a lock is stored under: the album artist and album name, trimmed, as the year step groups albums.

    The pipeline cleans album names before the year step; lock_year and unlock_year try a typed name as given and cleaned.
    """
    return artist.strip(), album.strip()


def _sync_to_disk(fileno: int) -> None:
    """Make the file's bytes reach the disk, past the drive's cache where the platform allows (macOS F_FULLFSYNC)."""
    if hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fileno, fcntl.F_FULLFSYNC)
    else:
        os.fsync(fileno)


class YearLockService:
    """Reads, answers and writes the year locks.

    Args:
        config: Typed application configuration (`logging.year_locks_file`)
        console_logger: Logger for lock and unlock messages and for rows and files the tool cannot read
        error_logger: Logger for a file that cannot be read
    """

    def __init__(self, config: AppConfig, console_logger: logging.Logger, error_logger: logging.Logger) -> None:
        self.config = config
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.locks_file_path = get_full_log_path(config, "year_locks_file", "csv/year_locks.csv", error_logger)
        # Replaced as a whole after every successful read or write: a reader on the event loop never sees a half-built dict
        self._locks: dict[AlbumKey, YearLock] = {}
        self._unreadable: list[UnreadableRow] = []
        self._guard = asyncio.Lock()

    async def initialize(self) -> None:
        """Load the locks from disk (a missing file means no locks; a file that cannot be read means no locks and no rewrites)."""
        if (loaded := await asyncio.to_thread(self._read)) is not None:
            self._adopt(loaded)
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
            current = await self._reread()
            await asyncio.to_thread(self._write, LockFile({**current.locks, key: lock}, current.unreadable))
        self.console_logger.info("Locked '%s - %s' at %s (%s)", lock.artist, lock.album, lock.year, source.value, extra=PLAIN_TEXT)
        return lock

    async def unlock(self, artist: str, album: str) -> bool:
        """Remove the album's lock; return False when there was none (a file that could not be read is not rewritten: OSError)."""
        key = album_key(artist, album)
        async with self._guard:
            current = await self._reread()
            if key not in current.locks:
                return False
            await asyncio.to_thread(self._write, LockFile({k: v for k, v in current.locks.items() if k != key}, current.unreadable))
        self.console_logger.info("Unlocked '%s - %s'", key[0], key[1], extra=PLAIN_TEXT)
        return True

    async def _reread(self) -> LockFile:
        """Read the file again before a change, so a hand edit made since the last read survives; refuse when it cannot be read."""
        loaded = await asyncio.to_thread(self._read)
        if loaded is None:
            msg = f"{self.locks_file_path} could not be read; not rewriting it"
            raise OSError(msg)
        self._adopt(loaded)
        return loaded

    def _adopt(self, loaded: LockFile) -> None:
        """Take a read as the current state; name the rows that are not locks, once each."""
        known = {(tuple(row.fields), row.reason) for row in self._unreadable}
        for row in loaded.unreadable:
            if (tuple(row.fields), row.reason) not in known:
                self.console_logger.warning(
                    "Skipping year_locks.csv line %d (%s); the row is kept as it is", row.line_number, row.reason, extra=PLAIN_TEXT
                )
        self._locks, self._unreadable = loaded.locks, loaded.unreadable

    def _read(self) -> LockFile | None:
        """Read the file; None, said on the console and in the error log, when it cannot be read and so must not be rewritten."""
        path = Path(self.locks_file_path)
        if not path.exists():
            return LockFile({}, [])
        try:
            # utf-8-sig: Numbers and Excel save a byte order mark, which would otherwise hide the first column
            with path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
        except (OSError, UnicodeDecodeError, csv.Error) as error:
            self.error_logger.exception("Year locks file %s could not be read", path)
            self._say_unreadable(path, str(error))
            return None
        if not rows:
            return LockFile({}, [])
        # A hand-typed header may carry spaces or capitals; a header without the key columns is another format altogether
        header = [name.strip().casefold() for name in rows[0]]
        if not KEY_COLUMNS.issubset(header):
            self._say_unreadable(path, f"its header {rows[0]} lacks {sorted(KEY_COLUMNS)}")
            return None
        locks: dict[AlbumKey, YearLock] = {}
        unreadable: list[UnreadableRow] = []
        for line_number, fields in enumerate(rows[1:], start=2):
            row = dict(zip(header, fields, strict=False))
            try:
                lock = self._parse_row(row)
            except ValueError as error:
                unreadable.append(UnreadableRow(line_number, fields, str(error)))
            else:
                locks[album_key(lock.artist, lock.album)] = lock
        return LockFile(locks, unreadable)

    def _say_unreadable(self, path: Path, reason: str) -> None:
        self.console_logger.error("%s could not be read (%s); running without locks and not rewriting it", path, reason, extra=PLAIN_TEXT)

    @staticmethod
    def _parse_row(row: dict[str, str]) -> YearLock:
        """Build the lock a row describes; ValueError names what is wrong with it."""
        source_text = (row.get("source") or "").strip() or LockSource.COMMAND.value
        locked_at_text = (row.get("locked_at") or "").strip()
        # A row added by hand carries no time: it is locked from now
        locked_at = datetime.strptime(locked_at_text, TIMESTAMP_FORMAT).replace(tzinfo=UTC) if locked_at_text else datetime.now(UTC)
        return YearLock(
            artist=(row.get("artist") or "").strip(),
            album=(row.get("album") or "").strip(),
            year=(row.get("year") or "").strip(),
            locked_at=locked_at,
            source=LockSource(source_text),
        )

    def _write(self, content: LockFile) -> None:
        """Write the whole file through a temp file synced to disk, then take it as the current state."""
        path = Path(self.locks_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".csv.tmp")
        with temp_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(FIELDNAMES)
            for lock in content.locks.values():
                writer.writerow([lock.artist, lock.album, lock.year, lock.locked_at.strftime(TIMESTAMP_FORMAT), lock.source.value])
            writer.writerows(row.fields for row in content.unreadable)
            # The new file must be on disk before it replaces the old one, or a power loss leaves an empty lock file
            handle.flush()
            _sync_to_disk(handle.fileno())
        temp_path.replace(path)
        self._locks, self._unreadable = content.locks, content.unreadable
