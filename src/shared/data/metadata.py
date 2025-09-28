"""Metadata Helpers Module.

Provides utility functions for parsing, cleaning, and processing music track metadata.
These functions are designed to be independent of specific service instances
and can be used across different parts of the application.

Functions:
    - parse_tracks: Parses raw AppleScript output into structured track dictionaries.
    - group_tracks_by_artist: Groups track dictionaries by artist name.
    - determine_dominant_genre_for_artist: Determines the most likely genre for an artist.
    - remove_parentheses_with_keywords: Removes specified parenthetical content from strings.
    - clean_names: Applies cleaning rules to track and album names.
    - is_music_app_running: Checks if Music.app is currently running.
"""

import logging
import re
import subprocess  # trunk-ignore(bandit/B404)
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from enum import IntEnum, auto
from pathlib import Path
from typing import Any

from src.shared.data.models import TrackDict


class TrackField(IntEnum):
    """Enumeration of track data field indices with auto-incrementing values."""

    ID = 0
    NAME = auto()
    ARTIST = auto()
    ALBUM_ARTIST = auto()  # Album artist field from AppleScript
    ALBUM = auto()
    GENRE = auto()
    DATE_ADDED = auto()
    TRACK_STATUS = auto()
    OLD_YEAR = auto()
    RELEASE_YEAR = auto()  # Year from release date field in Music.app
    NEW_YEAR = auto()


# Minimum required fields based on AppleScript output (up to DATE_ADDED)
MIN_REQUIRED_FIELDS = TrackField.DATE_ADDED + 1


def _extract_optional_field(fields: list[str], field_index: int) -> str:
    """Extract optional field value with bounds checking.

    Args:
        fields: List of field strings
        field_index: Index of the field to extract

    Returns:
        Stripped field value or empty string if index out of bounds

    """
    return fields[field_index].strip() if len(fields) > field_index else ""


def _validate_year_field(raw_year: str) -> str:
    """Validate year field ensuring it's a valid non-zero digit string.

    Args:
        raw_year: Raw year string to validate

    Returns:
        Validated year string or empty string if invalid

    """
    return raw_year if (raw_year.isdigit() and int(raw_year) != 0) else ""


def _create_track_from_fields(fields: list[str]) -> TrackDict:
    """Create a TrackDict instance from parsed fields.

    Args:
        fields: List of field strings in TrackField order

    Returns:
        TrackDict instance created from the fields

    """
    # Extract and validate year fields
    raw_old_year = _extract_optional_field(fields, TrackField.OLD_YEAR)
    raw_release_year = _extract_optional_field(fields, TrackField.RELEASE_YEAR)
    raw_new_year = _extract_optional_field(fields, TrackField.NEW_YEAR)

    old_year = _validate_year_field(raw_old_year)
    release_year = _validate_year_field(raw_release_year)
    new_year = _validate_year_field(raw_new_year)

    # Extract optional track_status field if available
    track_status = _extract_optional_field(fields, TrackField.TRACK_STATUS)

    # Build base TrackDict
    track = TrackDict(
        id=fields[TrackField.ID].strip(),
        name=fields[TrackField.NAME].strip(),
        artist=fields[TrackField.ARTIST].strip(),
        album=fields[TrackField.ALBUM].strip(),
        genre=fields[TrackField.GENRE].strip(),
        date_added=fields[TrackField.DATE_ADDED].strip(),
        track_status=track_status or None,
        old_year=old_year,
        release_year=release_year,
        new_year=new_year,
        year=old_year,  # Add current year field for year_retriever to access
    )

    if album_artist_value := _extract_optional_field(
        fields, TrackField.ALBUM_ARTIST
    ):
        track.__dict__["album_artist"] = album_artist_value

    return track


def parse_tracks(raw_data: str, error_logger: logging.Logger) -> list[TrackDict]:
    """Parse the raw data from AppleScript into a list of track dictionaries.

    Uses the Record Separator (U+001E) as the field delimiter and
    Group Separator (U+001D) as the line delimiter.

    :param raw_data: Raw string data from AppleScript.
    :param error_logger: Logger for error output.
    :return: List of track dictionaries.
    """
    field_separator = "\x1e"  # ASCII 30 (Record Separator)
    line_separator = "\x1d"  # ASCII 29 (Group Separator)

    if not raw_data:
        error_logger.error("No data fetched from AppleScript.")
        return []

    error_logger.debug(
        f"parse_tracks: Input raw_data (first 500 chars): {raw_data[:500]}...",
    )

    tracks: list[TrackDict] = []
    rows = raw_data.strip().split(line_separator)

    for row in rows:
        if not row:  # Skip empty rows
            continue

        fields = row.split(field_separator)

        if len(fields) >= MIN_REQUIRED_FIELDS:
            track = _create_track_from_fields(fields)
            tracks.append(track)
        else:
            error_logger.warning("Malformed track data row skipped: %s", row)

    return tracks


def group_tracks_by_artist(
    tracks: list[TrackDict],
) -> dict[str, list[TrackDict]]:
    """Group tracks by artist name into a dictionary for efficient processing.

    Uses standard Python collections, no external dependencies or logging needed internally.

    :param tracks: List of track dictionaries.
    :return: Dictionary mapping artist names to lists of their tracks.
    """
    # Use defaultdict for efficient grouping without checking for key existence
    artists: defaultdict[str, list[TrackDict]] = defaultdict(list)
    for track in tracks:
        # Ensure artist key exists, default to "Unknown" if missing
        # Use dict.get() for dictionary access, not getattr() which is for object attributes
        artist = track.get("artist", "Unknown")
        if artist and isinstance(artist, str):
            artists[artist].append(track)
    # Return defaultdict directly without converting to dict for better performance
    return artists


def determine_dominant_genre_for_artist(
    artist_tracks: Sequence[TrackDict],
    error_logger: logging.Logger,
) -> str:
    """Determine the dominant genre for an artist based on the earliest genre of their tracks.

    Does not require injected services, but logs errors.

    Algorithm:
        1. Find the earliest track for each album (by dateAdded).
        2. Determines the earliest album (by dateAdded of its earliest track).
        3. Uses the genre of the earliest track in that album as the dominant genre.

    :param artist_tracks: List of track dictionaries for a single artist.
    :param error_logger: Logger for error output.
    :return: The dominant genre string, or "Unknown" on error or if no tracks.
    """
    if not artist_tracks:
        return "Unknown"
    try:
        # Find the earliest track for each album
        album_earliest = _find_earliest_track_per_album(artist_tracks, error_logger)

        # From these tracks, find the earliest album (by the date of addition of its earliest track)
        # Ensure album_earliest is not empty before finding min
        if not album_earliest:
            return "Unknown"

        # Find the earliest track, ensuring date_added is valid string
        earliest_album_track = None
        earliest_date = None

        for track in album_earliest.values():
            date_str = track.get("date_added", "9999-12-31 00:00:00")
            if isinstance(date_str, str):
                try:
                    track_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    if earliest_date is None or track_date < earliest_date:
                        earliest_date = track_date
                        earliest_album_track = track
                except ValueError as e:
                    error_logger.warning("Invalid date_added format '%s': %s", date_str, e)
                    continue

        if not earliest_album_track:
            return "Unknown"

        dominant_genre = earliest_album_track.get("genre")

        # Ensure return type is always string
        return dominant_genre if isinstance(dominant_genre, str) else "Unknown"
    except (KeyError, TypeError, AttributeError) as e:  # Catch data structure errors
        error_logger.exception(
            "Error in determine_dominant_genre_for_artist: %s",
            e,
        )
        return "Unknown"


def _parse_track_date(date_str: str, error_logger: logging.Logger) -> datetime | None:
    """Parse track date string safely.

    Args:
        date_str: Date string to parse
        error_logger: Logger for error output

    Returns:
        Parsed datetime or None if invalid

    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        error_logger.warning("Invalid date format '%s': %s", date_str, e)
        return None


def _is_track_earlier(track: TrackDict, existing_track: TrackDict, error_logger: logging.Logger) -> bool:
    """Check if track is earlier than existing track.

    Args:
        track: Track to check
        existing_track: Current earliest track
        error_logger: Logger for error output

    Returns:
        True if the track is earlier

    """
    track_date_str = track.get("date_added", "9999-12-31 00:00:00")
    existing_date_str = existing_track.get("date_added", "9999-12-31 00:00:00")

    if not isinstance(track_date_str, str) or not isinstance(existing_date_str, str):
        return False

    track_date = _parse_track_date(track_date_str, error_logger)
    existing_date = _parse_track_date(existing_date_str, error_logger)

    return track_date is not None and existing_date is not None and track_date < existing_date


def _find_earliest_track_per_album(
    artist_tracks: Sequence[TrackDict],
    error_logger: logging.Logger,
) -> dict[str, TrackDict]:
    """Find the earliest track for each album in a list of tracks.

    Arguments:
        artist_tracks: List of track dictionaries for a single artist.
        error_logger: Logger for error output.

    Returns:
        Dictionary mapping album names to their earliest track.

    """
    album_earliest: dict[str, TrackDict] = {}

    for track in artist_tracks:
        album = track.get("album", "")
        date_str = track.get("date_added", "9999-12-31 00:00:00")

        # Validate album and date
        if not album or not isinstance(album, str) or not isinstance(date_str, str):
            continue

        # Validate date format
        if _parse_track_date(date_str, error_logger) is None:
            continue

        # Update the earliest track for album
        if album not in album_earliest or _is_track_earlier(track, album_earliest[album], error_logger):
            album_earliest[album] = track

    return album_earliest


def _find_matching_parenthesis(text: str, start_pos: int) -> int:
    """Find the matching closing parenthesis for the opening at start_pos.

    Args:
        text: The text to search in
        start_pos: Position of the opening parenthesis

    Returns:
        Position of matching closing parenthesis or -1 if not found

    """
    count = 1
    position = start_pos + 1
    while position < len(text) and count > 0:
        if text[position] == "(":
            count += 1
        elif text[position] == ")":
            count -= 1
        position += 1
    return position - 1 if count == 0 else -1


def _text_contains_keywords(text: str, keywords: list[str]) -> bool:
    """Check if text contains any of the keywords (case-insensitive).

    Args:
        text: Text to search in
        keywords: List of keywords to search for

    Returns:
        True if any keyword is found, False otherwise

    """
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in keywords)


def _remove_parentheses_segments(text: str, keywords: list[str]) -> str:
    """Remove parentheses segments that contain keywords.

    Args:
        text: Text to process
        keywords: Keywords to search for in parentheses

    Returns:
        Text with matching parentheses segments removed

    """
    cleaned = text
    position = 0

    while position < len(cleaned):
        if cleaned[position] == "(":
            end_pos = _find_matching_parenthesis(cleaned, position)
            if end_pos != -1:
                content = cleaned[position : end_pos + 1]
                if _text_contains_keywords(content, keywords):
                    cleaned = cleaned[:position] + cleaned[end_pos + 1 :]
                    continue  # Don't increment position, recheck from the same position
        position += 1

    return cleaned


def _remove_bracket_segments(text: str, keywords: list[str]) -> str:
    """Remove bracket segments that contain keywords.

    Args:
        text: Text to process
        keywords: Keywords to search for in brackets

    Returns:
        Text with matching bracket segments removed

    """
    cleaned = text
    position = 0

    while position < len(cleaned):
        if cleaned[position] == "[":
            end_pos = cleaned.find("]", position)
            if end_pos != -1:
                content = cleaned[position : end_pos + 1]
                if _text_contains_keywords(content, keywords):
                    cleaned = cleaned[:position] + cleaned[end_pos + 1 :]
                    continue  # Don't increment position, recheck from the same position
        position += 1

    return cleaned


def remove_parentheses_with_keywords(
    name: str,
    keywords: list[str],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> str:
    """Remove any parenthetical segment that contains at least one of the given keywords.

    This implementation uses balanced parentheses parsing to properly handle nested
    parentheses/brackets (e.g., "Album (Reissue (2024))" -> "Album"). It scans for
    opening brackets, finds the matching closing bracket, and removes the entire
    segment if it contains any of the keywords (case-insensitive).

    Args:
        name: The string to process
        keywords: List of keywords to search for in parenthetical segments
        console_logger: Logger for debug output
        error_logger: Logger for error output

    Returns:
        The cleaned string with matching parenthetical segments removed

    """
    if not name or not keywords:
        return name

    try:
        return _clean_text_segments(name, keywords, console_logger)
    except (ValueError, TypeError, AttributeError) as e:
        error_logger.exception(
            "Error processing '%s' with keywords %s: %s",
            name,
            keywords,
            e,
        )
        return name  # Return original on error


def _clean_text_segments(name: str, keywords: list[str], console_logger: logging.Logger) -> str:
    cleaned = name

    # Remove parentheses segments containing keywords
    cleaned = _remove_parentheses_segments(cleaned, keywords)

    # Remove bracket segments containing keywords
    cleaned = _remove_bracket_segments(cleaned, keywords)

    # Collapse excess whitespace that may be left after removals
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    console_logger.debug("remove_parentheses_with_keywords: '%s' -> '%s'", name, cleaned)
    return cleaned


def clean_names(
    artist: str,
    track_name: str,
    album_name: str,
    *,
    config: dict[str, Any],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> tuple[str, str]:
    """Clean the track name and album name based on the configuration settings.

    Requires config and loggers.

    :param artist: Artist name.
    :param track_name: Current track name.
    :param album_name: Current album name.
    :param config: Application configuration dictionary.
    :param console_logger: Logger for console output.
    :param error_logger: Logger for error output.
    :return: Tuple containing the cleaned track name and cleaned album name.
    """
    # Only log at debug level to reduce noise, especially for empty values
    if track_name or album_name:
        console_logger.debug(
            "clean_names called with: artist='%s', track_name='%s', album_name='%s'",
            artist,
            track_name,
            album_name,
        )
    # Skip logging entirely for empty track and album names

    # Use config passed as argument
    exceptions = config.get("exceptions", {}).get("track_cleaning", [])
    # Check if the current artist/album pair is in the exceptions list
    is_exception = any(exc.get("artist", "").lower() == artist.lower() and exc.get("album", "").lower() == album_name.lower() for exc in exceptions)

    if is_exception:
        console_logger.info(
            "No cleaning applied due to exceptions for artist '%s', album '%s'.",
            artist,
            album_name,
        )
        return track_name.strip(), album_name.strip()  # Return original names, stripped

    # Get cleaning config
    cleaning_config = config.get("cleaning", {})
    remaster_keywords = cleaning_config.get(
        "remaster_keywords",
        ["remaster", "remastered"],
    )
    album_suffixes_raw: list[str] = cleaning_config.get("album_suffixes_to_remove", [])
    # Sort suffixes by length in descending order to remove longer patterns first
    album_suffixes: list[str] = sorted(album_suffixes_raw, key=len, reverse=True)

    # Helper function for cleaning strings using remove_parentheses_with_keywords
    def clean_string(val: str, keywords: list[str]) -> str:
        """Clean a string by removing parenthetical segments containing keywords.

        Args:
            val: The string to clean
            keywords: List of keywords to search for in parenthetical segments

        Returns:
            The cleaned string with matching parenthetical segments removed

        """
        # Use the utility function defined above, passing loggers
        new_val = remove_parentheses_with_keywords(
            val,
            keywords,
            console_logger,
            error_logger,
        )
        # Clean up multiple spaces and strip whitespace
        new_val = re.sub(r"\s+", " ", new_val).strip()
        return new_val or ""  # Return an empty string if the result is empty after cleaning

    original_track = track_name
    original_album = album_name

    # Apply cleaning to track name and album name
    cleaned_track = clean_string(track_name, remaster_keywords)
    cleaned_album = clean_string(album_name, remaster_keywords)

    # Ensure cleaned_album is always a string (type hint for linters)
    cleaned_album = str(cleaned_album)

    # Remove specified album suffixes in a case-insensitive manner
    # and ensure leftover punctuation/whitespace is stripped
    removed = True
    while removed:
        removed = False
        for suffix in album_suffixes:
            if cleaned_album.lower().endswith(suffix.lower()):
                before = cleaned_album
                cleaned_album = cleaned_album[: -len(suffix)]
                # Strip trailing spaces, tabs, and dashes (safe approach)
                cleaned_album = cleaned_album.rstrip(" \t-\u2013\u2014")
                console_logger.debug(
                    "Removed suffix '%s' from album '%s'; result '%s'",
                    suffix,
                    before,
                    cleaned_album,
                )
                removed = True
                break

    # Log the cleaning results only if something changed
    if cleaned_track != original_track:
        console_logger.debug(
            "Cleaned track name: '%s' -> '%s'",
            original_track,
            cleaned_track,
        )
    if cleaned_album != original_album:
        console_logger.debug(
            "Cleaned album name: '%s' -> '%s'",
            original_album,
            cleaned_album,
        )

    return cleaned_track, cleaned_album


# noinspection PyArgumentEqualDefault
def _check_osascript_availability(error_logger: logging.Logger) -> bool:
    """Check if osascript is available and execute Music app status check.

    Args:
        error_logger: Logger for error output

    Returns:
        True if Music app is running, False otherwise

    """
    # SECURITY: Using hardcoded safe command with explicit validation,
    # No user input is passed to subprocess, preventing injection attacks
    osascript_path = "/usr/bin/osascript"

    # SECURITY: Verify osascript exists before execution
    if not Path(osascript_path).is_file():
        error_logger.error("osascript not found at expected path: %s", osascript_path)
        return False

    # SECURITY: Hardcoded safe AppleScript command with no variables
    apple_script = 'tell application "System Events" to (name of processes) contains "Music"'

    # SECURITY: Safe subprocess call with explicit parameters
    result = subprocess.run(  # noqa: S603
        [osascript_path, "-e", apple_script],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        shell=False,
    )
    # Log stderr from osascript if any
    if result.stderr:
        error_logger.warning(
            "AppleScript stderr during Music.app status check: %s",
            result.stderr.strip(),
        )

    return result.stdout.strip().lower() == "true"


def is_music_app_running(error_logger: logging.Logger) -> bool:
    """Check if the Music.app is currently running using subprocess.

    Args:
        error_logger: Logger for error output.

    Returns:
        True if Music.app is running, False otherwise.

    """
    try:
        return _check_osascript_availability(error_logger)
    except subprocess.TimeoutExpired:
        error_logger.warning("Music.app status check timed out after 10 seconds. Assuming Music.app is available.")
        return True  # Assume running on timeout to avoid blocking execution
    except (subprocess.SubprocessError, OSError) as e:
        error_logger.exception("Unable to check Music.app status: %s", e)
        return False  # Assume not running on error
    except (ValueError, KeyError, AttributeError) as e:  # Catch data processing errors
        error_logger.exception(
            "Unexpected error checking Music.app status: %s",
            e,
        )
        return False
