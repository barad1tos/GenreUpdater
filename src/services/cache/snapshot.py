"""Persistent library snapshot and delta cache management."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.models.protocols import AppleScriptClientProtocol
    from core.models.track_models import AppConfig, LibrarySnapshotConfig

from core.apple_script_names import FETCH_TRACKS_BY_IDS
from core.logger import ensure_directory, spinner
from core.models.cache_types import SNAPSHOT_VERSION, LibraryCacheMetadata, LibraryDeltaCache
from core.models.track_models import TrackDict
from core.tracks.track_delta import FIELD_SEPARATOR, LINE_SEPARATOR, TrackDelta, has_track_changed
from services.cache.json_utils import dumps_json, loads_json

DEFAULT_MAX_AGE_HOURS: int = 24
DEFAULT_COMPRESS_LEVEL: int = 6
JSON_SUFFIX: str = ".json"
GZIP_SUFFIX: str = ".json.gz"
FORCE_SCAN_INTERVAL_DAYS: int = 7

# Minimum expected field count from fetch_tracks.applescript output
MIN_FETCH_TRACKS_FIELDS: int = 11

# Smart Delta force-scan batch settings
DELTA_BATCH_SIZE: int = 200  # tracks per batch for update detection
DELTA_BATCH_TIMEOUT_SECONDS: int = 120  # timeout per batch (generous for 200 IDs)


def _utc_now_naive() -> datetime:
    """Return naive UTC datetime for consistent comparisons.

    Intentionally naive: all snapshot timestamps use naive UTC so that
    comparisons with library mtime (also stripped to naive UTC) are consistent.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class LibrarySnapshotService:
    """Service providing persistent library snapshot and delta caching."""

    def __init__(self, config: AppConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

        snapshot_cfg = config.caching.library_snapshot
        self.enabled = snapshot_cfg.enabled
        self.delta_enabled = snapshot_cfg.delta_enabled
        self.compress = snapshot_cfg.compress
        self.max_age = timedelta(hours=snapshot_cfg.max_age_hours)
        self.compress_level = min(max(snapshot_cfg.compress_level, 1), 9)

        self._base_cache_path = self._resolve_cache_file_path(config, snapshot_cfg)
        self._metadata_path = self._base_cache_path.with_suffix(".meta.json")
        self._delta_path = self._base_cache_path.parent / "library_delta.json"
        self._music_library_path = self._resolve_music_library_path(config)

        # Lock to prevent concurrent snapshot writes
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure directories exist and clean up stale formats."""
        ensure_directory(str(self._base_cache_path.parent), self.logger)
        await asyncio.to_thread(self._ensure_single_cache_format)

    async def load_snapshot(self) -> list[TrackDict] | None:
        """Load snapshot from disk."""
        snapshot_path = self._snapshot_path
        if not snapshot_path.exists():
            return None

        try:
            raw_bytes = await asyncio.to_thread(snapshot_path.read_bytes)
            if self.compress:
                raw_bytes = await asyncio.to_thread(gzip.decompress, raw_bytes)
            payload = loads_json(raw_bytes)
        except (OSError, ValueError) as snapshot_error:
            self.logger.exception("Failed to load library snapshot: %s", snapshot_error)
            return None

        try:
            return self._deserialize_tracks(payload)
        except ValueError as validation_error:
            self.logger.exception("Snapshot payload validation failed: %s", validation_error)
            return None

    async def save_snapshot(self, tracks: Sequence[TrackDict]) -> str:
        """Persist snapshot and return its hash."""
        async with self._write_lock:
            payload = self._prepare_snapshot_payload(tracks)
            serialized = dumps_json(payload)
            if self.compress:
                serialized = await asyncio.to_thread(gzip.compress, serialized, self.compress_level)

            snapshot_hash = self.compute_snapshot_hash(payload)
            snapshot_path = self._snapshot_path

            await asyncio.to_thread(self._write_bytes_atomic, snapshot_path, serialized)
            await asyncio.to_thread(self._ensure_single_cache_format)
            self.logger.info("Saved library snapshot (%d tracks)", len(payload))
            return snapshot_hash

    async def is_snapshot_valid(self) -> bool:
        """Check whether snapshot meets freshness and integrity requirements.

        Priority logic:
        1. If library_mtime unchanged → snapshot valid (ignore age)
        2. If library_mtime changed → check age and other constraints
        """
        # Check metadata existence and version
        metadata = await self.get_snapshot_metadata()
        if not metadata:
            self.logger.warning(
                "Snapshot metadata not found at %s; treating snapshot as invalid",
                self._metadata_path,
            )
            return False

        if metadata.version != SNAPSHOT_VERSION:
            self.logger.warning("Snapshot version mismatch (found %s, expected %s)", metadata.version, SNAPSHOT_VERSION)
            return False

        # Check library modification time (PRIMARY CHECK)
        try:
            library_mtime = await self.get_library_mtime()
        except FileNotFoundError:
            self.logger.warning("Music library path not found; treating snapshot as stale")
            return False

        # If library hasn't changed since snapshot → snapshot is valid regardless of age
        library_unchanged = library_mtime <= metadata.library_mtime
        if library_unchanged:
            self.logger.info(
                "Library unchanged since snapshot; using cached snapshot (age: %s)",
                _utc_now_naive() - metadata.last_full_scan,
            )
        else:
            # Library has changed - log it and proceed with additional checks
            time_diff = library_mtime - metadata.library_mtime
            self.logger.warning(
                "Music library was modified %.1f seconds after snapshot creation",
                time_diff.total_seconds(),
            )

            # Check age limit (only relevant if library changed)
            if self.max_age.total_seconds() > 0:
                age = _utc_now_naive() - metadata.last_full_scan
                if age > self.max_age:
                    self.logger.warning("Snapshot expired: age %s exceeds %s", age, self.max_age)
                    return False

        # Final check: snapshot file exists
        if not self._snapshot_path.exists():
            self.logger.warning("Snapshot file not found at %s", self._snapshot_path)
            return False

        return True

    async def get_snapshot_metadata(self) -> LibraryCacheMetadata | None:
        """Load snapshot metadata."""
        if not self._metadata_path.exists():
            return None

        try:
            raw_bytes = await asyncio.to_thread(self._metadata_path.read_bytes)
            data = loads_json(raw_bytes)
            return LibraryCacheMetadata.from_dict(data)
        except (OSError, KeyError, ValueError) as metadata_error:
            self.logger.warning("Failed to parse snapshot metadata: %s", metadata_error)
            return None

    async def update_snapshot_metadata(self, metadata: LibraryCacheMetadata) -> None:
        """Persist snapshot metadata."""
        data = dumps_json(metadata.to_dict(), indent=True)
        await asyncio.to_thread(self._write_bytes_atomic, self._metadata_path, data)

    async def load_delta(self) -> LibraryDeltaCache | None:
        """Load delta cache."""
        if not self.delta_enabled or not self._delta_path.exists():
            return None

        try:
            raw_bytes = await asyncio.to_thread(self._delta_path.read_bytes)
            data = loads_json(raw_bytes)
            delta = LibraryDeltaCache.from_dict(data)
        except (OSError, KeyError, ValueError) as delta_error:
            self.logger.warning("Failed to load delta cache: %s", delta_error)
            return None

        if delta.should_reset():
            self.logger.info("Delta cache exceeded limits; resetting")
            return None

        return delta

    async def save_delta(self, delta: LibraryDeltaCache) -> None:
        """Persist delta cache."""
        if not self.delta_enabled:
            return

        if delta.should_reset():
            delta.processed_track_ids.clear()
            delta.tracked_since = _utc_now_naive()

        delta_dict = delta.to_dict()
        data = dumps_json(delta_dict, indent=True)
        await asyncio.to_thread(self._write_bytes_atomic, self._delta_path, data)

    async def get_library_mtime(self) -> datetime:
        """Return modification time of the music library file.

        Returns a naive UTC datetime for consistency with snapshot comparisons.
        Uses UTC to prevent false positives on non-UTC local timezones.
        """
        if not self._music_library_path:
            msg = "music_library_path not configured"
            raise FileNotFoundError(msg)

        try:
            stat_result = await asyncio.to_thread(self._music_library_path.stat)
        except OSError as stat_error:
            raise FileNotFoundError(str(stat_error)) from stat_error

        # BUG FIX: Convert to UTC, then strip timezone to match naive datetime format
        # Without tz=UTC, fromtimestamp returns local time (e.g., EET +2), causing
        # false "library changed" detections when compared to UTC-saved snapshots
        return datetime.fromtimestamp(stat_result.st_mtime, tz=UTC).replace(tzinfo=None)

    @staticmethod
    def _parse_raw_track(raw_track: dict[str, Any]) -> TrackDict:
        """Parse raw track dict to TrackDict.

        Note: year_set_by_mgu is a tracking field managed by MGU, not from AppleScript.
        It's initialized to None here and populated by year_batch.py during processing.
        """
        year_value = raw_track.get("year")
        return TrackDict(
            id=raw_track.get("id", ""),
            name=raw_track.get("name", ""),
            artist=raw_track.get("artist", ""),
            album_artist=raw_track.get("album_artist"),
            album=raw_track.get("album", ""),
            genre=raw_track.get("genre"),
            date_added=raw_track.get("date_added"),
            last_modified=raw_track.get("modification_date"),
            track_status=raw_track.get("track_status"),
            year=year_value if year_value and str(year_value).strip() else None,
            release_year=raw_track.get("release_year"),
            year_set_by_mgu=None,  # Tracking field, not from AppleScript
        )

    def _parse_fetch_tracks_output(self, raw_output: str) -> list[dict[str, str]]:
        """Parse AppleScript fetch_tracks.applescript output into track dictionaries.

        Args:
            raw_output: Raw AppleScript output with ASCII 30/29 separators

        Returns:
            List of track dictionaries

        """
        tracks: list[dict[str, str]] = []
        lines = raw_output.split(LINE_SEPARATOR)

        for line in lines:
            if not line.strip():
                continue

            fields = line.split(FIELD_SEPARATOR)

            # Expected fields from AppleScript (fetch_tracks.applescript):
            # id, name, artist, album_artist, album, genre, date_added,
            # modification_date, track_status, year, release_year, ""
            if len(fields) >= MIN_FETCH_TRACKS_FIELDS:
                track = {
                    "id": fields[0],
                    "name": fields[1],
                    "artist": fields[2],
                    "album_artist": fields[3],
                    "album": fields[4],
                    "genre": fields[5],
                    "date_added": fields[6],
                    "modification_date": fields[7],
                    "track_status": fields[8],
                    "year": fields[9],
                    "release_year": fields[10],
                }
                tracks.append(track)
            else:
                self.logger.warning(
                    "Skipping line with insufficient fields (%d < %d): %s",
                    len(fields),
                    MIN_FETCH_TRACKS_FIELDS,
                    line[:100] if len(line) > 100 else line,
                )

        return tracks

    async def compute_smart_delta(
        self,
        applescript_client: AppleScriptClientProtocol,
        force: bool = False,
    ) -> TrackDelta | None:
        """Compute track delta using Hybrid Smart Delta approach.

        Two modes:
        - Fast mode (default): Detects new/removed by ID comparison only (~1-2s)
        - Force mode: Full metadata comparison for manual change detection (~30-60s)

        Force mode triggers when:
        - Force=True (CLI --force)
        - Last force scan was 7+ days ago (weekly auto-force)

        Fast mode (skips full scan) when:
        - First run (nothing to compare against)
        - Force scan was within last 7 days

        Args:
            applescript_client: AppleScriptClient instance for fetching tracks
            force: CLI --force flag

        Returns:
            TrackDelta with new/updated/removed track IDs, or None if snapshot unavailable

        """
        is_force, reason = await self.should_force_scan(force)
        mode_label = "force" if is_force else "fast"
        self.logger.info("Smart Delta [cyan]%s[/cyan] mode: %s", mode_label, reason)

        # Load snapshot
        snapshot_tracks = await self.load_snapshot()
        if not snapshot_tracks:
            self.logger.warning("No snapshot available for Smart Delta")
            return None

        snapshot_map = {str(track.id): track for track in snapshot_tracks}
        snapshot_ids = set(snapshot_map.keys())

        self.logger.info(
            "Loaded snapshot with %d tracks, fetching current IDs...",
            len(snapshot_ids),
        )

        # Fetch ALL current track IDs from Music.app (lightweight, ~1s)
        current_ids_list = await applescript_client.fetch_all_track_ids()
        if not current_ids_list:
            self.logger.warning("Failed to fetch track IDs from Music.app")
            return None

        current_ids = set(current_ids_list)

        # Compute ID differences
        new_ids = sorted(current_ids - snapshot_ids)
        removed_ids = sorted(snapshot_ids - current_ids)

        self.logger.info(
            "ID comparison: %d new, %d removed, %d existing",
            len(new_ids),
            len(removed_ids),
            len(current_ids & snapshot_ids),
        )

        # Updated detection depends on mode
        if is_force:
            updated_ids = await self._detect_updated_tracks(applescript_client, current_ids, snapshot_ids, snapshot_map)
        else:
            self.logger.info("Fast mode: skipping updated detection (trusting snapshot)")
            updated_ids = []

        self.logger.info(
            "Smart Delta (%s): %d new, %d updated, %d removed",
            mode_label,
            len(new_ids),
            len(updated_ids),
            len(removed_ids),
        )

        return TrackDelta(new_ids=new_ids, updated_ids=updated_ids, removed_ids=removed_ids)

    async def _detect_updated_tracks(
        self,
        applescript_client: AppleScriptClientProtocol,
        current_ids: set[str],
        snapshot_ids: set[str],
        snapshot_map: dict[str, TrackDict],
    ) -> list[str]:
        """Detect tracks with changed metadata (force mode only).

        Fetches only common tracks (exist in both current and snapshot) in batches
        and compares metadata to detect changes.

        This is much more efficient than fetching the entire library:
        - Only fetches tracks that could potentially be "updated"
        - New tracks are handled separately (not in common_ids)
        - Removed tracks don't need fetching
        """
        # Only fetch tracks that exist in both - these are the only candidates for "updated"
        common_ids = sorted(current_ids & snapshot_ids)

        if not common_ids:
            self.logger.info("No common tracks to check for updates")
            await self._update_force_scan_time()
            return []

        # Fetch common tracks in batches using fetch_tracks_by_ids.applescript
        current_map: dict[str, TrackDict] = {}
        total_batches = (len(common_ids) + DELTA_BATCH_SIZE - 1) // DELTA_BATCH_SIZE

        self.logger.info(
            "Force mode: fetching %d common tracks in %d batches...",
            len(common_ids),
            total_batches,
        )

        async with spinner(f"Force mode: fetching {len(common_ids)} tracks for update detection..."):
            for batch_index in range(0, len(common_ids), DELTA_BATCH_SIZE):
                batch = common_ids[batch_index : batch_index + DELTA_BATCH_SIZE]
                batch_num = batch_index // DELTA_BATCH_SIZE + 1
                ids_param = ",".join(batch)

                result = await applescript_client.run_script(
                    FETCH_TRACKS_BY_IDS,
                    arguments=[ids_param],
                    timeout=DELTA_BATCH_TIMEOUT_SECONDS,
                )

                if not result:
                    self.logger.warning(
                        "Batch %d/%d returned empty, skipping",
                        batch_num,
                        total_batches,
                    )
                    continue

                raw_tracks = self._parse_fetch_tracks_output(result)
                for raw_track in raw_tracks:
                    try:
                        track_dict = self._parse_raw_track(raw_track)
                        current_map[str(track_dict.id)] = track_dict
                    except (KeyError, ValueError) as parse_error:
                        self.logger.warning("Failed to parse track: %s", parse_error)

        if not current_map:
            self.logger.warning("Force scan: no tracks fetched successfully")
            await self._update_force_scan_time()
            return []

        # Find updated tracks (metadata changed)
        updated_ids = [
            track_id
            for track_id in common_ids
            if track_id in current_map and track_id in snapshot_map and has_track_changed(current_map[track_id], snapshot_map[track_id])
        ]
        self.logger.info(
            "Force scan found %d updated tracks (checked %d/%d common)",
            len(updated_ids),
            len(current_map),
            len(common_ids),
        )

        await self._update_force_scan_time()
        return updated_ids

    def is_enabled(self) -> bool:
        """Check whether snapshot caching is enabled."""
        return self.enabled

    def is_delta_enabled(self) -> bool:
        """Return whether delta caching is enabled."""
        return self.enabled and self.delta_enabled

    def clear_snapshot(self) -> bool:
        """Delete the snapshot file to force fresh data fetch from Music.app.

        Returns:
            True if snapshot was deleted, False if it didn't exist.
        """
        snapshot_path = self._snapshot_path
        if snapshot_path.exists():
            snapshot_path.unlink()
            self.logger.info("Cleared library snapshot: %s", snapshot_path)
            return True
        return False

    async def should_force_scan(self, force_flag: bool = False) -> tuple[bool, str]:
        """Determine if full metadata scan is needed.

        Force scan triggers when:
        - Force_flag is True (CLI --force)
        - Last force scan was 7+ days ago (weekly auto-force)

        Fast mode (no full scan) when:
        - First run (nothing to compare against)
        - Force scan was within last 7 days

        Args:
            force_flag: CLI --force flag value

        Returns:
            Tuple of (should_force, reason) explaining the decision

        """
        if force_flag:
            return True, "CLI --force flag"

        metadata = await self.get_snapshot_metadata()

        # First run or no previous force scan - use fast mode
        # (nothing to compare against anyway)
        if not metadata or not metadata.last_force_scan_time:
            return False, "first run (use --force to detect manual edits)"

        last_scan = datetime.fromisoformat(metadata.last_force_scan_time)
        # Normalize to naive (strip timezone if present) for comparison with UTC now.
        if last_scan.tzinfo is not None:
            last_scan = last_scan.replace(tzinfo=None)
        now = _utc_now_naive()
        days_since = (now - last_scan).days

        # Weekly auto-force for manual edit detection
        if days_since >= FORCE_SCAN_INTERVAL_DAYS:
            return True, f"weekly scan ({days_since} days since last force)"

        return False, f"fast mode ({days_since}d since last force scan)"

    async def _update_force_scan_time(self) -> None:
        """Update metadata with current force scan timestamp."""
        metadata = await self.get_snapshot_metadata()
        if metadata:
            metadata.last_force_scan_time = _utc_now_naive().isoformat()
            await self.update_snapshot_metadata(metadata)

    @staticmethod
    def compute_snapshot_hash(payload: Sequence[dict[str, Any]]) -> str:
        """Compute deterministic hash for snapshot payload."""
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    # Internal helpers

    @staticmethod
    def _prepare_snapshot_payload(tracks: Sequence[TrackDict]) -> list[dict[str, Any]]:
        return [track.model_dump(mode="json") for track in tracks]

    @staticmethod
    def _deserialize_tracks(payload: Any) -> list[TrackDict]:
        if not isinstance(payload, list):
            msg = "Snapshot payload must be a list"
            raise TypeError(msg)

        tracks: list[TrackDict] = []
        for item in payload:
            if isinstance(item, TrackDict):
                tracks.append(item)
                continue
            if isinstance(item, Mapping):
                try:
                    tracks.append(TrackDict(**dict(item)))
                except (TypeError, ValueError) as exc:
                    message = "Invalid snapshot entry"
                    raise TypeError(message) from exc
                continue
            msg = f"Invalid snapshot entry type: {type(item)}"
            raise TypeError(msg)
        return tracks

    def _write_bytes_atomic(self, target_path: Path, data: bytes) -> None:
        ensure_directory(str(target_path.parent), self.logger)
        temp_path: Path | None = None
        success = False
        try:
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=target_path.parent) as temp_file:
                temp_file.write(data)
                temp_path = Path(temp_file.name)
            temp_path.replace(target_path)
            success = True
        finally:
            if not success and temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def _ensure_single_cache_format(self) -> None:
        plain = self._base_cache_path.with_suffix(JSON_SUFFIX)
        compressed = self._base_cache_path.with_suffix(GZIP_SUFFIX)

        if self.compress:
            if plain.exists():
                try:
                    plain.unlink()
                except OSError as removal_error:
                    self.logger.warning("Failed to remove plain snapshot file %s: %s", plain, removal_error)
        elif compressed.exists():
            try:
                compressed.unlink()
            except OSError as removal_error:
                self.logger.warning("Failed to remove compressed snapshot file %s: %s", compressed, removal_error)

    @property
    def _snapshot_path(self) -> Path:
        return self._base_cache_path.with_suffix(GZIP_SUFFIX) if self.compress else self._base_cache_path.with_suffix(JSON_SUFFIX)

    @staticmethod
    def _resolve_cache_file_path(config: AppConfig, snapshot_cfg: LibrarySnapshotConfig) -> Path:
        raw_path = str(snapshot_cfg.cache_file)
        expanded = Path(os.path.expandvars(raw_path)).expanduser()
        if not expanded.is_absolute():
            logs_base = config.logs_base_dir or os.getenv("LOGS_BASE_DIR") or ""
            expanded = Path(logs_base).expanduser() / expanded if logs_base else Path.cwd() / expanded
        if expanded.suffix == ".gz":
            expanded = expanded.with_suffix(JSON_SUFFIX)
        if expanded.suffix != JSON_SUFFIX:
            expanded = expanded.with_suffix(JSON_SUFFIX)
        return expanded

    @staticmethod
    def _resolve_music_library_path(config: AppConfig) -> Path | None:
        library_path = config.music_library_path
        if not library_path:
            return None
        resolved = Path(os.path.expandvars(str(library_path))).expanduser()
        if not resolved.is_absolute():
            try:
                resolved = resolved.resolve()
            except OSError:
                resolved = resolved.absolute()
        return resolved
