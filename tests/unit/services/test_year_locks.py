"""Albums whose year a person chose: kept in csv/year_locks.csv, hand-editable, re-read on start."""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from core.models.cache_types import LockSource, YearLock
from services.year_locks import YearLockService
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


@pytest.fixture
def service(tmp_path: Path) -> YearLockService:
    """A lock service over a scratch logs directory."""
    config = create_test_app_config(logs_base_dir=str(tmp_path))
    return YearLockService(config, logging.getLogger("test.console"), logging.getLogger("test.error"))


@pytest.mark.asyncio
async def test_a_lock_survives_a_restart(service: YearLockService) -> None:
    await service.initialize()
    await service.lock("In Flames", "Battles", "2019", source=LockSource.DIALOG)

    reloaded = YearLockService(service.config, logging.getLogger("t"), logging.getLogger("t"))
    await reloaded.initialize()

    assert reloaded.get_locked_year("In Flames", "Battles") == "2019"
    assert reloaded.all_locks()[0].source is LockSource.DIALOG


@pytest.mark.asyncio
async def test_unlock_removes_the_lock_and_reports_whether_there_was_one(service: YearLockService) -> None:
    await service.initialize()
    await service.lock("A", "B", "2001", source=LockSource.COMMAND)

    assert await service.unlock("A", "B") is True
    assert await service.unlock("A", "B") is False
    assert service.get_locked_year("A", "B") is None


@pytest.mark.asyncio
async def test_a_hand_written_file_loads(service: YearLockService) -> None:
    path = Path(service.locks_file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("artist,album,year,locked_at,source\nDas Ich,Lava,2004,2026-10-11 12:00:00,command\n", encoding="utf-8")

    await service.initialize()

    assert service.get_locked_year("Das Ich", "Lava") == "2004"


@pytest.mark.asyncio
async def test_bad_rows_are_skipped_and_logged(service: YearLockService, caplog: pytest.LogCaptureFixture) -> None:
    path = Path(service.locks_file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "artist,album,year,locked_at,source\nA,B,,2026-10-11 12:00:00,command\nC,D,2004,not a date,command\nE,F,2004,2026-10-11 12:00:00,dialog\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="test.console"):
        await service.initialize()

    assert [lock.album for lock in service.all_locks()] == ["F"]
    assert sum("year_locks.csv" in record.message and record.name == "test.console" for record in caplog.records) == 2


@pytest.mark.asyncio
async def test_keys_ignore_surrounding_whitespace(service: YearLockService) -> None:
    await service.initialize()
    await service.lock(" A ", "B ", "2001", source=LockSource.COMMAND)

    assert service.get_locked_year("A", " B") == "2001"


@pytest.mark.asyncio
async def test_locking_again_replaces_the_year(service: YearLockService) -> None:
    await service.initialize()
    await service.lock("A", "B", "2001", source=LockSource.COMMAND)
    await service.lock("A", "B", "2002", source=LockSource.DIALOG)

    assert service.get_locked_year("A", "B") == "2002"
    assert len(service.all_locks()) == 1


@pytest.mark.asyncio
async def test_a_lock_that_could_not_be_read_back_is_refused(service: YearLockService) -> None:
    await service.initialize()

    with pytest.raises(ValueError, match="four-digit"):
        await service.lock("A", "B", "", source=LockSource.DIALOG)

    assert service.all_locks() == []


class TestYearLockInvariants:
    """A lock that could not be read back is refused at construction, whichever site builds it."""

    def test_empty_artist_or_album_is_refused(self) -> None:
        with pytest.raises(ValueError, match="artist and album"):
            YearLock(artist=" ", album="B", year="2001", locked_at=datetime.now(UTC), source=LockSource.DIALOG)
        with pytest.raises(ValueError, match="artist and album"):
            YearLock(artist="A", album="", year="2001", locked_at=datetime.now(UTC), source=LockSource.DIALOG)

    def test_a_year_that_is_not_four_digits_is_refused(self) -> None:
        with pytest.raises(ValueError, match="four-digit"):
            YearLock(artist="A", album="B", year="19", locked_at=datetime.now(UTC), source=LockSource.COMMAND)

    def test_a_naive_timestamp_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            YearLock(artist="A", album="B", year="2001", locked_at=datetime.now(UTC).replace(tzinfo=None), source=LockSource.COMMAND)


@pytest.mark.asyncio
async def test_a_lock_without_an_artist_is_refused(service: YearLockService) -> None:
    await service.initialize()

    with pytest.raises(ValueError, match="artist and album"):
        await service.lock("", "B", "2001", source=LockSource.DIALOG)

    assert service.all_locks() == []


def _write_file(service: YearLockService, text: str, *, encoding: str = "utf-8") -> Path:
    path = Path(service.locks_file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return path


class TestHandEdits:
    """The file is the person's: whatever an editor or a hand adds to it must survive the tool's own reads and writes."""

    @pytest.mark.asyncio
    async def test_a_file_saved_with_a_byte_order_mark_loads(self, service: YearLockService) -> None:
        """Numbers and Excel export UTF-8 with a BOM; it must not hide the first column."""
        _write_file(service, "artist,album,year,locked_at,source\nDas Ich,Lava,2004,2026-10-11 12:00:00,command\n", encoding="utf-8-sig")

        await service.initialize()

        assert service.get_locked_year("Das Ich", "Lava") == "2004"

    @pytest.mark.asyncio
    async def test_a_row_without_locked_at_or_source_is_stamped_now(self, service: YearLockService) -> None:
        _write_file(service, "artist,album,year\nDas Ich,Lava,2004\n")

        await service.initialize()

        lock = service.all_locks()[0]
        assert (lock.year, lock.source) == ("2004", LockSource.COMMAND)
        assert abs((datetime.now(UTC) - lock.locked_at).total_seconds()) < 60

    @pytest.mark.asyncio
    async def test_a_row_the_tool_cannot_read_survives_a_rewrite(self, service: YearLockService) -> None:
        path = _write_file(service, "artist,album,year,locked_at,source\nA,B,,2026-10-11 12:00:00,command\nE,F,2004,2026-10-11 12:00:00,dialog\n")
        await service.initialize()

        await service.lock("G", "H", "2001", source=LockSource.COMMAND)

        text = path.read_text(encoding="utf-8")
        assert "A,B,,2026-10-11 12:00:00,command" in text
        assert service.get_locked_year("G", "H") == "2001"

    @pytest.mark.asyncio
    async def test_a_hand_edit_made_during_the_run_survives_a_lock(self, service: YearLockService) -> None:
        await service.initialize()
        _write_file(service, "artist,album,year,locked_at,source\nDas Ich,Lava,2004,2026-10-11 12:00:00,command\n")

        await service.lock("A", "B", "2001", source=LockSource.COMMAND)

        reloaded = YearLockService(service.config, logging.getLogger("t"), logging.getLogger("t"))
        await reloaded.initialize()
        assert {(lock.artist, lock.album) for lock in reloaded.all_locks()} == {("Das Ich", "Lava"), ("A", "B")}

    @pytest.mark.asyncio
    async def test_a_hand_edit_made_during_the_run_survives_an_unlock(self, service: YearLockService) -> None:
        await service.initialize()
        await service.lock("A", "B", "2001", source=LockSource.COMMAND)
        path = Path(service.locks_file_path)
        path.write_text(path.read_text(encoding="utf-8") + "Das Ich,Lava,2004,2026-10-11 12:00:00,command\n", encoding="utf-8")

        assert await service.unlock("A", "B") is True

        reloaded = YearLockService(service.config, logging.getLogger("t"), logging.getLogger("t"))
        await reloaded.initialize()
        assert [(lock.artist, lock.album) for lock in reloaded.all_locks()] == [("Das Ich", "Lava")]

    @pytest.mark.asyncio
    async def test_a_file_that_cannot_be_read_is_never_rewritten(self, service: YearLockService, caplog: pytest.LogCaptureFixture) -> None:
        path = Path(service.locks_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        original = b"artist,album,year,locked_at,source\nCaf\xe9,B,2004,2026-10-11 12:00:00,command\n"
        path.write_bytes(original)

        with caplog.at_level(logging.ERROR, logger="test.console"):
            await service.initialize()

        assert any(str(path) in record.getMessage() for record in caplog.records)
        with pytest.raises(OSError, match="not rewriting"):
            await service.lock("A", "B", "2001", source=LockSource.COMMAND)
        assert path.read_bytes() == original

    @pytest.mark.asyncio
    async def test_the_new_file_reaches_the_disk_before_it_replaces_the_old(self, service: YearLockService) -> None:
        await service.initialize()
        order: list[str] = []
        fcntl = MagicMock()
        fcntl.fcntl = MagicMock(side_effect=lambda *_: order.append("sync"))

        with (
            patch("services.year_locks.fcntl", fcntl),
            patch("services.year_locks.Path.replace", side_effect=lambda *_: order.append("replace")),
        ):
            await service.lock("A", "B", "2001", source=LockSource.COMMAND)

        assert order == ["sync", "replace"]

    @pytest.mark.asyncio
    async def test_a_header_in_another_case_or_with_spaces_still_reads(self, service: YearLockService) -> None:
        """A person types the header the docs show, with spaces, or capitalised; the row must not turn unreadable."""
        _write_file(service, "Artist, Album, Year, Locked_at, Source\nIn Flames, Battles, 2019\n")

        await service.initialize()

        assert service.get_locked_year("In Flames", "Battles") == "2019"

    @pytest.mark.asyncio
    async def test_a_file_whose_header_lacks_the_key_columns_is_never_rewritten(
        self, service: YearLockService, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A semicolon-separated export has one column; rewriting it would turn every row into empty fields."""
        path = _write_file(service, "artist;album;year;locked_at;source\nA;B;2001;;\n")
        original = path.read_bytes()

        with caplog.at_level(logging.ERROR, logger="test.console"):
            await service.initialize()

        assert any(str(path) in record.getMessage() for record in caplog.records)
        with pytest.raises(OSError, match="not rewriting"):
            await service.lock("C", "D", "2001", source=LockSource.COMMAND)
        assert path.read_bytes() == original

    @pytest.mark.asyncio
    async def test_a_row_with_extra_fields_keeps_them_across_a_rewrite(self, service: YearLockService) -> None:
        """An unquoted comma in a hand-typed album name splits the row; the row still goes back as it was."""
        row = "Artist,Songs, Vol. 1,1999,2026-10-11 10:00:00,command"
        path = _write_file(service, f"artist,album,year,locked_at,source\n{row}\n")
        await service.initialize()

        await service.lock("C", "D", "2001", source=LockSource.COMMAND)

        assert row in path.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_a_re_read_that_fails_keeps_the_locks_loaded_at_start(self, service: YearLockService) -> None:
        await service.initialize()
        await service.lock("A", "B", "2001", source=LockSource.COMMAND)
        Path(service.locks_file_path).write_bytes(b"artist,album,year,locked_at,source\nCaf\xe9,B,2004,2026-10-11 12:00:00,command\n")

        with pytest.raises(OSError, match="not rewriting"):
            await service.lock("C", "D", "2001", source=LockSource.COMMAND)

        assert service.get_locked_year("A", "B") == "2001"
        assert service.get_locked_year("C", "D") is None

    @pytest.mark.asyncio
    async def test_unlock_on_a_file_that_cannot_be_read_raises(self, service: YearLockService) -> None:
        await service.initialize()
        await service.lock("A", "B", "2001", source=LockSource.COMMAND)
        Path(service.locks_file_path).write_bytes(b"artist,album,year,locked_at,source\nCaf\xe9,B,2004,2026-10-11 12:00:00,command\n")

        with pytest.raises(OSError, match="not rewriting"):
            await service.unlock("A", "B")

    @pytest.mark.asyncio
    async def test_a_lock_whose_write_fails_is_not_remembered(self, service: YearLockService) -> None:
        await service.initialize()

        with patch("services.year_locks.Path.replace", side_effect=OSError("disk full")), pytest.raises(OSError, match="disk full"):
            await service.lock("C", "D", "2001", source=LockSource.COMMAND)

        assert service.get_locked_year("C", "D") is None

    @pytest.mark.asyncio
    async def test_the_locks_stay_readable_while_the_file_is_re_read(self, service: YearLockService) -> None:
        """Albums are judged concurrently: while one album's lock re-reads the file, another must still see its lock."""
        await service.initialize()
        await service.lock("A", "B", "2001", source=LockSource.COMMAND)
        seen: list[str | None] = []
        real_reader = csv.reader

        def sampling_reader(csvfile: Iterable[str]) -> Iterator[list[str]]:
            """Read as csv.reader does, sampling the lock between rows."""
            for row in real_reader(csvfile):
                seen.append(service.get_locked_year("A", "B"))
                yield row

        with patch("services.year_locks.csv.reader", sampling_reader):
            await service.lock("C", "D", "2001", source=LockSource.COMMAND)

        assert seen
        assert set(seen) == {"2001"}

    @pytest.mark.asyncio
    async def test_an_unreadable_row_is_said_once_not_on_every_lock(self, service: YearLockService, caplog: pytest.LogCaptureFixture) -> None:
        _write_file(service, "artist,album,year,locked_at,source\nA,B,,2026-10-11 12:00:00,command\n")

        with caplog.at_level(logging.WARNING, logger="test.console"):
            await service.initialize()
            for album in ("C", "D", "E"):
                await service.lock("X", album, "2001", source=LockSource.COMMAND)

        assert sum("year_locks.csv" in record.getMessage() and record.name == "test.console" for record in caplog.records) == 1
