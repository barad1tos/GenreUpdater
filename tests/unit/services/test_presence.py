"""Someone is at the Mac when the console is theirs and the input devices were used recently."""

from __future__ import annotations

import pytest

from services.presence import is_user_present, parse_hid_idle_seconds

IOREG_SAMPLE = '    |   "HIDIdleTime" = 2437802500\n    |   "HIDParameters" = {...}\n'


def test_idle_time_is_reported_in_nanoseconds() -> None:
    assert parse_hid_idle_seconds(IOREG_SAMPLE) == pytest.approx(2.4378025)


def test_missing_idle_time_reads_as_none() -> None:
    assert parse_hid_idle_seconds("no such key") is None


async def _idle(seconds: float | None) -> float | None:
    return seconds


@pytest.mark.asyncio
async def test_present_when_the_console_is_mine_and_input_is_recent() -> None:
    assert await is_user_present(600, idle_seconds=lambda: _idle(12.0), console_is_mine=lambda: True)


@pytest.mark.asyncio
async def test_absent_after_the_idle_threshold() -> None:
    assert not await is_user_present(600, idle_seconds=lambda: _idle(600.0), console_is_mine=lambda: True)


@pytest.mark.asyncio
async def test_absent_when_the_console_belongs_to_someone_else_or_idle_is_unknown() -> None:
    assert not await is_user_present(600, idle_seconds=lambda: _idle(1.0), console_is_mine=lambda: False)
    assert not await is_user_present(600, idle_seconds=lambda: _idle(None), console_is_mine=lambda: True)
