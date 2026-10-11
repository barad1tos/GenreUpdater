"""Albums whose year a person chose: kept in csv/year_locks.csv, hand-editable, re-read on start."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.models.cache_types import LockSource
from services.year_locks import YearLockService
from tests.factories import create_test_app_config


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

    with caplog.at_level(logging.WARNING, logger="test.error"):
        await service.initialize()

    assert [lock.album for lock in service.all_locks()] == ["F"]
    assert sum("year_locks.csv" in record.message for record in caplog.records) == 2


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
