"""Track List Synchronization Module.

This module handles track list persistence and synchronization between
the Music.app library and CSV storage, including AppleScript-based field fetching.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from core.models.types import TrackDict
from metrics.csv_utils import TRACK_FIELDNAMES, save_csv

if TYPE_CHECKING:
    from collections.abc import Sequence

    from core.models.types import (
        AppleScriptClientProtocol,
        CacheServiceProtocol,
    )


# Note: TRACK_FIELDNAMES is imported from csv_utils for shared fieldname definition


def _validate_csv_header(
    reader: csv.DictReader[str],
    expected_fieldnames: list[str],
    csv_path: str,
    logger: logging.Logger,
) -> list[str]:
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


def _create_track_from_row(
    row: dict[str, str],
    fields_to_read: list[str],
    expected_fieldnames: list[str],
) -> TrackDict | None:
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
    """Load the track list from the CSV file into a dictionary.

    The track ID is used as the key.
    Reads columns: id, name, artist, album, genre, date_added, track_status, old_year, new_year.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        Dictionary of track dictionaries.

    """
    # Import here to avoid circular imports

    track_map: dict[str, TrackDict] = {}
    if not Path(csv_path).exists():
        return track_map

    logger = logging.getLogger("console_logger")
    expected_fieldnames = TRACK_FIELDNAMES

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
    # Import here to avoid circular imports

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


def _create_normalized_track_dict(
    track: TrackDict,
    tid: str,
    artist: str,
    album: str,
) -> TrackDict:
    """Create normalized TrackDict with clean field values."""
    return TrackDict(
        id=tid,
        name=(track.name or "").strip(),
        artist=artist,
        album=album,
        genre=(track.genre or "").strip(),
        date_added=(track.date_added or "").strip(),
        track_status=(track.track_status or "").strip(),
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


def _check_if_track_needs_update(
    old_data: TrackDict,
    new_data: TrackDict,
    fields: list[str],
) -> bool:
    """Check if a track needs updating based on field differences."""
    return any(getattr(old_data, field, "") != getattr(new_data, field, "") for field in fields)


def _update_existing_track_fields(
    old_data: TrackDict,
    new_data: TrackDict,
    fields: list[str],
) -> None:
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


# AppleScript output field count constants
_FIELD_COUNT_WITH_ALBUM_ARTIST = 11
_FIELD_COUNT_WITHOUT_ALBUM_ARTIST = 10

# Field indices for format with album_artist (11 fields)
_DATE_ADDED_IDX_11 = 6
_STATUS_IDX_11 = 7
_OLD_YEAR_IDX_11 = 8

# Field indices for format without album_artist (10 fields)
_DATE_ADDED_IDX_10 = 5
_STATUS_IDX_10 = 6
_OLD_YEAR_IDX_10 = 7


def _resolve_field_indices(field_count: int) -> tuple[int, int, int] | None:
    """Return indices for date_added, status, and old_year columns."""
    if field_count == _FIELD_COUNT_WITH_ALBUM_ARTIST:
        return _DATE_ADDED_IDX_11, _STATUS_IDX_11, _OLD_YEAR_IDX_11
    if field_count == _FIELD_COUNT_WITHOUT_ALBUM_ARTIST:
        return _DATE_ADDED_IDX_10, _STATUS_IDX_10, _OLD_YEAR_IDX_10
    return None


def _parse_osascript_output(raw_output: str) -> dict[str, dict[str, str]]:
    """Parse AppleScript output into track cache dictionary.

    Validates field count to detect AppleScript output format changes and logs
    warnings for lines with incorrect field counts to aid debugging.
    """
    tracks_cache: dict[str, dict[str, str]] = {}

    line_separator = chr(29) if chr(29) in raw_output else None
    field_separator = chr(30) if chr(30) in raw_output else "\t"
    tracks_data = raw_output.strip().split(line_separator) if line_separator else raw_output.strip().splitlines()

    for line_num, track_line in enumerate(tracks_data, start=1):
        if not track_line.strip():  # Skip empty lines
            continue
        fields = track_line.split(field_separator)
        indices = _resolve_field_indices(len(fields))
        if indices is None:
            logging.warning(
                "Track line %d has %d fields, expected 10 or 11. Skipping line: %r",
                line_num,
                len(fields),
                track_line[:100],  # Truncate long lines for readability
            )
            continue
        date_added_index, status_index, old_year_index = indices
        track_id = fields[0]
        # Fields order: ID, Name, Artist, Album, Genre, DateAdded, TrackStatus, Year, ReleaseYear, NewYear
        missing_value = "missing value"
        tracks_cache[track_id] = {
            "date_added": (fields[date_added_index] if fields[date_added_index] != missing_value else ""),
            "track_status": (fields[status_index] if fields[status_index] != missing_value else ""),
            "old_year": (fields[old_year_index] if fields[old_year_index] != missing_value else ""),
        }

    return tracks_cache


def _handle_osascript_error(
    process_returncode: int,
    stdout: bytes | None,
    stderr: bytes | None,
) -> None:
    """Handle and log osascript execution errors."""
    error_msg = stderr.decode() if stderr else "No error message"
    print(f"DEBUG: osascript failed with return code {process_returncode}: {error_msg}")
    print(f"DEBUG: stdout was: {stdout.decode() if stdout else 'None'}")
    logging.getLogger(__name__).warning("osascript failed (return code %d): %s", process_returncode, error_msg)


async def _execute_osascript_process(
    cmd: list[str],
) -> tuple[int, bytes | None, bytes | None]:
    """Execute osascript subprocess and return results."""
    process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = await process.communicate()
    return (process.returncode or 0), stdout, stderr


async def _fetch_track_fields_direct(
    script_path: str,
    artist_filter: str | None,
) -> dict[str, dict[str, str]]:
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
    final_list: list[TrackDict],
    applescript_client: AppleScriptClientProtocol | None,
    console_logger: logging.Logger,
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
            console_logger.info(
                "Using osascript with script=%s, filter=%s",
                script_path,
                artist_filter or "ALL",
            )

            tracks_cache = await _fetch_track_fields_direct(script_path, artist_filter)
            console_logger.info("Cached %d track records via osascript", len(tracks_cache))
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as e:
            console_logger.warning("Failed to fetch track fields via osascript: %s", e)
        except (AttributeError, ValueError, IndexError) as e:
            console_logger.exception("Parsing or process error fetching track fields via osascript: %s", e)

    return tracks_cache


def _update_track_with_cached_fields_for_sync(
    track: TrackDict,
    tracks_cache: dict[str, dict[str, str]],
) -> None:
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
        console_logger.info(
            "Removed %s tracks from CSV that no longer exist in Music.app",
            removed_count,
        )

    # Generate the final list from the updated csv_map and write to CSV
    final_list = list(csv_map.values())
    console_logger.info("Final CSV track count after sync: %s", len(final_list))

    # Define the fieldnames for the output CSV file
    fieldnames = TRACK_FIELDNAMES

    # Convert TrackDict to dict[str, str] for save_csv with proper field mapping
    track_dicts: list[dict[str, str]] = []
    missing_fields_count = 0

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
        console_logger.info(
            "Filled missing fields for %d tracks via AppleScript cache",
            missing_fields_count,
        )
    save_csv(track_dicts, fieldnames, csv_path, console_logger, error_logger, "tracks")


def save_track_map_to_csv(
    track_map: dict[str, TrackDict],
    csv_path: str,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> None:
    """Persist the provided track map to CSV using standard field ordering."""
    sorted_tracks = sorted(track_map.values(), key=lambda t: t.id)
    track_dicts = [_convert_track_to_csv_dict(track) for track in sorted_tracks]
    fieldnames = TRACK_FIELDNAMES
    save_csv(track_dicts, fieldnames, csv_path, console_logger, error_logger, "tracks")
