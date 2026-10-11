"""Someone is at the Mac when the console is theirs and the input devices were used recently."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.presence import hid_idle_seconds, is_user_present, parse_hid_idle_seconds

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
async def test_absent_when_the_console_belongs_to_someone_else() -> None:
    assert await is_user_present(600, idle_seconds=lambda: _idle(1.0), console_is_mine=lambda: False) is False


@pytest.mark.asyncio
async def test_unknown_idle_time_means_presence_is_unknown() -> None:
    """A failed ioreg read is not an absent person; the caller says so instead of claiming nobody is there."""
    assert await is_user_present(600, idle_seconds=lambda: _idle(None), console_is_mine=lambda: True) is None


@pytest.mark.asyncio
async def test_a_hung_ioreg_is_killed_and_reads_as_unknown() -> None:
    process = MagicMock()
    process.communicate = AsyncMock(side_effect=TimeoutError)
    process.wait = AsyncMock()

    with patch("core.presence.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        assert await hid_idle_seconds() is None

    process.kill.assert_called_once()


@pytest.mark.asyncio
async def test_a_process_that_ended_before_the_kill_still_reads_as_unknown() -> None:
    process = MagicMock()
    process.communicate = AsyncMock(side_effect=TimeoutError)
    process.wait = AsyncMock()
    process.kill = MagicMock(side_effect=ProcessLookupError)

    with patch("core.presence.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        assert await hid_idle_seconds() is None
