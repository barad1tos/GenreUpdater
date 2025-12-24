"""Search strategy detection for alternative API queries.

This module provides detection of album types that require alternative
search strategies when standard API queries return no results.

Different from album_type.py:
- album_type: How to HANDLE year once found (skip, update, mark)
- search_strategy: How to FIND year in first place (modify query)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "SearchStrategy",
    "SearchStrategyInfo",
]


class SearchStrategy(Enum):
    """Search strategy for API queries."""

    NORMAL = "normal"  # No modification needed
    SOUNDTRACK = "soundtrack"  # Extract movie name from album
    VARIOUS_ARTISTS = "various"  # Search by album only
    STRIP_BRACKETS = "strip"  # Remove [SPECIAL TEXT] from album
    GREATEST_HITS = "hits"  # Try artist + "Greatest Hits"


@dataclass(frozen=True, slots=True)
class SearchStrategyInfo:
    """Information about detected search strategy."""

    strategy: SearchStrategy
    detected_pattern: str | None = None
    modified_artist: str | None = None
    modified_album: str | None = None
