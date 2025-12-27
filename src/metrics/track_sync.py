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
from typing import TYPE_CHECKING, TypedDict

from core.models.types import TrackDict
from metrics.csv_utils import TRACK_FIELDNAMES, save_csv

if TYPE_CHECKING:
    from collections.abc import Sequence

    from core.models.types import (
        AppleScriptClientProtocol,
        CacheServiceProtocol,
    )


class ParsedTrackFields(TypedDict):
    """Typed dict for parsed AppleScript track fields.

    Contains the 4 fields extracted from AppleScript output that are
    needed for track synchronization and delta detection.

    Note: 'year' is the CURRENT year in Music.app (position 9 in AppleScript).
    This may be used to populate TrackDict.year_before_mgu for new tracks.
    """

    date_added: str
    last_modified: str
    track_status: str
    year: str


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
        year=track_data.get("year") or None,  # Current year for delta detection
        date_added=track_data["date_added"] or None,
        last_modified=track_data.get("last_modified", "") or None,
        track_status=track_data["track_status"] or None,
        # Backward compat: read both old and new field names (auto-migration)
        year_before_mgu=(track_data.get("year_before_mgu") or track_data.get("old_year") or "").strip() or None,
        year_set_by_mgu=(track_data.get("year_set_by_mgu") or track_data.get("new_year") or "").strip() or None,
    )


def load_track_list(csv_path: str) -> dict[str, TrackDict]:
    """Load the track list from the CSV file into a dictionary.

    The track ID is used as the key.
    Reads columns: id, name, artist, album, genre, date_added, track_status, year_before_mgu, year_set_by_mgu.

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
    """Return a mapping album_key -> year_set_by_mgu for albums already processed in CSV."""
    processed: dict[str, str] = {}
    for track in csv_map.values():
        artist = (track.artist or "").strip()
        album = (track.album or "").strip()
        year_set_by_mgu = (track.year_set_by_mgu or "").strip()
        if artist and album and year_set_by_mgu:
            album_key = cache_service.generate_album_key(artist, album)
            processed[album_key] = year_set_by_mgu
    return processed


async def _build_musicapp_track_map(
    all_tracks: Sequence[TrackDict],
    processed_albums: dict[str, str],
    cache_service: CacheServiceProtocol,
    partial_sync: bool,
    error_logger: logging.Logger,
) -> dict[str, TrackDict]:
    """Build normalized map {track_id: TrackDict} from Music.app tracks.

    Handles partial_sync logic and ensures year consistency with cache & CSV.
    """
    track_map: dict[str, TrackDict] = {}
    for track in all_tracks:
        # Validate track ID
        track_id = (track.id or "").strip()
        if not track_id:
            continue

        # Normalize basic fields
        artist = (track.artist or "").strip()
        album = (track.album or "").strip()
        album_key = cache_service.generate_album_key(artist, album)

        # Ensure year fields are properly initialized
        _normalize_track_year_fields(track)

        # Handle partial sync with cache coordination
        if partial_sync:
            await _handle_partial_sync_cache(track, processed_albums, cache_service, album_key, artist, album, error_logger)

        # Create normalized track dictionary
        track_map[track_id] = _create_normalized_track_dict(track, track_id, artist, album)
    return track_map


def _normalize_track_year_fields(track: TrackDict) -> None:
    """Ensure mandatory year fields exist in track."""
    if track.year_before_mgu is None:
        track.year_before_mgu = ""
    if track.year_set_by_mgu is None:
        track.year_set_by_mgu = ""


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

    track.year_set_by_mgu = processed_albums[album_key]
    try:
        cached_year = await cache_service.get_album_year_from_cache(artist, album)
        if not cached_year or cached_year != track.year_set_by_mgu:
            await cache_service.store_album_year_in_cache(artist, album, track.year_set_by_mgu)
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
        year=(track.year or "").strip(),  # Current year for delta detection
        date_added=(track.date_added or "").strip(),
        track_status=(track.track_status or "").strip(),
        year_before_mgu=(track.year_before_mgu or "").strip(),
        year_set_by_mgu=(track.year_set_by_mgu or "").strip(),
    )


def _get_musicapp_syncable_fields() -> list[str]:
    """Fields that sync FROM Music.app TO CSV during resync.

    Note: year_before_mgu and year_set_by_mgu are EXCLUDED because:
    - They are tracking fields managed by year_batch.py, not sync
    - AppleScript doesn't provide them (only Music.app's current year)
    - During sync, we preserve CSV's historical tracking data

    However, _merge_musicapp_into_csv() will initialize empty year_before_mgu
    from musicapp_track.year to prevent redundant fetches in sync_track_list_with_current.
    See Issue #126 for context.
    """
    return [
        "name",
        "artist",
        "album",
        "genre",
        "year",  # Current year for delta detection
        "date_added",
        "track_status",
        # year_before_mgu/year_set_by_mgu deliberately excluded - preserved from CSV
    ]


def _track_fields_differ(
    csv_track: TrackDict,
    musicapp_track: TrackDict,
    fields: list[str],
) -> bool:
    """Check if CSV track differs from Music.app track in specified fields."""
    return any(getattr(csv_track, field, "") != getattr(musicapp_track, field, "") for field in fields)


def _update_csv_track_from_musicapp(
    csv_track: TrackDict,
    musicapp_track: TrackDict,
    fields: list[str],
) -> None:
    """Update CSV track with values from Music.app track.

    Only updates fields that exist on both tracks and have non-None values
    in Music.app track. Logs warning for missing fields.
    """
    for field in fields:
        missing_in = []
        if not hasattr(csv_track, field):
            missing_in.append("csv_track")
        if not hasattr(musicapp_track, field):
            missing_in.append("musicapp_track")
        if missing_in:
            logging.warning(
                "Field '%s' does not exist on %s. Skipping update for this field.",
                field,
                " and ".join(missing_in),
            )
            continue
        musicapp_value = getattr(musicapp_track, field)
        if musicapp_value is not None:
            setattr(csv_track, field, musicapp_value)


def _merge_musicapp_into_csv(
    musicapp_tracks: dict[str, TrackDict],
    csv_tracks: dict[str, TrackDict],
) -> int:
    """Merge Music.app tracks into CSV tracks dict.

    - New tracks (in Music.app but not CSV): added to csv_tracks
    - Existing tracks: CSV updated with Music.app values for syncable fields
    - Tracking fields (year_before_mgu, year_set_by_mgu): preserved from CSV if present,
      otherwise initialized from Music.app's current year

    Returns count of added/updated tracks.
    """
    updated = 0
    syncable_fields = _get_musicapp_syncable_fields()

    for track_id, musicapp_track in musicapp_tracks.items():
        # Add new track if not in CSV
        if track_id not in csv_tracks:
            csv_tracks[track_id] = musicapp_track
            updated += 1
            continue

        # Update existing CSV track if fields differ
        csv_track = csv_tracks[track_id]
        if _track_fields_differ(csv_track, musicapp_track, syncable_fields):
            _update_csv_track_from_musicapp(csv_track, musicapp_track, syncable_fields)
            updated += 1

        # Initialize empty year_before_mgu from Music.app's current year
        # This prevents a redundant second fetch in sync_track_list_with_current
        if not csv_track.year_before_mgu and musicapp_track.year:
            csv_track.year_before_mgu = musicapp_track.year

    return updated


def _build_osascript_command(script_path: str, artist_filter: str | None) -> list[str]:
    """Build osascript command with optional artist filter."""
    cmd = ["osascript", script_path]
    if artist_filter:
        cmd.append(artist_filter)
    return cmd


# AppleScript output field count constants
# Format: id, name, artist, album_artist, album, genre, date_added, modification_date, status, year, release_year, ""
# Note: Last field is empty placeholder (AppleScript outputs ""). year_set_by_mgu is CSV-only, set by year_batch.py.
_FIELD_COUNT_WITH_ALBUM_ARTIST = 12
_FIELD_COUNT_WITHOUT_ALBUM_ARTIST = 11

# Field indices for format with album_artist (12 fields)
# 0:id, 1:name, 2:artist, 3:album_artist, 4:album, 5:genre, 6:date_added, 7:mod_date, 8:status, 9:year, 10:release_year, 11:""
_DATE_ADDED_IDX_12 = 6
_MODIFICATION_DATE_IDX_12 = 7
_STATUS_IDX_12 = 8
_YEAR_IDX_12 = 9

# Field indices for format without album_artist (11 fields)
# 0:id, 1:name, 2:artist, 3:album, 4:genre, 5:date_added, 6:mod_date, 7:status, 8:year, 9:release_year, 10:""
_DATE_ADDED_IDX_11 = 5
_MODIFICATION_DATE_IDX_11 = 6
_STATUS_IDX_11 = 7
_YEAR_IDX_11 = 8


def _resolve_field_indices(field_count: int) -> tuple[int, int, int, int] | None:
    """Return indices for date_added, modification_date, status, and year columns."""
    if field_count == _FIELD_COUNT_WITH_ALBUM_ARTIST:
        return _DATE_ADDED_IDX_12, _MODIFICATION_DATE_IDX_12, _STATUS_IDX_12, _YEAR_IDX_12
    if field_count == _FIELD_COUNT_WITHOUT_ALBUM_ARTIST:
        return _DATE_ADDED_IDX_11, _MODIFICATION_DATE_IDX_11, _STATUS_IDX_11, _YEAR_IDX_11
    return None


_MISSING_VALUE_PLACEHOLDER = "missing value"


def _sanitize_applescript_field(value: str) -> str:
    """Convert AppleScript 'missing value' placeholder to empty string."""
    return "" if value == _MISSING_VALUE_PLACEHOLDER else value


def _parse_single_track_line(
    fields: list[str],
    indices: tuple[int, int, int, int],
) -> ParsedTrackFields:
    """Parse fields into ParsedTrackFields using resolved indices."""
    date_added_idx, mod_date_idx, status_idx, year_idx = indices
    return {
        "date_added": _sanitize_applescript_field(fields[date_added_idx]),
        "last_modified": _sanitize_applescript_field(fields[mod_date_idx]),
        "track_status": _sanitize_applescript_field(fields[status_idx]),
        "year": _sanitize_applescript_field(fields[year_idx]),
    }


def _parse_osascript_output(raw_output: str) -> dict[str, ParsedTrackFields]:
    """Parse AppleScript output into track cache dictionary.

    Validates field count to detect AppleScript output format changes and logs
    warnings for lines with incorrect field counts to aid debugging.

    Returns:
        Dict mapping track_id to ParsedTrackFields with date_added,
        last_modified, track_status, and year.
    """
    tracks_cache: dict[str, ParsedTrackFields] = {}

    line_separator = chr(29) if chr(29) in raw_output else None
    field_separator = chr(30) if chr(30) in raw_output else "\t"
    # Use strip('\n\r') instead of strip() to preserve trailing tabs (empty fields)
    stripped_output = raw_output.strip("\n\r")
    tracks_data = stripped_output.split(line_separator) if line_separator else stripped_output.splitlines()

    for line_num, track_line in enumerate(tracks_data, start=1):
        if not track_line.strip():
            continue
        fields = track_line.split(field_separator)
        indices = _resolve_field_indices(len(fields))
        if indices is None:
            logging.warning(
                "Track line %d has %d fields, expected 11 or 12. Skipping line: %r",
                line_num,
                len(fields),
                track_line[:100],
            )
            continue
        tracks_cache[fields[0]] = _parse_single_track_line(fields, indices)

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
) -> dict[str, ParsedTrackFields]:
    """Fetch track fields using direct osascript call.

    Args:
        script_path: Path to AppleScript file
        artist_filter: Artist filter to limit query scope

    Returns:
        Dict mapping track_id to ParsedTrackFields with date_added,
        last_modified, track_status, year fields.
    """
    tracks_cache: dict[str, ParsedTrackFields] = {}

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
) -> dict[str, ParsedTrackFields]:
    """Fetch missing track fields via AppleScript if needed for sync operation."""
    tracks_cache: dict[str, ParsedTrackFields] = {}

    has_missing_fields = any(not track.date_added or not track.track_status or not track.year_before_mgu for track in final_list if track.id)

    if has_missing_fields and applescript_client is not None:
        try:
            console_logger.info("Fetching track fields via direct osascript...")

            # Use applescript_client's configured directory instead of hardcoded path
            if applescript_client.apple_scripts_dir:
                script_path = str(Path(applescript_client.apple_scripts_dir) / "fetch_tracks.applescript")
            else:
                # Fallback to relative path if apple_scripts_dir is not available
                script_path = str(Path("applescripts") / "fetch_tracks.applescript")

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
    tracks_cache: dict[str, ParsedTrackFields],
) -> None:
    """Update track with cached fields if they were empty for sync operation.

    Populates date_added, last_modified, track_status from AppleScript cache.
    The last_modified field enables idempotent delta detection by tracking
    when Music.app metadata was last changed.

    For 'year' field: AppleScript returns the CURRENT year in Music.app.
    - Populates track.year for delta detection
    - Populates track.year_before_mgu ONLY if empty (preserves original value for rollback)
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

    if cached_year := cached_fields.get("year"):
        # Always update track.year (current state for delta detection)
        if not track.year:
            track.year = cached_year
        # Set year_before_mgu only if empty (preserve original for rollback/audit)
        if not track.year_before_mgu:
            track.year_before_mgu = cached_year


def _convert_track_to_csv_dict(track: TrackDict) -> dict[str, str]:
    """Convert TrackDict to CSV dictionary format."""
    return {
        "id": track.id or "",
        "name": track.name or "",
        "artist": track.artist or "",
        "album": track.album or "",
        "genre": track.genre or "",
        "year": track.year or "",  # Current year for delta detection
        "date_added": track.date_added or "",
        "last_modified": getattr(track, "last_modified", "") or "",
        "track_status": track.track_status or "",
        "year_before_mgu": track.year_before_mgu or "",
        "year_set_by_mgu": track.year_set_by_mgu or "",
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
        partial_sync: Whether to perform a partial sync (only update year_set_by_mgu if missing).
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

    # 3. Build map of tracks fetched from Music.app
    musicapp_tracks = await _build_musicapp_track_map(
        all_tracks,
        processed_albums,
        cache_service,
        partial_sync,
        error_logger,
    )

    # 4. Merge Music.app tracks into CSV
    added_or_updated = _merge_musicapp_into_csv(musicapp_tracks, csv_map)
    console_logger.info("Added/Updated %s tracks in CSV.", added_or_updated)

    # 5. Remove tracks from CSV that no longer exist in Music.app
    removed_count = len([tid for tid in csv_map if tid not in musicapp_tracks])
    csv_map = {tid: track for tid, track in csv_map.items() if tid in musicapp_tracks}

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
