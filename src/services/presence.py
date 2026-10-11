"""Whether a person is at the Mac: the console is this user's and the input devices were used recently.

Music.app writes nothing that tells a manual edit from Apple's own change; presence is the only separating fact
(a person edits at the Mac, Apple does not), so the year step asks a dialog only when someone is there to answer it.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

HID_IDLE_PATTERN = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')
NANOSECONDS_PER_SECOND = 1_000_000_000
IOREG_TIMEOUT_SECONDS = 10


def parse_hid_idle_seconds(ioreg_output: str) -> float | None:
    """Return the seconds since the last keyboard or mouse input from `ioreg -c IOHIDSystem` output, or None when absent.

    IOKit reports `HIDIdleTime` in nanoseconds.
    """
    match = HID_IDLE_PATTERN.search(ioreg_output)
    return int(match.group(1)) / NANOSECONDS_PER_SECOND if match else None


async def hid_idle_seconds() -> float | None:
    """Read the idle time from IOKit; None when `ioreg` is missing, fails or times out."""
    try:
        process = await asyncio.create_subprocess_exec(
            "ioreg", "-c", "IOHIDSystem", "-d", "4", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=IOREG_TIMEOUT_SECONDS)
    except (OSError, TimeoutError):
        return None
    return parse_hid_idle_seconds(stdout.decode(errors="replace"))


def console_user_is_me() -> bool:
    """Return True when the console (the logged-in GUI session) belongs to the user running the tool."""
    try:
        return Path("/dev/console").stat().st_uid == os.getuid()
    except OSError:
        return False


async def is_user_present(
    idle_threshold_seconds: int,
    *,
    idle_seconds: Callable[[], Awaitable[float | None]] = hid_idle_seconds,
    console_is_mine: Callable[[], bool] = console_user_is_me,
) -> bool:
    """Return True when the console is this user's and input happened within `idle_threshold_seconds`.

    Args:
        idle_threshold_seconds: Idle time at or above which nobody counts as present
        idle_seconds: Reads the idle time (injectable for tests)
        console_is_mine: Checks the console owner (injectable for tests)

    Returns:
        Whether a person can answer a dialog right now
    """
    if not console_is_mine():
        return False
    idle = await idle_seconds()
    return idle is not None and idle < idle_threshold_seconds
