"""Tests for AppleScript client utility functions."""

from __future__ import annotations

from datetime import datetime, UTC
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from src.core.utils.datetime_utils import datetime_to_applescript_timestamp


def test_datetime_to_applescript_timestamp_rounds_to_minute() -> None:
    dt = datetime(2024, 5, 1, 10, 5, 59, 123456, tzinfo=UTC)
    expected = int(datetime(2024, 5, 1, 10, 5, tzinfo=UTC).timestamp())
    assert datetime_to_applescript_timestamp(dt) == expected


def test_datetime_to_applescript_timestamp_handles_dst() -> None:
    try:
        tz = ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError:
        pytest.skip("ZoneInfo database missing for America/Los_Angeles")

    before = datetime(2024, 3, 10, 1, 59, tzinfo=tz)
    after = datetime(2024, 3, 10, 3, 1, tzinfo=tz)

    delta_seconds = datetime_to_applescript_timestamp(after) - datetime_to_applescript_timestamp(before)
    assert 0 < delta_seconds < 4000
