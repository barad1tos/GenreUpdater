"""Search strategy detection for alternative API queries.

This module provides detection of album types that require alternative
search strategies when standard API queries return no results.

Different from album_type.py:
- album_type: How to HANDLE year once found (skip, update, mark)
- search_strategy: How to FIND year in first place (modify query)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from core.models.track_models import AppConfig

__all__ = [
    "SearchStrategy",
    "SearchStrategyInfo",
    "detect_search_strategy",
]


class SearchStrategy(Enum):
    """Search strategy for API queries."""

    NORMAL = "normal"  # No modification needed
    SOUNDTRACK = "soundtrack"  # Extract movie name from album
    VARIOUS_ARTISTS = "various"  # Search by album only
    STRIP_BRACKETS = "strip"  # Remove [SPECIAL TEXT] from album


@dataclass(frozen=True, slots=True)
class SearchStrategyInfo:
    """Information about detected search strategy."""

    strategy: SearchStrategy
    detected_pattern: str | None = None
    modified_artist: str | None = None
    modified_album: str | None = None


# Default patterns
_DEFAULT_SOUNDTRACK_PATTERNS: Final[frozenset[str]] = frozenset(
    {
        "soundtrack",
        "original score",
        "OST",
        "motion picture",
        "film score",
    }
)

_DEFAULT_VARIOUS_ARTISTS: Final[frozenset[str]] = frozenset(
    {
        "Various Artists",
        "Various",
        "VA",
        "Різні виконавці",
    }
)

# Threshold for unusual bracket content detection
# Short tags like [CD1], [v2], [Disc 2] are normal (≤10 chars)
# Long content like [MESSAGE FROM THE CLERGY] is unusual (>10 chars)
_UNUSUAL_BRACKET_MIN_LENGTH: Final[int] = 10


def _get_patterns(config: AppConfig | dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    """Get soundtrack and various artists patterns from config or defaults."""
    if isinstance(config, AppConfig):
        detection = config.album_type_detection
        soundtrack_list = detection.soundtrack_patterns or list(_DEFAULT_SOUNDTRACK_PATTERNS)
        various_list = detection.various_artists_names or list(_DEFAULT_VARIOUS_ARTISTS)
    else:
        album_config = config.get("album_type_detection", {})
        soundtrack_list = album_config.get("soundtrack_patterns", list(_DEFAULT_SOUNDTRACK_PATTERNS))
        various_list = album_config.get("various_artists_names", list(_DEFAULT_VARIOUS_ARTISTS))
    return frozenset(soundtrack_list), frozenset(various_list)


def _is_soundtrack(album: str, patterns: frozenset[str]) -> str | None:
    """Check if album matches soundtrack patterns. Returns matched pattern.

    Uses simple substring matching (not word-boundary regex) because:
    - Soundtrack patterns are distinctive enough to avoid false positives
    - Simpler matching handles variations like "original-score" naturally
    """
    album_lower = album.lower()
    return next(
        (pattern for pattern in patterns if pattern.lower() in album_lower),
        None,
    )


def _is_various_artists(artist: str, patterns: frozenset[str]) -> bool:
    """Check if artist is Various Artists."""
    artist_lower = artist.lower().strip()
    return any(p.lower() == artist_lower for p in patterns)


def _has_unusual_brackets(album: str) -> tuple[bool, str | None]:
    """Check for unusual bracket content like [MESSAGE FROM THE CLERGY]."""
    bracket_match = re.search(r"\[([^]]+)]", album)
    if not bracket_match:
        return False, None

    content = bracket_match[1]
    normal_patterns = {"deluxe", "remaster", "bonus", "disc", "cd", "version"}
    if content.lower() in normal_patterns or any(p in content.lower() for p in normal_patterns):
        return False, None

    if len(content) > _UNUSUAL_BRACKET_MIN_LENGTH or content.isupper():
        stripped = re.sub(r"\s*\[[^]]+]\s*", "", album).strip()
        return True, stripped

    return False, None


def detect_search_strategy(
    artist: str,
    album: str,
    config: AppConfig | dict[str, Any],
) -> SearchStrategyInfo:
    """Detect which search strategy to use for API queries.

    Detection order (first match wins):
    1. Soundtrack patterns in album
    2. Various Artists as artist
    3. Unusual bracket content
    4. Default: NORMAL
    """
    if not album:
        return SearchStrategyInfo(strategy=SearchStrategy.NORMAL)

    soundtrack_patterns, various_patterns = _get_patterns(config)

    # 1. Check for soundtrack
    if pattern := _is_soundtrack(album, soundtrack_patterns):
        album_lower = album.lower()
        idx = album_lower.find(pattern.lower())
        if idx > 0 and (movie_name := album[:idx].strip().rstrip("([-\u2013\u2014")):
            return SearchStrategyInfo(
                strategy=SearchStrategy.SOUNDTRACK,
                detected_pattern=pattern,
                modified_artist=movie_name,
                modified_album=movie_name,
            )
        return SearchStrategyInfo(
            strategy=SearchStrategy.SOUNDTRACK,
            detected_pattern=pattern,
        )

    # 2. Check for Various Artists
    if _is_various_artists(artist, various_patterns):
        return SearchStrategyInfo(strategy=SearchStrategy.VARIOUS_ARTISTS, detected_pattern=artist, modified_album=album)

    # 3. Check for unusual brackets
    has_unusual, stripped = _has_unusual_brackets(album)
    if has_unusual and stripped:
        return SearchStrategyInfo(
            strategy=SearchStrategy.STRIP_BRACKETS,
            detected_pattern="brackets",
            modified_artist=artist,
            modified_album=stripped,
        )

    return SearchStrategyInfo(strategy=SearchStrategy.NORMAL)
