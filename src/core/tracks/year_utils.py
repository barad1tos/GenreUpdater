"""Year-related utility functions.

Pure functions without side effects for year processing.
These utilities are used across the year retrieval subsystem.
"""

from typing import Any


def resolve_non_negative_int(value: Any, default: int) -> int:
    """Convert arbitrary value to non-negative int with fallback.

    Args:
        value: Value to convert (may be int, str, float, None, etc.)
        default: Default value if conversion fails or result is negative

    Returns:
        Non-negative integer, or default if invalid

    Examples:
        >>> resolve_non_negative_int(5, 0)
        5
        >>> resolve_non_negative_int("10", 0)
        10
        >>> resolve_non_negative_int(-3, 0)
        0
        >>> resolve_non_negative_int(None, 7)
        7

    """
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    return candidate if candidate >= 0 else default


def resolve_positive_int(value: Any, default: int) -> int:
    """Convert arbitrary value to strictly positive int with fallback.

    Args:
        value: Value to convert
        default: Default value if conversion fails or result is not positive

    Returns:
        Positive integer (> 0), or default if invalid

    Examples:
        >>> resolve_positive_int(5, 1)
        5
        >>> resolve_positive_int(0, 1)
        1
        >>> resolve_positive_int(-3, 1)
        1

    """
    result = resolve_non_negative_int(value, default)
    return result if result > 0 else default


def resolve_non_negative_float(value: Any, default: float) -> float:
    """Convert arbitrary value to non-negative float with fallback.

    Args:
        value: Value to convert
        default: Default value if conversion fails or result is negative

    Returns:
        Non-negative float, or default if invalid

    Examples:
        >>> resolve_non_negative_float(3.14, 0.0)
        3.14
        >>> resolve_non_negative_float("2.5", 0.0)
        2.5
        >>> resolve_non_negative_float(-1.0, 0.0)
        0.0

    """
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return default
    return candidate if candidate >= 0 else default


def normalize_collaboration_artist(artist: str) -> str:
    """Normalize collaboration artists to main artist.

    For collaborations like "Main Artist & Other" or "Main Artist feat. Other",
    extract the main artist to group all tracks together.

    Args:
        artist: Artist name potentially containing collaborations

    Returns:
        Main artist name for grouping

    Examples:
        >>> normalize_collaboration_artist("Drake feat. Rihanna")
        'Drake'
        >>> normalize_collaboration_artist("Daft Punk & Pharrell")
        'Daft Punk'
        >>> normalize_collaboration_artist("Solo Artist")
        'Solo Artist'

    """
    # Common collaboration separators
    separators = [
        " & ",
        " feat. ",
        " feat ",
        " ft. ",
        " ft ",
        " vs. ",
        " vs ",
        " with ",
        " and ",
        " x ",
        " X ",
    ]

    return next(
        (artist.split(separator, maxsplit=1)[0].strip() for separator in separators if separator in artist),
        artist,
    )
