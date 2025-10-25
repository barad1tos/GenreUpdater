#!/usr/bin/env python3

"""Reports Module.

Handles CSV/HTML report generation and track data management for music library operations.
Provides both file operations and formatted console output with support for caching.

Main features:
- Track list management with persistent storage and synchronization
- Consolidated reporting for genres, years, and track/album naming
- Interactive console output with color formatting
- CSV data handling with validation
- Unified interface for actual and simulated operations
- HTML analytics with performance metrics

Key functions:
- save_to_csv: Save track metadata to CSV files
- save_unified_changes_report: Generate formatted change reports
- load_track_list: Load and validate track data
- sync_track_list_with_current: Sync data between runs
- save_unified_dry_run: Create reports for simulations
- save_html_report: Generate HTML analytics

Note: Uses CacheServiceProtocol for album year caching.
"""

import asyncio
import csv
import logging
import subprocess
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from src.shared.core.logger import ensure_directory, get_full_log_path
from src.shared.data.models import ChangeLogEntry
from src.shared.data.types import AppleScriptClientProtocol, CacheServiceProtocol, TrackDict


class Color:
    """ANSI color codes for console output."""

    RED = "\033[31m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RESET = "\033[0m"


class ChangeType:
    """Enumeration of change types."""

    GENRE = "genre"
    YEAR = "year"
    NAME = "name"
    OTHER = "other"


class Key:
    """Enumeration of key names for CSV fields."""

    CHANGE_TYPE = "change_type"
    ARTIST = "artist"
    ALBUM = "album"
    TRACK_NAME = "track_name"
    OLD_GENRE = "old_genre"
    NEW_GENRE = "new_genre"
    OLD_YEAR = "old_year"
    NEW_YEAR = "new_year"
    OLD_TRACK_NAME = "old_track_name"
    NEW_TRACK_NAME = "new_track_name"
    OLD_ALBUM_NAME = "old_album_name"
    NEW_ALBUM_NAME = "new_album_name"
    TIMESTAMP = "timestamp"


class Format:
    """Enumeration of formatting constants."""

    COL_WIDTH_30 = 30
    COL_WIDTH_40 = 40
    COL_WIDTH_38 = 38
    COL_WIDTH_10 = 10
    SEPARATOR_80 = 80
    SEPARATOR_100 = 100
    TRUNCATE_SUFFIX = ".."
    ARROW = "→"
    HEADER_OLD_NEW = "Old → New"
    HEADER_ITEM_TYPE = "Item Type"
    HEADER_ITEM_NAME = "Item Name"
    ITEM_TYPE_TRACK = "Track"
    ITEM_TYPE_ALBUM = "Album"
    ITEM_TYPE_OTHER = "Other"


class Misc:
    """Enumeration of miscellaneous constants."""

    CHANGES_REPORT_TYPE = "changes report"
    EMOJI_REPORT = "📋"
    EMOJI_CHANGE = "🔄"
    UNKNOWN = "Unknown"
    UNKNOWN_ARTIST = "Unknown Artist"
    UNKNOWN_ALBUM = "Unknown Album"
    UNKNOWN_TRACK = "Unknown Track"
    DURATION_FIELD = "Duration (s)"


def _save_csv(
    data: Sequence[dict[str, str]],
    fieldnames: Sequence[str],
    file_path: str,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    data_type: str,
) -> None:
    """Save the provided data to a CSV file.

    Checks if the target directory for the CSV file exists, and creates it if not.
    Uses atomic write pattern with a temporary file.

    :param data: List of dictionaries to save to the CSV file.
    :param fieldnames: List of field names for the CSV file.
    :param file_path: Path to the CSV file.
    :param console_logger: Logger for console output.
    :param error_logger: Logger for error output.
    :param data_type: Type of data being saved (e.g., "tracks", "changes report").
    """
    ensure_directory(str(Path(file_path).parent))
    console_logger.info("Saving %s to CSV: %s", data_type, file_path)

    temp_file_path = f"{file_path}.tmp"

    try:
        with Path(temp_file_path).open(mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            # Filter each row to include only keys present in fieldnames
            for row in data:
                filtered_row = {field: row.get(field, "") for field in fieldnames}
                writer.writerow(filtered_row)

        # Atomic rename
        # On Windows, os.replace provides atomic replacement if the destination exists
        # On POSIX, os.rename is atomic
        Path(temp_file_path).replace(Path(file_path))

        console_logger.info(
            "%s saved to %s (%d entries).",
            data_type.capitalize(),
            file_path,
            len(data),
        )
    except (OSError, UnicodeError):
        error_logger.exception("Failed to save %s", data_type)
        # Clean up temporary file in case of error
        if Path(temp_file_path).exists():
            try:
                Path(temp_file_path).unlink()
            except OSError as cleanup_e:
                error_logger.warning(
                    "Failed to remove temporary file %s: %s",
                    temp_file_path,
                    cleanup_e,
                )


def save_to_csv(
    tracks: Sequence[TrackDict],
    file_path: str,
    console_logger: logging.Logger | None = None,
    error_logger: logging.Logger | None = None,
) -> None:
    """Save the list of track dictionaries to a CSV file."""
    if console_logger is None:
        console_logger = logging.getLogger("console_logger")
    if error_logger is None:
        error_logger = logging.getLogger("error_logger")

    # Updated fieldnames to ensure we capture all necessary fields
    # These fields should match the structure expected by load_track_list
    fieldnames = [
        "id",
        "name",
        "artist",
        "album",
        "genre",
        "date_added",
        "last_modified",
        "track_status",
        "old_year",
        "new_year",
    ]
    # Convert TrackDict models to plain dictionaries with string values
    track_dicts = [{field: str(track.get(field) or "") for field in fieldnames} for track in tracks]
    _save_csv(
        track_dicts,
        fieldnames,
        file_path,
        console_logger,
        error_logger,
        "tracks",
    )


# Mapping of change types to headers {Displayed Header: dict_key}
_HEADERS_BY_TYPE: dict[str, dict[str, str]] = {
    "genre": {
        "Artist": Key.ARTIST,
        "Album": Key.ALBUM,
        "Track": Key.TRACK_NAME,
        "Old Genre": Key.OLD_GENRE,
        "New Genre": Key.NEW_GENRE,
    },
    "year": {
        "Artist": Key.ARTIST,
        "Album": Key.ALBUM,
        "Old Year": Key.OLD_YEAR,
        "New Year": Key.NEW_YEAR,
    },
    "cleaning": {
        "Artist": Key.ARTIST,
        "Original Album": Key.OLD_ALBUM_NAME,
        "Cleaned Album": Key.NEW_ALBUM_NAME,
        "Original Track": Key.OLD_TRACK_NAME,
        "Cleaned Track": Key.NEW_TRACK_NAME,
    },
    "name": {
        # Dynamic headers - see _build_dynamic_headers()
    },
}


def _build_headers(change_type: str) -> dict[str, str] | None:
    """Return header mapping for a given change type substring."""
    return next(
        (mapping for key, mapping in _HEADERS_BY_TYPE.items() if key in change_type),
        None,
    )


def _build_dynamic_headers(change_type: str, records: list[dict[str, str]]) -> dict[str, str]:
    """Build dynamic headers for name changes based on what fields are actually present."""
    if change_type != "name" or not records:
        # For non-name changes, use static headers
        static_headers = _build_headers(change_type)
        return static_headers or {}

    # For name changes, dynamically determine what changed
    headers = {"Artist": Key.ARTIST}

    # Check if we have album name changes
    has_album_changes = any(rec.get(Key.OLD_ALBUM_NAME) and rec.get(Key.NEW_ALBUM_NAME) for rec in records)

    # Check if we have track name changes
    has_track_changes = any(rec.get(Key.OLD_TRACK_NAME) and rec.get(Key.NEW_TRACK_NAME) for rec in records)

    if has_album_changes and not has_track_changes:
        # Only album changes
        headers["Old Album Name"] = Key.OLD_ALBUM_NAME
        headers["New Album Name"] = Key.NEW_ALBUM_NAME
    elif has_track_changes and not has_album_changes:
        # Only track changes
        headers["Album"] = Key.ALBUM
        headers["Old Track Name"] = Key.OLD_TRACK_NAME
        headers["New Track Name"] = Key.NEW_TRACK_NAME
    else:
        # Both types or fallback - show item type and generic names
        headers["Item Type"] = "item_type"
        headers["Old Name"] = "old_name"
        headers["New Name"] = "new_name"

    return headers


def _get_change_type_color(change_type: str) -> str:
    """Get the color for a specific change type."""
    type_colors = {
        "genre_update": "cyan",
        "year_update": "green",
        "name_change": "magenta",
        "other": "white",
    }
    return type_colors.get(change_type, "white")


def _create_value_transformers() -> dict[str, Callable[[dict[str, str]], str]]:
    """Create transformers for converting record values."""
    return {
        "item_type": lambda r: "Track" if r.get(Key.TRACK_NAME) else "Album",
        "old_name": lambda r: r.get(Key.OLD_TRACK_NAME) or r.get(Key.OLD_ALBUM_NAME, ""),
        "new_name": lambda r: r.get(Key.NEW_TRACK_NAME) or r.get(Key.NEW_ALBUM_NAME, ""),
    }


def _determine_if_changed(header: str, value: str, record: dict[str, str]) -> bool:
    """Determine if a value represents an actual change."""
    if "new" not in header.lower() or not value:
        return False

    if "year" in header.lower():
        old_year = record.get(Key.OLD_YEAR, "")
        return old_year != value
    if "genre" in header.lower():
        old_genre = record.get(Key.OLD_GENRE, "")
        return old_genre != value
    if "name" in header.lower():
        old_name = record.get(Key.OLD_TRACK_NAME, "") if "track" in header.lower() else record.get(Key.OLD_ALBUM_NAME, "")
        return old_name != value
    # Only known change types (year, genre, name) are highlighted
    # Log unrecognized headers for debugging
    logging.debug(
        "Unrecognized header '%s' with value '%s' - not highlighting (only year/genre/name are tracked)",
        header,
        value,
    )
    return False


def _apply_value_highlighting(header: str, value: str, record: dict[str, str]) -> str:
    """Apply appropriate highlighting to a table value."""
    if _determine_if_changed(header, value, record):
        return f"[bold yellow]{value}[/bold yellow]"
    if "old" in header.lower() and value and value != "":
        return f"[dim]{value}[/dim]"
    return value


def _render_change_group(
    console: Console,
    change_type: str,
    records: list[dict[str, str]],
    console_logger: logging.Logger,
) -> None:
    """Render records of a single change type as a rich table.

    Falls back to plain logging if no header mapping exists for the change type.
    """
    headers = _build_dynamic_headers(change_type, records)
    if not headers:
        for rec in records:
            console_logger.info("Other Change: %s", rec)
        return

    # Group heading with color
    color = _get_change_type_color(change_type)
    console.print(
        f"\n{Misc.EMOJI_CHANGE} [bold {color}]{change_type.upper().replace('_', ' ')} ({len(records)}):[/bold {color}]",
    )

    # Create table with headers
    table = Table(show_lines=True)
    for header in headers:
        table.add_column(header, overflow="fold")

    # Process each record with transformations and highlighting
    transformers = _create_value_transformers()
    for rec in records:
        row: list[str] = []
        for header, key in headers.items():
            value = str(transformers.get(key, lambda r, k=key: r.get(k, ""))(rec))
            highlighted_value = _apply_value_highlighting(header, value, rec)
            row.append(highlighted_value)
        table.add_row(*row)

    console.print(table)


def _sort_changes_by_artist_album(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort changes by artist and album for stable ordering."""
    return sorted(
        changes,
        key=lambda c: (
            c.get(Key.ARTIST, Misc.UNKNOWN),
            c.get(Key.ALBUM, Misc.UNKNOWN),
        ),
    )


def _group_changes_by_type(changes: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Group changes by their change type."""
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for rec in changes:
        grouped[rec.get(Key.CHANGE_TYPE, ChangeType.OTHER)].append(rec)
    return grouped


def _render_compact_change(console: Console, change_type: str, record: dict[str, Any]) -> None:
    """Render a single change record in compact arrow format."""
    artist = record.get(Key.ARTIST, "")
    album = record.get(Key.ALBUM, "")
    track = record.get(Key.TRACK_NAME, "")

    if change_type == "genre_update":
        old_val = record.get(Key.OLD_GENRE, "")
        new_val = record.get(Key.NEW_GENRE, "")
        item = f"{artist} - {track or album}"
        console.print(f"  {item}: [dim]{old_val}[/dim] → [bold yellow]{new_val}[/bold yellow]")
    elif change_type == "year_update":
        old_val = record.get(Key.OLD_YEAR, "")
        new_val = record.get(Key.NEW_YEAR, "")
        item = f"{artist} - {album}"
        # Only highlight in yellow if the year actually changed
        if old_val != new_val:
            console.print(f"  {item}: [dim]{old_val or '(empty)'}[/dim] → [bold yellow]{new_val}[/bold yellow]")
        else:
            console.print(f"  {item}: {old_val or '(empty)'} → {new_val}")
    elif change_type == "name_change":
        if record.get(Key.OLD_TRACK_NAME):
            old_val = record.get(Key.OLD_TRACK_NAME, "")
            new_val = record.get(Key.NEW_TRACK_NAME, "")
            item = f"{artist} - Track"
        else:
            old_val = record.get(Key.OLD_ALBUM_NAME, "")
            new_val = record.get(Key.NEW_ALBUM_NAME, "")
            item = f"{artist} - Album"
        console.print(f"  {item}: [dim]{old_val}[/dim] → [bold yellow]{new_val}[/bold yellow]")


def _render_compact_group(console: Console, change_type: str, records: list[dict[str, str]]) -> None:
    """Render a group of changes in compact mode with arrows."""
    color = _get_change_type_color(change_type)
    console.print(f"\n[bold {color}]{change_type.upper().replace('_', ' ')} ({len(records)}):[/bold {color}]")

    for record in records:
        _render_compact_change(console, change_type, record)


def _get_csv_fieldnames() -> list[str]:
    """Get the standard fieldnames for CSV export."""
    return [
        Key.CHANGE_TYPE,
        Key.ARTIST,
        Key.ALBUM,
        Key.TRACK_NAME,
        Key.OLD_GENRE,
        Key.NEW_GENRE,
        Key.OLD_YEAR,
        Key.NEW_YEAR,
        Key.OLD_TRACK_NAME,
        Key.NEW_TRACK_NAME,
        Key.OLD_ALBUM_NAME,
        Key.NEW_ALBUM_NAME,
        Key.TIMESTAMP,
    ]


def _print_no_changes_summary(console: Console | None = None) -> None:
    """Render the standard console summary when no changes are present."""
    active_console = console or Console()
    active_console.print(f"\n{Misc.EMOJI_REPORT} [bold]Changes Summary:[/]")
    active_console.print("[dim italic]No changes were made during this run.[/dim italic]")
    active_console.print("-" * Format.SEPARATOR_100)


def _is_real_change(change: dict[str, Any], logger: logging.Logger | None = None) -> bool:
    """Check if a change represents a real modification (old != new).

    Args:
        change: Change record dictionary
        logger: Optional logger for warnings about unknown change types

    Returns:
        True if the change has different old and new values, False otherwise
    """
    change_type = change.get("change_type", "")

    if change_type == "genre_update":
        return change.get("old_genre") != change.get("new_genre")
    if change_type == "year_update":
        return change.get("old_year") != change.get("new_year")
    if change_type == "name_clean":
        return change.get("old_track_name") != change.get("new_track_name")
    if change_type == "metadata_cleaning":
        # Check both album and track name changes
        album_changed = change.get("old_album_name") != change.get("new_album_name")
        track_changed = change.get("old_track_name") != change.get("new_track_name")
        return album_changed or track_changed

    # For unknown types, log a warning and include by default to be safe
    if logger:
        logger.warning(
            "Unknown change_type '%s' encountered in _is_real_change; defaulting to include. This may indicate a typo or missing handler.",
            change_type,
        )
    return True


def save_unified_changes_report(
    changes: list[dict[str, Any]],
    file_path: str | None,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    compact_mode: bool = False,
) -> None:
    """Pretty-print change summary to console and optionally persist full CSV.

    Args:
        changes: List of change records
        file_path: Path to save CSV report (None to skip file saving)
        console_logger: Logger for console output
        error_logger: Logger for errors
        compact_mode: If True, display changes in compact format with arrows

    """
    # Handle empty changes case
    if not changes:
        _print_no_changes_summary()
        return

    # Sort and group changes
    changes_sorted = _sort_changes_by_artist_album(changes)

    # Filter changes for console display (show only real changes where old != new)
    console_changes = [change for change in changes_sorted if _is_real_change(change, console_logger)]

    # Handle case where all changes were filtered out (e.g., force_update with no real changes)
    if not console_changes:
        _print_no_changes_summary()
        # Still save CSV with all entries (including force_update) for audit trail
        if file_path:
            ensure_directory(str(Path(file_path).parent), error_logger)
            _save_csv(
                changes_sorted,  # Use full unfiltered list for CSV audit trail
                _get_csv_fieldnames(),
                file_path,
                console_logger,
                error_logger,
                Misc.CHANGES_REPORT_TYPE,
            )
        return

    grouped = _group_changes_by_type(console_changes)

    # Console output (filtered - only real changes)
    console = Console()
    console.print(f"\n{Misc.EMOJI_REPORT} [bold]Changes Summary:[/]")

    # Render each group based on mode
    for change_type, records in grouped.items():
        if compact_mode:
            _render_compact_group(console, change_type, records)
        else:
            _render_change_group(console, change_type, records, console_logger)

    console.print("-" * Format.SEPARATOR_100)

    # CSV export if file path provided (save ALL changes including force_update duplicates)
    if file_path:
        ensure_directory(str(Path(file_path).parent), error_logger)
        _save_csv(
            changes_sorted,  # Use full unfiltered list for CSV audit trail
            _get_csv_fieldnames(),
            file_path,
            console_logger,
            error_logger,
            Misc.CHANGES_REPORT_TYPE,
        )


def _convert_changelog_to_dict(item: dict[str, Any] | ChangeLogEntry) -> dict[str, Any]:
    """Convert ChangeLogEntry objects to dictionary format.

    Uses Pydantic's model_dump() to ensure all fields are automatically included
    when ChangeLogEntry evolves, preventing silent omissions.
    """
    if isinstance(item, ChangeLogEntry):
        # Type ignore because Pydantic's model_dump() returns dict[str, Any] but mypy sees it as Any
        result: dict[str, Any] = item.model_dump()
        # Maintain backwards compatibility for summary consumers expecting 'album'
        if "album" not in result:
            result[Key.ALBUM] = result.get("album_name", "")
        return result
    return item


def _determine_change_type(change: dict[str, Any]) -> str:
    """Determine the change type based on available fields."""
    if ("new_genre" in change and change.get("new_genre")) or change.get("field") == "genre":
        return "genre"
    if ("new_year" in change and change.get("new_year")) or change.get("field") == "year":
        return "year"
    if "new_track_name" in change or "new_album_name" in change or change.get("field") == "name":
        return "name"
    return "other"


def _map_genre_field_values(change: dict[str, Any]) -> None:
    """Map genre field values from old_value/new_value to specific fields."""
    if "old_value" in change and "old_genre" not in change:
        change["old_genre"] = change["old_value"]
    if "new_value" in change and "new_genre" not in change:
        change["new_genre"] = change["new_value"]


def _map_year_field_values(change: dict[str, Any]) -> None:
    """Map year field values from old_value/new_value to specific fields."""
    if "old_value" in change and "old_year" not in change:
        change["old_year"] = change["old_value"]
    if "new_value" in change and "new_year" not in change:
        change["new_year"] = change["new_value"]


def _normalize_field_mappings(change: dict[str, Any]) -> None:
    """Normalize field mappings for CSV output."""
    field = change.get("field")

    if field == "genre":
        _map_genre_field_values(change)
    elif field == "year":
        _map_year_field_values(change)

    # Ensure track_name is mapped from name field
    if "name" in change and "track_name" not in change:
        change["track_name"] = change["name"]


def _add_timestamp_to_filename(file_path: str | None) -> str | None:
    """Add timestamp to filename to preserve previous reports."""
    if file_path is None:
        return None
    path_obj = Path(file_path)
    base = str(path_obj.parent / path_obj.stem)
    ext = path_obj.suffix
    timestamp_suffix = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{base}_{timestamp_suffix}{ext}"


def save_changes_report(
    changes: Sequence[dict[str, Any] | ChangeLogEntry],
    file_path: str | None,
    console_logger: logging.Logger | None = None,
    error_logger: logging.Logger | None = None,
    add_timestamp: bool = False,
    compact_mode: bool = True,  # Default to compact mode for better visibility
) -> None:
    """Save the list of change dictionaries to a CSV file.

    By default, it overwrites the specified file. If `add_timestamp` is True,
    it appends a timestamp to the filename to preserve previous reports.

    Args:
        changes: List of change dictionaries or ChangeLogEntry objects
        file_path: Path to save the CSV file (None to skip file saving)
        console_logger: Logger for console output
        error_logger: Logger for errors
        add_timestamp: If True, add timestamp to filename
        compact_mode: If True, display changes in compact format with yellow highlighting

    """
    if console_logger is None:
        console_logger = logging.getLogger("console_logger")
    if error_logger is None:
        error_logger = logging.getLogger("error_logger")

    # Convert ChangeLogEntry objects to dictionaries
    dict_changes = [_convert_changelog_to_dict(item) for item in changes]

    # Process each change for type determination and field normalization
    for change in dict_changes:
        if "change_type" not in change:
            change["change_type"] = _determine_change_type(change)
        _normalize_field_mappings(change)

    # Generate final file path with optional timestamp (handle None)
    final_path = _add_timestamp_to_filename(file_path) if add_timestamp and file_path else file_path

    save_unified_changes_report(
        dict_changes,
        final_path,
        console_logger,
        error_logger,
        compact_mode,
    )


def save_changes_csv(
    changes: Sequence[dict[str, Any] | ChangeLogEntry],
    file_path: str,
    console_logger: logging.Logger | None = None,
    error_logger: logging.Logger | None = None,
    add_timestamp: bool = False,
) -> None:
    """Compatibility wrapper for saving change reports in CSV format."""
    save_changes_report(
        changes,
        file_path,
        console_logger,
        error_logger,
        add_timestamp,
    )


def _get_dry_run_fieldnames() -> list[str]:
    """Get the standard fieldnames for dry run CSV export."""
    return [
        "change_type",
        "track_id",
        "artist",
        "album",
        "track_name",
        "original_name",
        "cleaned_name",
        "original_album",
        "cleaned_album",
        "original_genre",
        "simulated_genre",
        "original_year",
        "simulated_year",
        "date_added",
        "timestamp",
    ]


def _process_cleaning_changes(cleaning_changes: list[dict[str, str]]) -> list[dict[str, str]]:
    """Process cleaning changes and normalize field mappings."""
    processed_changes: list[dict[str, str]] = []
    for change in cleaning_changes:
        change_copy = change.copy()
        change_copy["change_type"] = "cleaning"

        # Map original_name to track_name for consistency if needed
        if "track_name" not in change_copy and "original_name" in change_copy:
            change_copy["track_name"] = change_copy["original_name"]

        processed_changes.append(change_copy)
    return processed_changes


def _determine_genre_year_change_type(change: dict[str, str]) -> None:
    """Determine change type for genre/year changes if not already set."""
    if "change_type" not in change:
        if "new_genre" in change:
            change["change_type"] = "genre_update"
        elif "new_year" in change:
            change["change_type"] = "year_update"


def _process_genre_fields(change: dict[str, str]) -> None:
    """Process genre-specific field mappings."""
    if "new_genre" in change:
        change["simulated_genre"] = change.pop("new_genre")
    if "old_genre" in change:
        change["original_genre"] = change.pop("old_genre")


def _process_year_fields(change: dict[str, str]) -> None:
    """Process year-specific field mappings."""
    if "new_year" in change:
        change["simulated_year"] = change.pop("new_year")
    if "old_year" in change:
        change["original_year"] = change.pop("old_year")


def _process_genre_year_changes(genre_changes: list[dict[str, str]]) -> list[dict[str, str]]:
    """Process genre and year changes with proper field mappings."""
    processed_changes: list[dict[str, str]] = []
    for change in genre_changes:
        change_copy = change.copy()

        # Determine change type and process fields
        _determine_genre_year_change_type(change_copy)

        if change_copy.get("change_type") == "genre_update":
            _process_genre_fields(change_copy)
        elif change_copy.get("change_type") == "year_update":
            _process_year_fields(change_copy)

        processed_changes.append(change_copy)
    return processed_changes


def save_unified_dry_run(
    cleaning_changes: list[dict[str, str]],
    genre_changes: list[dict[str, str]],
    file_path: str,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> None:
    """Save unified dry run report combining cleaning and genre changes.

    Args:
        cleaning_changes: List of dictionaries with cleaning changes
        genre_changes: List of dictionaries with genre changes
        file_path: Path to the CSV file
        console_logger: Logger for console output
        error_logger: Logger for error output

    """
    # Get standard fieldnames and process changes using helper functions
    fieldnames = _get_dry_run_fieldnames()
    processed_cleaning = _process_cleaning_changes(cleaning_changes)
    processed_genre = _process_genre_year_changes(genre_changes)

    # Combine and sort changes
    combined_changes = processed_cleaning + processed_genre
    combined_changes.sort(
        key=lambda x: (x.get("artist", "Unknown"), x.get("album", "Unknown")),
    )

    # Save to CSV with directory creation
    ensure_directory(str(Path(file_path).parent), error_logger)
    _save_csv(
        combined_changes,
        fieldnames,
        file_path,
        console_logger,
        error_logger,
        "dry run report",
    )


def _get_expected_track_fieldnames() -> list[str]:
    """Get the expected fieldnames for track CSV loading."""
    return [
        "id",
        "name",
        "artist",
        "album",
        "genre",
        "date_added",
        "last_modified",
        "track_status",
        "old_year",
        "new_year",
    ]


def _validate_csv_header(reader: "csv.DictReader[str]", expected_fieldnames: list[str], csv_path: str, logger: logging.Logger) -> list[str]:
    """Validate CSV header and return fields to read."""
    if reader.fieldnames is None:
        logger.warning("CSV file %s is empty or has no header.", csv_path)
        return []

    fieldnames: Sequence[str] = reader.fieldnames or []
    if any(field not in fieldnames for field in expected_fieldnames):
        logger.warning(
            "CSV header in %s does not match expected fieldnames. Expected: %s, Found: %s. Attempting to load with available fields.",
            csv_path,
            expected_fieldnames,
            fieldnames,
        )
        actual_fieldnames: list[str] = list(fieldnames)
        return [field for field in expected_fieldnames if field in actual_fieldnames]

    return expected_fieldnames


def _create_track_from_row(row: dict[str, str], fields_to_read: list[str], expected_fieldnames: list[str]) -> TrackDict | None:
    """Create TrackDict from CSV row data."""
    if not row.get("id", "").strip():
        return None

    # Create track dictionary, getting values for fields_to_read
    track_data = {field: row.get(field, "").strip() for field in fields_to_read}
    # Ensure all expected fields are present in the track dictionary, even if empty
    for field in expected_fieldnames:
        track_data.setdefault(field, "")

    # Create actual TrackDict instance with proper type conversion
    return TrackDict(
        id=track_data["id"],
        name=track_data["name"],
        artist=track_data["artist"],
        album=track_data["album"],
        genre=track_data["genre"] or None,
        date_added=track_data["date_added"] or None,
        last_modified=track_data.get("last_modified", "") or None,
        track_status=track_data["track_status"] or None,
        old_year=track_data["old_year"] or None,
        new_year=track_data["new_year"] or None,
    )


def load_track_list(csv_path: str) -> dict[str, TrackDict]:
    """Load the track list from the CSV file into a dictionary. The track ID is used as the key.

    Reads columns: id, name, artist, album, genre, date_added, track_status, old_year, new_year.

    :param csv_path: Path to the CSV file.
    :return: Dictionary of track dictionaries.
    """
    track_map: dict[str, TrackDict] = {}
    if not Path(csv_path).exists():
        return track_map

    logger = logging.getLogger("console_logger")
    expected_fieldnames = _get_expected_track_fieldnames()

    try:
        with Path(csv_path).open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields_to_read = _validate_csv_header(reader, expected_fieldnames, csv_path, logger)
            if not fields_to_read:
                return track_map

            for row in reader:
                track = _create_track_from_row(row, fields_to_read, expected_fieldnames)
                if track and track.id:
                    track_map[track.id] = track

        logger.info("Loaded %d tracks from track_list.csv.", len(track_map))
    except (OSError, UnicodeError, csv.Error):
        logger.exception("Could not read track_list.csv")
    return track_map


# -------------------- Track List Synchronization Helpers --------------------


def _get_processed_albums_from_csv(
    csv_map: dict[str, TrackDict],
    cache_service: CacheServiceProtocol,
) -> dict[str, str]:
    """Return a mapping album_key -> new_year for albums already processed in CSV."""
    processed: dict[str, str] = {}
    for track in csv_map.values():
        artist = (track.artist or "").strip()
        album = (track.album or "").strip()
        new_year = (track.new_year or "").strip()
        if artist and album and new_year:
            album_key = cache_service.generate_album_key(artist, album)
            processed[album_key] = new_year
    return processed


async def _build_current_map(
    all_tracks: Sequence[TrackDict],
    processed_albums: dict[str, str],
    cache_service: CacheServiceProtocol,
    partial_sync: bool,
    error_logger: logging.Logger,
) -> dict[str, TrackDict]:
    """Return a normalized map id -> track_dict for the current fetch.

    Handles partial_sync logic and ensures year consistency with cache & CSV.
    """
    current: dict[str, TrackDict] = {}
    for tr in all_tracks:
        # Validate track ID
        tid = (tr.id or "").strip()
        if not tid:
            continue

        # Normalize basic fields
        artist = (tr.artist or "").strip()
        album = (tr.album or "").strip()
        album_key = cache_service.generate_album_key(artist, album)

        # Ensure year fields are properly initialized
        _normalize_track_year_fields(tr)

        # Handle partial sync with cache coordination
        if partial_sync:
            await _handle_partial_sync_cache(tr, processed_albums, cache_service, album_key, artist, album, error_logger)

        # Create normalized track dictionary
        current[tid] = _create_normalized_track_dict(tr, tid, artist, album)
    return current


def _normalize_track_year_fields(track: TrackDict) -> None:
    """Ensure mandatory year fields exist in track."""
    if track.old_year is None:
        track.old_year = ""
    if track.new_year is None:
        track.new_year = ""


async def _handle_partial_sync_cache(
    track: TrackDict,
    processed_albums: dict[str, str],
    cache_service: CacheServiceProtocol,
    album_key: str,
    artist: str,
    album: str,
    error_logger: logging.Logger,
) -> None:
    """Handle partial sync logic with cache coordination."""
    if album_key not in processed_albums:
        return

    track.new_year = processed_albums[album_key]
    try:
        cached_year = await cache_service.get_album_year_from_cache(artist, album)
        if not cached_year or cached_year != track.new_year:
            await cache_service.store_album_year_in_cache(artist, album, track.new_year)
    except OSError:
        error_logger.exception(
            "Error syncing year from CSV to cache for %s - %s",
            artist,
            album,
        )


def _create_normalized_track_dict(track: TrackDict, tid: str, artist: str, album: str) -> TrackDict:
    """Create normalized TrackDict with clean field values."""
    return TrackDict(
        id=tid,
        name=(track.name or "").strip(),
        artist=artist,
        album=album,
        genre=(track.genre or "").strip(),
        date_added=(track.date_added or "").strip(),  # Correct field name
        track_status=(track.track_status or "").strip(),  # Correct field name
        old_year=(track.old_year or "").strip(),
        new_year=(track.new_year or "").strip(),
    )


def _get_fields_to_check() -> list[str]:
    """Get the list of fields that should be checked during track merging.

    Note:
        Uses old_year and new_year for change tracking. The 'year' field exists
        in TrackDict but is not populated in this context (reports.py sync operations).
    """
    return [
        "name",
        "artist",
        "album",
        "genre",
        "date_added",
        "track_status",
        "old_year",
        "new_year",
    ]


def _check_if_track_needs_update(old_data: TrackDict, new_data: TrackDict, fields: list[str]) -> bool:
    """Check if a track needs updating based on field differences."""
    return any(getattr(old_data, field, "") != getattr(new_data, field, "") for field in fields)


def _update_existing_track_fields(old_data: TrackDict, new_data: TrackDict, fields: list[str]) -> None:
    """Update existing track with new field values.

    Logs a warning if a field does not exist on TrackDict to catch typos
    and prevent silent failures.
    """
    for field in fields:
        missing_in = []
        if not hasattr(old_data, field):
            missing_in.append("old_data")
        if not hasattr(new_data, field):
            missing_in.append("new_data")
        if missing_in:
            logging.warning(
                "Field '%s' does not exist on %s. Skipping update for this field.",
                field,
                " and ".join(missing_in),
            )
            continue
        new_value = getattr(new_data, field)
        if new_value is not None:
            setattr(old_data, field, new_value)


def _merge_current_into_csv(
    current_map: dict[str, TrackDict],
    csv_map: dict[str, TrackDict],
) -> int:
    """Merge *current_map* into *csv_map* and return count of added/updated items."""
    updated = 0
    fields_to_check = _get_fields_to_check()

    for tid, new_data in current_map.items():
        # Add new track if not exists
        if tid not in csv_map:
            csv_map[tid] = new_data
            updated += 1
            continue

        # Update existing track if fields have changed
        old_data = csv_map[tid]
        if _check_if_track_needs_update(old_data, new_data, fields_to_check):
            _update_existing_track_fields(old_data, new_data, fields_to_check)
            updated += 1
    return updated


def _build_osascript_command(script_path: str, artist_filter: str | None) -> list[str]:
    """Build osascript command with optional artist filter."""
    cmd = ["osascript", script_path]
    if artist_filter:
        cmd.append(artist_filter)
    return cmd


def _parse_osascript_output(raw_output: str) -> dict[str, dict[str, str]]:
    """Parse AppleScript output into track cache dictionary.

    Validates field count to detect AppleScript output format changes and logs
    warnings for lines with incorrect field counts to aid debugging.
    """
    tracks_cache: dict[str, dict[str, str]] = {}

    # AppleScript uses ASCII character 29 (line separator) and 30 (field separator)
    line_separator = chr(29)  # ASCII 29
    field_separator = chr(30)  # ASCII 30
    tracks_data = raw_output.strip().split(line_separator)

    # AppleScript returns 10 fields: ID, Name, Artist, Album, Genre, DateAdded, TrackStatus, Year, ReleaseYear, NewYear
    expected_field_count = 10

    for line_num, track_line in enumerate(tracks_data, start=1):
        if not track_line.strip():  # Skip empty lines
            continue
        fields = track_line.split(field_separator)
        if len(fields) != expected_field_count:
            logging.warning(
                "Track line %d has %d fields, expected %d. Skipping line: %r",
                line_num,
                len(fields),
                expected_field_count,
                track_line[:100],  # Truncate long lines for readability
            )
            continue
        track_id = fields[0]
        # Fields order: ID, Name, Artist, Album, Genre, DateAdded, TrackStatus, Year, ReleaseYear, NewYear
        missing_value = "missing value"
        tracks_cache[track_id] = {
            "date_added": fields[5] if fields[5] != missing_value else "",
            "track_status": fields[6] if fields[6] != missing_value else "",
            "old_year": fields[7] if fields[7] != missing_value else "",
        }

    return tracks_cache


def _handle_osascript_error(process_returncode: int, stdout: bytes | None, stderr: bytes | None) -> None:
    """Handle and log osascript execution errors."""
    error_msg = stderr.decode() if stderr else "No error message"
    print(f"DEBUG: osascript failed with return code {process_returncode}: {error_msg}")
    print(f"DEBUG: stdout was: {stdout.decode() if stdout else 'None'}")
    logging.getLogger(__name__).warning("osascript failed (return code %d): %s", process_returncode, error_msg)


async def _execute_osascript_process(cmd: list[str]) -> tuple[int, bytes | None, bytes | None]:
    """Execute osascript subprocess and return results."""
    process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = await process.communicate()
    return (process.returncode or 0), stdout, stderr


async def _fetch_track_fields_direct(script_path: str, artist_filter: str | None) -> dict[str, dict[str, str]]:
    """Fetch track fields using direct osascript call.

    Args:
        script_path: Path to AppleScript file
        artist_filter: Artist filter to limit query scope

    Returns:
        Dict mapping track_id to dict with date_added, track_status, old_year fields

    """
    tracks_cache: dict[str, dict[str, str]] = {}

    try:
        cmd = _build_osascript_command(script_path, artist_filter)
        returncode, stdout, stderr = await _execute_osascript_process(cmd)

        if returncode == 0 and stdout:
            raw_output = stdout.decode()
            tracks_cache = _parse_osascript_output(raw_output)
        else:
            _handle_osascript_error(returncode, stdout, stderr)

    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as e:
        logging.getLogger(__name__).warning("Failed to fetch track fields directly: %s", e)
    except (AttributeError, ValueError, IndexError) as e:
        logging.getLogger(__name__).exception("Parsing or process error in _fetch_track_fields_direct: %s", e)

    return tracks_cache


async def _fetch_missing_track_fields_for_sync(
    final_list: list[TrackDict], applescript_client: AppleScriptClientProtocol | None, console_logger: logging.Logger
) -> dict[str, dict[str, str]]:
    """Fetch missing track fields via AppleScript if needed for sync operation."""
    tracks_cache: dict[str, dict[str, str]] = {}

    has_missing_fields = any(not track.date_added or not track.track_status or not track.old_year for track in final_list if track.id)

    if has_missing_fields and applescript_client is not None:
        try:
            console_logger.info("Fetching track fields via direct osascript...")

            # Use applescript_client's configured directory instead of hardcoded path
            if applescript_client.apple_scripts_dir:
                script_path = str(Path(applescript_client.apple_scripts_dir) / "fetch_tracks.scpt")
            else:
                # Fallback to relative path if apple_scripts_dir is not available
                script_path = str(Path("applescripts") / "fetch_tracks.scpt")

            artist_filter = None  # None means fetch ALL tracks
            console_logger.info("Using osascript with script=%s, filter=%s", script_path, artist_filter or "ALL")

            tracks_cache = await _fetch_track_fields_direct(script_path, artist_filter)
            console_logger.info("Cached %d track records via osascript", len(tracks_cache))
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as e:
            console_logger.warning("Failed to fetch track fields via osascript: %s", e)
        except (AttributeError, ValueError, IndexError) as e:
            console_logger.exception("Parsing or process error fetching track fields via osascript: %s", e)

    return tracks_cache


def _update_track_with_cached_fields_for_sync(track: TrackDict, tracks_cache: dict[str, dict[str, str]]) -> None:
    """Update track with cached fields if they were empty for sync operation.

    Uses .get() for safe dictionary access to prevent KeyError if cache structure
    changes. Currently, last_modified is not populated by _parse_osascript_output(),
    but this defensive approach ensures future compatibility.
    """
    if not track.id or track.id not in tracks_cache:
        return
    cached_fields = tracks_cache[track.id]

    if not track.date_added and cached_fields.get("date_added"):
        track.date_added = cached_fields["date_added"]
    if not getattr(track, "last_modified", "") and (cached_value := cached_fields.get("last_modified")):
        track.last_modified = cached_value
    if not track.track_status and cached_fields.get("track_status"):
        track.track_status = cached_fields["track_status"]
    if not track.old_year and cached_fields.get("old_year"):
        track.old_year = cached_fields["old_year"]


def _convert_track_to_csv_dict(track: TrackDict) -> dict[str, str]:
    """Convert TrackDict to CSV dictionary format."""
    return {
        "id": track.id or "",
        "name": track.name or "",
        "artist": track.artist or "",
        "album": track.album or "",
        "genre": track.genre or "",
        "date_added": track.date_added or "",
        "last_modified": getattr(track, "last_modified", "") or "",
        "track_status": track.track_status or "",
        "old_year": track.old_year or "",
        "new_year": track.new_year or "",
    }


# noinspection PyUnusedLocal
async def sync_track_list_with_current(
    all_tracks: Sequence[TrackDict],
    csv_path: str,
    cache_service: CacheServiceProtocol,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    partial_sync: bool = False,
    applescript_client: AppleScriptClientProtocol | None = None,
) -> None:
    """Synchronize the current track list with the data in a CSV file.

    Args:
        all_tracks: List of track dictionaries to sync.
        csv_path: Path to the CSV file.
        cache_service: Cache service protocol for album year caching.
        console_logger: Logger for console output.
        error_logger: Logger for error output.
        partial_sync: Whether to perform a partial sync (only update new_year if missing).
        applescript_client: AppleScript client for fetching missing track fields.

    """
    console_logger.info(
        "Starting sync: fetched %s tracks; CSV file: %s",
        len(all_tracks),
        csv_path,
    )

    # 1. Load existing CSV as dict
    csv_map = load_track_list(csv_path)

    # 2. Determine albums already processed (for partial sync logic)
    processed_albums = _get_processed_albums_from_csv(csv_map, cache_service)

    # 3. Build normalized map for freshly fetched tracks
    current_map = await _build_current_map(
        all_tracks,
        processed_albums,
        cache_service,
        partial_sync,
        error_logger,
    )

    # 4. Merge current into CSV map
    added_or_updated = _merge_current_into_csv(current_map, csv_map)
    console_logger.info("Added/Updated %s tracks in CSV.", added_or_updated)

    # 5. Remove tracks from CSV that no longer exist in Music.app
    removed_count = len([tid for tid in csv_map if tid not in current_map])
    csv_map = {tid: track for tid, track in csv_map.items() if tid in current_map}

    if removed_count > 0:
        console_logger.info("Removed %s tracks from CSV that no longer exist in Music.app", removed_count)

    # Generate the final list from the updated csv_map and write to CSV
    final_list = list(csv_map.values())
    console_logger.info("Final CSV track count after sync: %s", len(final_list))

    # Define the fieldnames for the output CSV file
    fieldnames = [
        "id",
        "name",
        "artist",
        "album",
        "genre",
        "date_added",
        "last_modified",
        "track_status",
        "old_year",
        "new_year",
    ]

    # Convert TrackDict to dict[str, str] for _save_csv with proper field mapping
    track_dicts: list[dict[str, str]] = []
    missing_fields_count = 0
    tracks_cache: dict[str, dict[str, str]] = {}

    # Fetch missing track fields via AppleScript if needed
    tracks_cache = await _fetch_missing_track_fields_for_sync(final_list, applescript_client, console_logger)

    # Process tracks and convert to CSV format
    for track in final_list:
        _update_track_with_cached_fields_for_sync(track, tracks_cache)

        if not track.date_added and track.id and track.id in tracks_cache and tracks_cache[track.id]["date_added"]:
            missing_fields_count += 1

        track_dict = _convert_track_to_csv_dict(track)
        track_dicts.append(track_dict)

    if missing_fields_count > 0:
        console_logger.info("Filled missing fields for %d tracks via AppleScript cache", missing_fields_count)
    _save_csv(track_dicts, fieldnames, csv_path, console_logger, error_logger, "tracks")


def save_track_map_to_csv(
    track_map: dict[str, TrackDict],
    csv_path: str,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> None:
    """Persist the provided track map to CSV using standard field ordering."""

    sorted_tracks = sorted(track_map.values(), key=lambda t: t.id)
    track_dicts = [_convert_track_to_csv_dict(track) for track in sorted_tracks]
    fieldnames = _get_expected_track_fieldnames()
    _save_csv(track_dicts, fieldnames, csv_path, console_logger, error_logger, "tracks")


def _generate_empty_html_template(date_str: str, report_file: str, console_logger: logging.Logger, error_logger: logging.Logger) -> None:
    """Generate and save an empty HTML template when no data is available."""
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Analytics Report for {date_str}</title>
    <style>
        table {{
            border-collapse: collapse;
            width: 100%;
            font-size: 0.95em;
        }}
        th, td {{
            border: 1px solid #dddddd;
            text-align: left;
            padding: 6px;
        }}
        th {{
            background-color: #f2f2f2;
        }}
        .error {{
            background-color: #ffcccc;
        }}
    </style>
</head>
<body>
    <h2>Analytics Report for {date_str}</h2>
    <p><strong>No analytics data was collected during this run.</strong></p>
    <p>Possible reasons:</p>
    <ul>
        <li>Script executed in dry-run mode without analytics collection</li>
        <li>No decorated functions were called</li>
        <li>Decorator failed to log events</li>
    </ul>
</body>
</html>"""
    try:
        Path(report_file).parent.mkdir(parents=True, exist_ok=True)
        with Path(report_file).open("w", encoding="utf-8") as file:
            file.write(html_content)
        console_logger.info("Empty analytics HTML report saved to %s.", report_file)
    except (OSError, UnicodeError):
        error_logger.exception("Failed to save empty HTML report")


def _group_events_by_duration_and_success(
    events: list[dict[str, Any]],
    duration_thresholds: dict[str, float],
    group_successful_short_calls: bool,
    error_logger: logging.Logger,
) -> tuple[dict[tuple[str, str], dict[str, float]], list[dict[str, Any]]]:
    """Group events by duration and success status."""
    grouped_short_success: dict[tuple[str, str], dict[str, float]] = {}
    big_or_fail_events: list[dict[str, Any]] = []
    short_max = duration_thresholds.get("short_max", 2)

    if not group_successful_short_calls:
        return grouped_short_success, events

    for event in events:
        try:
            event_duration = event[Misc.DURATION_FIELD]
            success = event["Success"]

            if success and event_duration <= short_max:
                key = (
                    event.get("Function", "Unknown"),
                    event.get("Event Type", "Unknown"),
                )
                if key not in grouped_short_success:
                    grouped_short_success[key] = {"count": 0, "total_duration": 0.0}
                grouped_short_success[key]["count"] += 1
                grouped_short_success[key]["total_duration"] += event_duration
            else:
                big_or_fail_events.append(event)
        except KeyError:
            error_logger.exception(
                "Missing key in event data during grouping, event: %s",
                event,
            )
            big_or_fail_events.append(event)

    return grouped_short_success, big_or_fail_events


def _generate_main_html_template(
    date_str: str,
    call_counts: dict[str, int],
    success_counts: dict[str, int],
    events: list[dict[str, Any]],
    force_mode: bool,
) -> str:
    """Generate the main HTML template with header and summary."""
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Analytics Report for {date_str}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            line-height: 1.6;
        }}
        h2, h3 {{
            color: #333;
            border-bottom: 1px solid #ddd;
            padding-bottom: 10px;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            font-size: 0.95em;
            margin-bottom: 20px;
        }}
        th, td {{
            border: 1px solid #dddddd;
            text-align: left;
            padding: 8px;
        }}
        th {{
            background-color: #f2f2f2;
            position: sticky;
            top: 0;
        }}
        tr:nth-child(even) {{
            background-color: #f9f9f9;
        }}
        .error {{
            background-color: #ffcccc;
        }}
        .summary {{
            background-color: #e6f3ff;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}
        .run-type {{
            font-weight: bold;
            color: #0066cc;
        }}
        .duration-short {{ background-color: #e0ffe0; }}
        .duration-medium {{ background-color: #fffacd; }}
        .duration-long {{ background-color: #ffb0b0; }}
    </style>
</head>
<body>
    <h2>Analytics Report for {date_str}</h2>
    <div class="summary">
        <p class="run-type">Run type: {"Full scan" if force_mode else "Incremental update"}</p>
        <p><strong>Total functions:</strong> {len(call_counts)}</p>
        <p><strong>Total events:</strong> {len(events)}</p>
        <p><strong>Success rate:</strong> {
        (sum(success_counts.values()) / sum(call_counts.values()) * 100 if sum(call_counts.values()) else 0):.1f}%</p>
    </div>"""


def _generate_grouped_success_table(
    grouped_short_success: dict[tuple[str, str], dict[str, float]],
    group_successful_short_calls: bool,
) -> str:
    """Generate HTML table for grouped successful short calls."""
    html = """
    <h3>Grouped Short & Successful Calls</h3>
    <table>
        <tr>
            <th>Function</th>
            <th>Event Type</th>
            <th>Count</th>
            <th>Avg Duration (s)</th>
            <th>Total Duration (s)</th>
        </tr>"""

    if not (group_successful_short_calls and grouped_short_success):
        html += """
        <tr><td colspan="5">No short successful calls found or grouping disabled.</td></tr>"""
    else:
        for (function_name, event_type), values in sorted(grouped_short_success.items()):
            count = values["count"]
            total_duration = values["total_duration"]
            avg_duration = round(total_duration / count, 4) if count > 0 else 0
            html += f"""
        <tr>
            <td>{function_name}</td>
            <td>{event_type}</td>
            <td>{count}</td>
            <td>{avg_duration}</td>
            <td>{round(total_duration, 4)}</td>
        </tr>"""

    html += "</table>"
    return html


def _get_duration_category(event_duration: float, duration_thresholds: dict[str, float]) -> str:
    """Determine the duration category based on thresholds."""
    if event_duration <= duration_thresholds.get("short_max", 2):
        return "short"
    if event_duration <= duration_thresholds.get("medium_max", 5):
        return "medium"
    return "long"


def _determine_event_row_class(event: dict[str, Any], duration_thresholds: dict[str, float]) -> str:
    """Determine the CSS class for an event table row based on success and duration."""
    success = event.get("Success", False)
    if not success:
        return "error"

    event_duration = event.get(Misc.DURATION_FIELD, 0)
    duration_category = _get_duration_category(event_duration, duration_thresholds)
    return f"duration-{duration_category}"


def _format_event_table_row(event: dict[str, Any], row_class: str) -> str:
    """Format a single event as an HTML table row."""
    event_duration = event.get(Misc.DURATION_FIELD, 0)
    success = event.get("Success", False)
    success_display = "Yes" if success else "No"

    return f"""
        <tr class="{row_class}">
            <td>{event.get("Function", "Unknown")}</td>
            <td>{event.get("Event Type", "Unknown")}</td>
            <td>{event.get("Start Time", "Unknown")}</td>
            <td>{event.get("End Time", "Unknown")}</td>
            <td>{event_duration}</td>
            <td>{success_display}</td>
        </tr>"""


def _generate_detailed_events_table_html(
    big_or_fail_events: list[dict[str, Any]],
    duration_thresholds: dict[str, float],
    error_logger: logging.Logger,
) -> str:
    """Generate HTML table for detailed events (errors or long/medium calls)."""
    html = """
    <h3>Detailed Calls (Errors or Long/Medium Calls)</h3>
    <table>
        <tr>
            <th>Function</th>
            <th>Event Type</th>
            <th>Start Time</th>
            <th>End Time</th>
            <th>Duration (s)</th>
            <th>Success</th>
        </tr>"""

    if big_or_fail_events:
        for event in sorted(big_or_fail_events, key=lambda x: x.get("Start Time", "")):
            try:
                row_class = _determine_event_row_class(event, duration_thresholds)
                html += _format_event_table_row(event, row_class)
            except KeyError:
                error_logger.exception(
                    "Error formatting event for detailed list, event data: %s",
                    event,
                )
    else:
        html += """
        <tr><td colspan="6">No detailed calls to display.</td></tr>"""

    html += "</table>"
    return html


def _generate_summary_table_html(
    call_counts: dict[str, int],
    success_counts: dict[str, int],
    decorator_overhead: dict[str, float],
) -> str:
    """Generate HTML table for function call summary."""
    html = """
    <h3>Summary</h3>
    <table>
        <tr>
            <th>Function</th>
            <th>Call Count</th>
            <th>Success Count</th>
            <th>Success Rate (%)</th>
            <th>Total Decorator Overhead (s)</th>
        </tr>"""

    if call_counts:
        for function_name, count in sorted(call_counts.items()):
            success_count = success_counts.get(function_name, 0)
            success_rate = (success_count / count * 100) if count else 0
            overhead = decorator_overhead.get(function_name, 0)

            html += f"""
        <tr>
            <td>{function_name}</td>
            <td>{count}</td>
            <td>{success_count}</td>
            <td>{success_rate:.2f}</td>
            <td>{round(overhead, 4)}</td>
        </tr>"""
    else:
        html += """
        <tr><td colspan="5">No function calls recorded.</td></tr>"""

    html += """
    </table>
</body>
</html>"""
    return html


def save_html_report(
    events: list[dict[str, Any]],
    call_counts: dict[str, int],
    success_counts: dict[str, int],
    decorator_overhead: dict[str, float],
    config: dict[str, Any],
    console_logger: logging.Logger | None = None,
    error_logger: logging.Logger | None = None,
    group_successful_short_calls: bool = False,
    force_mode: bool = False,
) -> None:
    """Generate an HTML report from the provided analytics data."""
    if console_logger is None:
        console_logger = logging.getLogger("console_logger")
    if error_logger is None:
        error_logger = logging.getLogger("error_logger")

    # Configuration and setup
    console_logger.info(
        "Starting HTML report generation with %d events, %d function counts",
        len(events),
        len(call_counts),
    )
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    logs_base_dir = config.get("logs_base_dir", "")
    reports_dir = Path(logs_base_dir) / "analytics"
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_file = get_full_log_path(
        config,
        "analytics_html_report_file",
        str(Path("analytics") / ("analytics_full.html" if force_mode else "analytics_incremental.html")),
    )
    duration_thresholds = config.get("analytics", {}).get(
        "duration_thresholds",
        {"short_max": 2, "medium_max": 5, "long_max": 10},
    )

    # Check for empty data
    if not events and not call_counts:
        console_logger.warning(
            "No analytics data available for report - creating empty template",
        )
        _generate_empty_html_template(date_str, report_file, console_logger, error_logger)
        return

    # Group events
    grouped_short_success, big_or_fail_events = _group_events_by_duration_and_success(
        events, duration_thresholds, group_successful_short_calls, error_logger
    )

    # Generate HTML sections
    html_content = _generate_main_html_template(date_str, call_counts, success_counts, events, force_mode)
    html_content += _generate_grouped_success_table(grouped_short_success, group_successful_short_calls)
    html_content += _generate_detailed_events_table_html(big_or_fail_events, duration_thresholds, error_logger)
    html_content += _generate_summary_table_html(call_counts, success_counts, decorator_overhead)

    # Save the report
    try:
        Path(report_file).parent.mkdir(parents=True, exist_ok=True)
        with Path(report_file).open("w", encoding="utf-8") as file:
            file.write(html_content)
        console_logger.info("Analytics HTML report saved to %s.", report_file)
    except (OSError, UnicodeError):
        error_logger.exception("Failed to save HTML report")


def save_detailed_dry_run_report(
    changes: list[dict[str, str]],
    file_path: str,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> None:
    """Generate a detailed HTML report with separate tables for each change type."""
    if not changes:
        console_logger.info("No changes to report for dry run.")
        return

    # Group changes by type
    changes_by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    for change in changes:
        change_type = change.get("change_type", "unknown").replace("_", " ").title()
        changes_by_type[change_type].append(change)

    # Generate HTML
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Dry Run Report</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                margin: 10px;
                background-color: #f9f9f9;
                color: #333;
            }
            h2 { color: #1a1a1a; border-bottom: 2px solid #ddd; padding-bottom: 5px; }
            table {
                border-collapse: collapse; width: 100%; margin-bottom: 15px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.1); table-layout: auto;
            }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; white-space: nowrap; }
            thead { background-color: #e9ecef; }
            th { font-weight: 600; }
            tbody tr:nth-child(even) { background-color: #f2f2f2; }
            tbody tr:hover { background-color: #e9e9e9; }
            .container { max-width: 1200px; margin: auto; background: white; padding: 20px; border-radius: 8px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Dry Run Simulation Report</h1>
    """

    # Create table for each change type
    for change_type, change_list in changes_by_type.items():
        if not change_list:
            continue

        html += f"<h2>{change_type} ({len(change_list)} potential changes)</h2>"

        # Strictly defined columns for each report type for reliability
        header_map = {
            "Cleaning": [
                "artist",
                "original_name",
                "cleaned_name",
                "original_album",
                "cleaned_album",
            ],
            "Genre Update": [
                "artist",
                "album",
                "track_name",
                "original_genre",
                "new_genre",
            ],
            "Year Update": [
                "artist",
                "album",
                "track_name",
                "original_year",
                "simulated_year",
            ],
        }

        # Get the correct list of keys for the current report type.
        # If the type is unknown, fallback to the old behavior as a backup option.
        headers = header_map.get(
            change_type,
            [h for h in change_list[0] if h not in ["change_type", "timestamp", "track_id", "date_added"]],
        )

        html += "<table><thead><tr>"
        for header in headers:
            # Create readable column headers
            html += f"<th>{header.replace('_', ' ').title()}</th>"
        html += "</tr></thead><tbody>"

        # Fill table with data
        for item in change_list:
            html += "<tr>"
            # Go through the fixed list of headers
            for header_key in headers:
                value = item.get(header_key, "")
                html += f"<td>{value}</td>"
            html += "</tr>"

        html += "</tbody></table>"

    html += """
        </div>
    </body>
    </html>
    """

    # Save HTML file
    try:
        ensure_directory(str(Path(file_path).parent))
        with Path(file_path).open("w", encoding="utf-8") as f:
            f.write(html)
        console_logger.info(
            "Successfully generated detailed dry run HTML report at: %s",
            file_path,
        )
    except (OSError, UnicodeError):
        error_logger.exception("Failed to save detailed dry run HTML report")
