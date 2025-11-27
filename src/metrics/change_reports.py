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

import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from src.core.logger import ensure_directory
from src.core.models.track_models import ChangeLogEntry
from src.core.models.types import TrackDict
from src.metrics.csv_utils import TRACK_FIELDNAMES
from src.metrics.csv_utils import save_csv as _save_csv

# Re-export HTML report functions for backward compatibility
from src.metrics.html_reports import (
    save_detailed_dry_run_report,
    save_html_report,
)

# Re-export track sync functions for backward compatibility
from src.metrics.track_sync import (
    load_track_list,
    save_track_map_to_csv,
    sync_track_list_with_current,
)

__all__ = [
    # Track sync
    "load_track_list",
    # Change reports
    "save_changes_report",
    # HTML reports
    "save_detailed_dry_run_report",
    "save_html_report",
    "save_track_map_to_csv",
    "sync_track_list_with_current",
]


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

    # Convert TrackDict models to plain dictionaries with string values
    track_dicts = [{field: str(track.get(field) or "") for field in TRACK_FIELDNAMES} for track in tracks]
    _save_csv(
        track_dicts,
        TRACK_FIELDNAMES,
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


def _print_change_line(
    console: Console,
    item: str,
    old_val: str,
    new_val: str,
    *,
    suffix: str = "",
    highlight: bool = True,
) -> None:
    """Print a single change line with consistent formatting."""
    old_display = old_val or "(empty)"
    suffix_str = f" [dim]{suffix}[/dim]" if suffix else ""
    if highlight:
        console.print(
            f"  {item}{suffix_str}: [dim]{old_display}[/dim] → [bold yellow]{new_val}[/bold yellow]"
        )
    else:
        console.print(f"  {item}{suffix_str}: {old_display} → {new_val}")


def _render_genre_change(console: Console, record: dict[str, Any]) -> None:
    """Render genre update change."""
    artist = record.get(Key.ARTIST, "")
    track = record.get(Key.TRACK_NAME, "")
    album = record.get(Key.ALBUM, "")
    item = f"{artist} - {track or album}"
    _print_change_line(
        console, item, record.get(Key.OLD_GENRE, ""), record.get(Key.NEW_GENRE, "")
    )


def _render_year_change(console: Console, record: dict[str, Any]) -> None:
    """Render year update change."""
    artist = record.get(Key.ARTIST, "")
    album = record.get(Key.ALBUM, "")
    old_val = record.get(Key.OLD_YEAR, "")
    new_val = record.get(Key.NEW_YEAR, "")
    item = f"{artist} - {album}"
    _print_change_line(console, item, old_val, new_val, highlight=(old_val != new_val))


def _render_name_change(console: Console, record: dict[str, Any]) -> None:
    """Render name change (track or album)."""
    artist = record.get(Key.ARTIST, "")
    if record.get(Key.OLD_TRACK_NAME):
        old_val = record.get(Key.OLD_TRACK_NAME, "")
        new_val = record.get(Key.NEW_TRACK_NAME, "")
        item = f"{artist} - Track"
    else:
        old_val = record.get(Key.OLD_ALBUM_NAME, "")
        new_val = record.get(Key.NEW_ALBUM_NAME, "")
        item = f"{artist} - Album"
    _print_change_line(console, item, old_val, new_val)


def _render_metadata_cleaning(console: Console, record: dict[str, Any]) -> None:
    """Render metadata cleaning change."""
    artist = record.get(Key.ARTIST, "")
    album = record.get(Key.ALBUM, "")
    track_name = record.get(Key.TRACK_NAME, "")

    # Display album cleaning if changed
    old_album = record.get(Key.OLD_ALBUM_NAME, "")
    new_album = record.get(Key.NEW_ALBUM_NAME, "")
    if old_album != new_album:
        item = f"{artist} - {track_name}"
        _print_change_line(console, item, old_album, new_album, suffix="(album)")

    # Display track name cleaning if changed
    old_track = record.get(Key.OLD_TRACK_NAME, "")
    new_track = record.get(Key.NEW_TRACK_NAME, "")
    if old_track != new_track:
        item = f"{artist} - {album}"
        _print_change_line(console, item, old_track, new_track, suffix="(track)")


# Mapping of change types to their render functions
_CHANGE_RENDERERS: dict[str, Callable[[Console, dict[str, Any]], None]] = {
    "genre_update": _render_genre_change,
    "year_update": _render_year_change,
    "name_change": _render_name_change,
    "metadata_cleaning": _render_metadata_cleaning,
}


def _render_compact_change(console: Console, change_type: str, record: dict[str, Any]) -> None:
    """Render a single change record in compact arrow format."""
    if renderer := _CHANGE_RENDERERS.get(change_type):
        renderer(console, record)

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


