"""Unified text normalization for artist/album matching.

This module provides a single source of truth for normalizing artist and album names
when used for matching, comparison, or as dictionary/cache keys.

All code that compares artist or album names for equality should use normalize_for_matching().
"""

from __future__ import annotations

import re


def normalize_for_matching(text: str) -> str:
    """Normalize text for case-insensitive matching and cache keys.

    This is THE standard normalization for all artist/album comparisons.
    Use this everywhere you need to:
    - Compare artist/album names for equality
    - Generate cache keys
    - Group tracks by artist
    - Look up values in mapping dictionaries

    Args:
        text: Text to normalize (artist name, album name, etc.)

    Returns:
        Normalized text: stripped whitespace, lowercased

    Examples:
        >>> normalize_for_matching("  Vildhjarta  ")
        'vildhjarta'
        >>> normalize_for_matching("2CELLOS")
        '2cellos'
        >>> normalize_for_matching("AC/DC")
        'ac/dc'
    """
    return text.strip().lower() if text else ""


def are_names_equal(name1: str, name2: str) -> bool:
    """Check if two names are equivalent after normalization.

    Convenience function for comparing artist/album names.

    Args:
        name1: First name to compare
        name2: Second name to compare

    Returns:
        True if names are equivalent after normalization
    """
    return normalize_for_matching(name1) == normalize_for_matching(name2)


def normalize_search_name(name: str) -> str:
    """Normalize artist/album name for API queries.

    Performs substitutions that improve API matching:
    - & → and (Karma & Effect → Karma and Effect)
    - w/ → with (Split w/ Band → Split with Band)
    - Strips trailing compilation markers (Album + 4 → Album)
    - Normalizes whitespace

    Note: This is for API QUERIES, not for scoring/matching; scoring uses ReleaseScorer._normalize_name.
    """
    if not name:
        return name

    result = name

    # Common substitutions for better API matching
    substitutions = {
        " & ": " and ",
        "&": " and ",  # Handle no-space cases like "Fire&Water"
        " w/ ": " with ",
        " w/": " with ",
        " = ": " ",  # Liberation = Termination → Liberation Termination
        ":": " ",  # Issue #103: Colons break Lucene search (III:Trauma → III Trauma)
    }

    for old, new in substitutions.items():
        result = result.replace(old, new)

    # Strip trailing compilation markers: "+ 4", "+ 10" (number = # bonus tracks)
    # Pattern: " + " followed by digit(s) at end of string
    # More conservative than ".*" to preserve legitimate titles like "Album + Bonus Tracks"
    result = re.sub(r"\s*+\+\s++\d.*$", "", result)

    # Strip content after " / " (split albums - keep first part only)
    # "Robot Hive / Exodus" → "Robot Hive"
    # "House By the Cemetery / Mortal Massacre" → "House By the Cemetery"
    if " / " in result:
        result = result.split(" / ", maxsplit=1)[0].strip()

    # Normalize whitespace (multiple spaces to single)
    return re.sub(r"\s+", " ", result).strip()


def search_names(artist: str, album: str) -> tuple[str, str]:
    """Return the artist and album names the year search asks the providers for, and caches their answers under.

    Quotes and parenthetical content such as "(Deluxe Edition)" or "(Remastered)" are dropped from the album, since
    they are edition metadata rather than part of the title; both names then go through normalize_search_name.

    Args:
        artist: Artist name as the library has it
        album: Album name as the library has it

    Returns:
        Tuple of (artist, album) as the search uses them
    """
    album_clean = album.replace('"', "").replace("'", "")
    album_clean = re.sub(r"\s*+\([^)]*+\)", "", album_clean).strip()
    return normalize_search_name(artist), normalize_search_name(album_clean)
