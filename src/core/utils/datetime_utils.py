"""Date/time utilities for the application."""

from __future__ import annotations

from datetime import UTC, datetime


def datetime_to_applescript_timestamp(dt: datetime) -> int:
    """Convert datetime to Unix timestamp expected by AppleScript filters.

    Args:
        dt: datetime object (can be naive or aware)

    Returns:
        Unix timestamp as integer, floored to the minute

    """
    aware_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    utc_dt = aware_dt.astimezone(UTC)
    floored = utc_dt.replace(second=0, microsecond=0)
    return int(floored.timestamp())
