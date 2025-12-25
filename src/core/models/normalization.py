"""Unified text normalization for artist/album matching.

This module provides a single source of truth for normalizing artist and album names
when used for matching, comparison, or as dictionary/cache keys.

All code that compares artist or album names for equality should use normalize_for_matching().
"""

from __future__ import annotations


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
