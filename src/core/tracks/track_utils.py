"""Shared track utility functions.

This module provides common utilities for track operations that are used
across multiple modules to avoid code duplication.
"""

from datetime import UTC, datetime

from core.models.track_models import TrackDict


def is_missing_or_unknown_genre(track: TrackDict) -> bool:
    """Check if track has missing or unknown genre.

    Args:
        track: Track to check

    Returns:
        True if genre is missing, empty, or 'unknown'

    """
    genre_val = track.get("genre", "")

    # Check type before applying string operations
    if not isinstance(genre_val, str):
        return True

    genre_stripped = genre_val.strip()
    return not genre_stripped or genre_stripped.lower() in {"unknown", ""}


def parse_track_date_added(track: TrackDict) -> datetime | None:
    """Parse track's date_added field to datetime.

    Args:
        track: Track with date_added field

    Returns:
        Parsed datetime with UTC timezone, or None if parsing fails

    """
    try:
        date_added_str = track.get("date_added", "")
        if isinstance(date_added_str, str) and date_added_str:
            return datetime.strptime(date_added_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None
    return None
