"""Year locks: albums whose year a person chose, kept in csv/year_locks.csv.

The year step restores a locked year and never asks the providers about the album; the periodic recheck (PR C) skips it.
The file is plain CSV so it can be edited by hand; it is read once at start and rewritten whole on every change.
"""

from __future__ import annotations

import asyncio
import csv
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
YEAR_LENGTH = 4


def album_key(artist: str, album: str) -> tuple[str, str]:
    """Return the key a lock is stored under: the album artist and album name, trimmed, as the year step groups albums.

    The pipeline cleans album names before the year step, and the CLI cleans a typed name before it looks a lock up.
    """
    return artist.strip(), album.strip()


class YearLockService:
    """Reads, answers and writes the year locks.

    Args:
        config: Typed application configuration (`logging.year_locks_file`)
        console_logger: Logger for lock and unlock messages
        error_logger: Logger for rows the file cannot use
    """

    def __init__(self, config: AppConfig, console_logger: logging.Logger, error_logger: logging.Logger) -> None:
        self.config = config
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.locks_file_path = get_full_log_path(config, "year_locks_file", "csv/year_locks.csv", error_logger)
        self._locks: dict[tuple[str, str], YearLock] = {}
        self._guard = asyncio.Lock()

    async def initialize(self) -> None:
        """Load the locks from disk (a missing file means no locks)."""
        self._locks = await asyncio.to_thread(self._read)
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
            The lock as recorded

        Raises:
            ValueError: The year is not four digits, so the lock could never be read back
        """
        year = year.strip()
        if len(year) != YEAR_LENGTH or not year.isdigit():
            msg = f"A lock needs a four-digit year, got '{year}'"
            raise ValueError(msg)
        key = album_key(artist, album)
        lock = YearLock(artist=key[0], album=key[1], year=year, locked_at=datetime.now(UTC), source=source)
        async with self._guard:
            self._locks[key] = lock
            await asyncio.to_thread(self._write, list(self._locks.values()))
        self.console_logger.info("Locked '%s - %s' at %s (%s)", lock.artist, lock.album, lock.year, source.value, extra=PLAIN_TEXT)
        return lock

    async def unlock(self, artist: str, album: str) -> bool:
        """Remove the album's lock; return False when there was none."""
        key = album_key(artist, album)
        async with self._guard:
            if key not in self._locks:
                return False
            del self._locks[key]
            await asyncio.to_thread(self._write, list(self._locks.values()))
        self.console_logger.info("Unlocked '%s - %s'", key[0], key[1], extra=PLAIN_TEXT)
        return True

    def _read(self) -> dict[tuple[str, str], YearLock]:
        path = Path(self.locks_file_path)
        if not path.exists():
            return {}
        locks: dict[tuple[str, str], YearLock] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            for line_number, row in enumerate(csv.DictReader(handle), start=2):
                lock = self._parse_row(row, line_number)
                if lock is not None:
                    locks[album_key(lock.artist, lock.album)] = lock
        return locks

    def _parse_row(self, row: dict[str, str], line_number: int) -> YearLock | None:
        artist = (row.get("artist") or "").strip()
        album = (row.get("album") or "").strip()
        year = (row.get("year") or "").strip()
        source_text = (row.get("source") or LockSource.COMMAND.value).strip()
        if not artist or not album or len(year) != YEAR_LENGTH or not year.isdigit() or source_text not in LockSource:
            self.error_logger.warning("Skipping year_locks.csv line %d: needs artist, album, a four-digit year and a known source", line_number)
            return None
        try:
            locked_at = datetime.strptime((row.get("locked_at") or "").strip(), TIMESTAMP_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            self.error_logger.warning("Skipping year_locks.csv line %d: locked_at is not %s", line_number, TIMESTAMP_FORMAT)
            return None
        return YearLock(artist=artist, album=album, year=year, locked_at=locked_at, source=LockSource(source_text))

    def _write(self, locks: list[YearLock]) -> None:
        path = Path(self.locks_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".csv.tmp")
        with temp_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            for lock in locks:
                writer.writerow(
                    {
                        "artist": lock.artist,
                        "album": lock.album,
                        "year": lock.year,
                        "locked_at": lock.locked_at.strftime(TIMESTAMP_FORMAT),
                        "source": lock.source.value,
                    }
                )
        temp_path.replace(path)
