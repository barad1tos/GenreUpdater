"""Album type detection for year update fallback logic.

This module provides pattern-based detection of special album types
(B-Sides, Demo Vault, Greatest Hits, etc.) that require different
handling during year updates.

The detection helps prevent incorrect year assignments when API services
return compilation/reissue years instead of original release years.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    "COMPILATION_PATTERNS",
    "REISSUE_PATTERNS",
    "SPECIAL_ALBUM_PATTERNS",
    "AlbumType",
    "AlbumTypeInfo",
    "YearHandlingStrategy",
    "detect_album_type",
    "get_year_handling_strategy",
    "is_special_album",
]


class AlbumType(Enum):
    """Classification of album types for year handling."""

    NORMAL = "normal"
    SPECIAL = "special"  # B-Sides, Demo, Vault, etc.
    COMPILATION = "compilation"  # Greatest Hits, Best Of, etc.
    REISSUE = "reissue"  # Remastered, Anniversary, Deluxe, etc.


class YearHandlingStrategy(Enum):
    """Strategy for handling year updates based on album type."""

    NORMAL = "normal"  # Apply year normally
    MARK_AND_SKIP = "mark_and_skip"  # Mark for verification, skip update
    MARK_AND_UPDATE = "mark_and_update"  # Mark for verification, still update


@dataclass(frozen=True, slots=True)
class AlbumTypeInfo:
    """Information about detected album type."""

    album_type: AlbumType
    detected_pattern: str | None
    strategy: YearHandlingStrategy


# Patterns that indicate special albums (B-Sides, Demo collections, etc.)
# These albums often have compilation years that differ from original tracks
# NOTE: Use hyphenated forms - matching normalizes hyphens to spaces
SPECIAL_ALBUM_PATTERNS: Final[frozenset[str]] = frozenset({
    "b-sides",
    "b-side",
    "demo",
    "demos",
    "vault",
    "rarities",
    "rarity",
    "archive",
    "archives",
    "outtakes",
    "outtake",
    "unreleased",
    "sessions",
    "session",
    "bonus-tracks",
    "bonus",
    "extras",
    "bootleg",
    "bootlegs",
    "alternate",
    "alternates",
    "acoustic-versions",
    "live-sessions",
})

# Patterns that indicate compilation albums
# These usually have their own release year separate from individual tracks
COMPILATION_PATTERNS: Final[frozenset[str]] = frozenset({
    "greatest hits",
    "best of",
    "collection",
    "anthology",
    "compilation",
    "complete",
    "essential",
    "definitive",
    "ultimate",
    "gold",
    "platinum",
    "hits",
    "singles",
    "collected",
    "retrospective",
})

# Patterns that indicate reissued/remastered albums
# May have reissue year instead of original release year
REISSUE_PATTERNS: Final[frozenset[str]] = frozenset({
    "remaster",
    "remastered",
    "anniversary",
    "deluxe",
    "expanded",
    "special edition",
    "collector",
    "redux",
    "revisited",
    "re-release",
    "re-issue",
    "reissue",
})


def _normalize_for_matching(text: str) -> str:
    """Normalize text for pattern matching.

    Converts to lowercase, removes special characters, and normalizes whitespace.
    """
    # Lowercase
    text = text.lower()
    # Replace hyphens and underscores with spaces
    text = re.sub(r"[-_]", " ", text)
    # Remove parentheses content markers but keep the text
    text = re.sub(r"[()[\]{}]", " ", text)
    # Normalize whitespace
    return " ".join(text.split())


def _find_pattern_match(
    normalized_text: str, patterns: frozenset[str]
) -> str | None:
    """Find first matching pattern in text.

    Args:
        normalized_text: Normalized album name
        patterns: Set of patterns to match against

    Returns:
        Matched pattern string or None
    """
    for pattern in patterns:
        # Normalize the pattern the same way as the text
        # (convert hyphens/underscores to spaces)
        normalized_pattern = re.sub(r"[-_]", " ", pattern)
        # Check if pattern appears as a word boundary match
        # This prevents "demos" matching "demonstrations"
        if re.search(rf"\b{re.escape(normalized_pattern)}\b", normalized_text):
            return pattern  # Return original pattern (not normalized)
    return None


def detect_album_type(album_name: str) -> AlbumTypeInfo:
    """Detect the type of album based on its name.

    Analyzes album name for patterns indicating special handling
    is needed for year updates.

    Args:
        album_name: The album name to analyze

    Returns:
        AlbumTypeInfo with detected type, pattern, and handling strategy

    Examples:
        >>> detect_album_type("Blue Stahli B-Sides")
        AlbumTypeInfo(album_type=AlbumType.SPECIAL, detected_pattern='b-sides', ...)

        >>> detect_album_type("Greatest Hits")
        AlbumTypeInfo(album_type=AlbumType.COMPILATION, detected_pattern='greatest hits', ...)

        >>> detect_album_type("Normal Album")
        AlbumTypeInfo(album_type=AlbumType.NORMAL, detected_pattern=None, ...)
    """
    if not album_name:
        return AlbumTypeInfo(
            album_type=AlbumType.NORMAL,
            detected_pattern=None,
            strategy=YearHandlingStrategy.NORMAL,
        )

    normalized = _normalize_for_matching(album_name)

    # Check special patterns first (highest priority - always skip)
    pattern = _find_pattern_match(normalized, SPECIAL_ALBUM_PATTERNS)
    if pattern:
        return AlbumTypeInfo(
            album_type=AlbumType.SPECIAL,
            detected_pattern=pattern,
            strategy=YearHandlingStrategy.MARK_AND_SKIP,
        )

    # Check compilation patterns (mark and skip)
    pattern = _find_pattern_match(normalized, COMPILATION_PATTERNS)
    if pattern:
        return AlbumTypeInfo(
            album_type=AlbumType.COMPILATION,
            detected_pattern=pattern,
            strategy=YearHandlingStrategy.MARK_AND_SKIP,
        )

    # Check reissue patterns (mark but still update - reissue year is often correct)
    pattern = _find_pattern_match(normalized, REISSUE_PATTERNS)
    if pattern:
        return AlbumTypeInfo(
            album_type=AlbumType.REISSUE,
            detected_pattern=pattern,
            strategy=YearHandlingStrategy.MARK_AND_UPDATE,
        )

    # Normal album
    return AlbumTypeInfo(
        album_type=AlbumType.NORMAL,
        detected_pattern=None,
        strategy=YearHandlingStrategy.NORMAL,
    )


def is_special_album(album_name: str) -> tuple[bool, str | None]:
    """Check if album name indicates a special album type.

    This is a convenience function that returns a simple boolean
    along with the detected pattern.

    Args:
        album_name: The album name to check

    Returns:
        Tuple of (is_special, detected_pattern)
        - is_special: True if album is special, compilation, or reissue
        - detected_pattern: The pattern that matched, or None

    Examples:
        >>> is_special_album("Demo Vault: Wasteland")
        (True, 'vault')

        >>> is_special_album("Regular Album")
        (False, None)
    """
    info = detect_album_type(album_name)
    is_special = info.album_type != AlbumType.NORMAL
    return is_special, info.detected_pattern


def get_year_handling_strategy(album_name: str) -> YearHandlingStrategy:
    """Get the year handling strategy for an album.

    Args:
        album_name: The album name to check

    Returns:
        YearHandlingStrategy enum value indicating how to handle year updates

    Examples:
        >>> get_year_handling_strategy("B-Sides Collection")
        YearHandlingStrategy.MARK_AND_SKIP

        >>> get_year_handling_strategy("Album (Remastered)")
        YearHandlingStrategy.MARK_AND_UPDATE

        >>> get_year_handling_strategy("Normal Album")
        YearHandlingStrategy.NORMAL
    """
    info = detect_album_type(album_name)
    return info.strategy
