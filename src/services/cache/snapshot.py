"""Persistent library snapshot and delta cache management."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.models.protocols import AppleScriptClientProtocol

from src.services.cache.json_utils import dumps_json, loads_json
from src.services.delta import TrackDelta, compute_track_delta
from src.core.logger import ensure_directory
from src.core.models.track import TrackDict

SNAPSHOT_VERSION = "1.0"
DEFAULT_MAX_AGE_HOURS = 24
DELTA_MAX_TRACKED_IDS = 50_000
DELTA_MAX_AGE = timedelta(days=7)
DEFAULT_COMPRESS_LEVEL = 6
JSON_SUFFIX = ".json"
GZIP_SUFFIX = ".json.gz"


def _now() -> datetime:
    """Return naive UTC datetime for consistency."""
    utc_now = datetime.now(UTC)
    return utc_now.replace(tzinfo=None)


@dataclass(slots=True)
class LibraryCacheMetadata:
    """Metadata describing the stored snapshot."""

    last_full_scan: datetime
    library_mtime: datetime
    track_count: int
    snapshot_hash: str
    version: str = SNAPSHOT_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata to a JSON-friendly dict."""
        return {
            "version": self.version,
            "last_full_scan": self.last_full_scan.isoformat(),
            "library_mtime": self.library_mtime.isoformat(),
            "track_count": self.track_count,
            "snapshot_hash": self.snapshot_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LibraryCacheMetadata:
        """Create metadata from stored json."""
        return cls(
            version=data.get("version", SNAPSHOT_VERSION),
            last_full_scan=datetime.fromisoformat(data["last_full_scan"]),
            library_mtime=datetime.fromisoformat(data["library_mtime"]),
            track_count=int(data["track_count"]),
            snapshot_hash=str(data["snapshot_hash"]),
        )


@dataclass(slots=True)
class LibraryDeltaCache:
    """Delta cache tracking incremental processing state."""

    last_run: datetime
    processed_track_ids: set[str] = field(default_factory=set)
    field_hashes: dict[str, str] = field(default_factory=dict)
    tracked_since: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to disk format."""
        return {
            "last_run": self.last_run.isoformat(),
            "processed_track_ids": sorted(self.processed_track_ids),
            "field_hashes": dict(self.field_hashes),
            "tracked_since": (self.tracked_since or self.last_run).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LibraryDeltaCache:
        """Deserialize delta cache."""
        tracked_since_raw = data.get("tracked_since")
        tracked_since = datetime.fromisoformat(tracked_since_raw) if tracked_since_raw else None
        return cls(
            last_run=datetime.fromisoformat(data["last_run"]),
            processed_track_ids=set(map(str, data.get("processed_track_ids", []))),
            field_hashes=dict(data.get("field_hashes", {})),
            tracked_since=tracked_since,
        )

    def should_reset(self, *, now: datetime | None = None) -> bool:
        """Return True if delta should be cleared because of size or age limits."""
        current_time = now or _now()
        if len(self.processed_track_ids) >= DELTA_MAX_TRACKED_IDS:
            return True
        tracked_ref = self.tracked_since or self.last_run
        return current_time - tracked_ref > DELTA_MAX_AGE

    def add_processed_ids(self, track_ids: Iterable[str]) -> None:
        """Add processed track identifiers while respecting limits."""
        current_time = _now()
        if self.should_reset(now=current_time):
            self.processed_track_ids.clear()
            self.tracked_since = current_time

        self.processed_track_ids.update(map(str, track_ids))
        if self.tracked_since is None:
            self.tracked_since = current_time


class LibrarySnapshotService:
    """Service providing persistent library snapshot and delta caching."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

        options = self._resolve_options(config)
        self.enabled = bool(options.get("enabled", True))
        self.delta_enabled = bool(options.get("delta_enabled", True))
        self.compress = bool(options.get("compress", False))
        self.max_age = timedelta(hours=int(options.get("max_age_hours", DEFAULT_MAX_AGE_HOURS)))
        compress_level = int(options.get("compress_level", DEFAULT_COMPRESS_LEVEL))
        self.compress_level = min(max(compress_level, 1), 9)

        self._base_cache_path = self._resolve_cache_file_path(config, options)
        self._metadata_path = self._base_cache_path.with_suffix(".meta.json")
        self._delta_path = self._base_cache_path.parent / "library_delta.json"
        self._music_library_path = self._resolve_music_library_path(config, options)

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
        except (OSError, ValueError) as exc:
            self.logger.exception("Failed to load library snapshot: %s", exc)
            return None

        try:
            return self._deserialize_tracks(payload)
        except ValueError as exc:
            self.logger.exception("Snapshot payload validation failed: %s", exc)
            return None

    async def save_snapshot(self, tracks: Sequence[TrackDict]) -> str:
        """Persist snapshot and return its hash."""
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
            self.logger.warning("Snapshot metadata not found; treating snapshot as invalid")
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
                "✓ Library unchanged since snapshot creation; using cached snapshot (age: %s)",
                _now() - metadata.last_full_scan,
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
                age = _now() - metadata.last_full_scan
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
        except (OSError, KeyError, ValueError) as exc:
            self.logger.warning("Failed to parse snapshot metadata: %s", exc)
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
        except (OSError, KeyError, ValueError) as exc:
            self.logger.warning("Failed to load delta cache: %s", exc)
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
            delta.tracked_since = _now()

        delta_dict = delta.to_dict()
        data = dumps_json(delta_dict, indent=True)
        await asyncio.to_thread(self._write_bytes_atomic, self._delta_path, data)

    async def get_library_mtime(self) -> datetime:
        """Return modification time of the music library file."""
        if not self._music_library_path:
            msg = "music_library_path not configured"
            raise FileNotFoundError(msg)

        try:
            stat_result = await asyncio.to_thread(self._music_library_path.stat)
        except OSError as exc:
            raise FileNotFoundError(str(exc)) from exc

        return datetime.fromtimestamp(stat_result.st_mtime)

    async def compute_smart_delta(
        self,
        applescript_client: AppleScriptClientProtocol,
        batch_size: int = 1000,
    ) -> TrackDelta | None:
        """Compute track delta using Smart Delta approach (fetch by IDs).

        This method:
        1. Loads the snapshot from disk
        2. Fetches current metadata for all track IDs from Music.app
        3. Computes delta between current and snapshot

        Args:
            applescript_client: AppleScriptClient instance for fetching tracks
            batch_size: Number of track IDs to fetch per batch

        Returns:
            TrackDelta with new/updated/removed track IDs, or None if snapshot unavailable

        """
        self.logger.info("🔍 Computing Smart Delta...")

        # Load snapshot
        snapshot_tracks = await self.load_snapshot()
        if not snapshot_tracks:
            self.logger.warning("⚠️  No snapshot available for Smart Delta")
            return None

        snapshot_map = {str(track.id): track for track in snapshot_tracks}
        track_ids = list(snapshot_map.keys())

        self.logger.info(
            "✓ Loaded snapshot with %d tracks, fetching current metadata...",
            len(track_ids),
        )

        # Fetch current tracks by ID
        raw_tracks = await applescript_client.fetch_tracks_by_ids(track_ids, batch_size=batch_size)

        # Convert dict to TrackDict
        current_tracks: list[TrackDict] = []
        for raw_track in raw_tracks:
            try:
                year_value = raw_track.get("year")
                track_dict = TrackDict(
                    id=raw_track.get("id", ""),
                    name=raw_track.get("name", ""),
                    artist=raw_track.get("artist", ""),
                    album_artist=raw_track.get("album_artist"),
                    album=raw_track.get("album", ""),
                    genre=raw_track.get("genre"),
                    date_added=raw_track.get("date_added"),
                    track_status=raw_track.get("track_status"),
                    year=year_value if year_value and year_value.strip() else None,
                    release_year=raw_track.get("release_year"),
                    new_year=raw_track.get("new_year"),
                    last_modified=None,  # Not available from fetch_by_ids
                )
                current_tracks.append(track_dict)
            except (KeyError, ValueError) as exc:
                self.logger.warning("⚠️  Failed to parse track %s: %s", raw_track.get("id"), exc)
                continue

        self.logger.info("✓ Fetched %d tracks from Music.app", len(current_tracks))

        # Compute delta
        delta = compute_track_delta(current_tracks, snapshot_map)

        self.logger.info(
            "✓ Smart Delta computed: %d new, %d updated, %d removed",
            len(delta.new_ids),
            len(delta.updated_ids),
            len(delta.removed_ids),
        )

        return delta

    def is_enabled(self) -> bool:
        """Check whether snapshot caching is enabled."""
        return self.enabled

    def is_delta_enabled(self) -> bool:
        """Return whether delta caching is enabled."""
        return self.enabled and self.delta_enabled

    @staticmethod
    def compute_snapshot_hash(payload: Sequence[dict[str, Any]]) -> str:
        """Compute deterministic hash for snapshot payload."""
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    # -------------------------- Internal helpers -------------------------- #

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
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=target_path.parent) as temp:
            temp.write(data)
            temp_path = Path(temp.name)
        try:
            temp_path.replace(target_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def _ensure_single_cache_format(self) -> None:
        plain = self._base_cache_path.with_suffix(JSON_SUFFIX)
        compressed = self._base_cache_path.with_suffix(GZIP_SUFFIX)

        if self.compress:
            if plain.exists():
                try:
                    plain.unlink()
                except OSError as exc:
                    self.logger.warning("Failed to remove plain snapshot file %s: %s", plain, exc)
        elif compressed.exists():
            try:
                compressed.unlink()
            except OSError as exc:
                self.logger.warning("Failed to remove compressed snapshot file %s: %s", compressed, exc)

    @property
    def _snapshot_path(self) -> Path:
        return self._base_cache_path.with_suffix(GZIP_SUFFIX) if self.compress else self._base_cache_path.with_suffix(JSON_SUFFIX)

    @staticmethod
    def _resolve_options(config: dict[str, Any]) -> dict[str, Any]:
        caching = config.get("caching", {})
        if isinstance(caching, dict):
            options = caching.get("library_snapshot", {})
            if isinstance(options, dict):
                return options
        return {}

    @staticmethod
    def _resolve_cache_file_path(config: dict[str, Any], options: dict[str, Any]) -> Path:
        raw_path = str(options.get("cache_file", "cache/library_snapshot.json"))
        expanded = Path(os.path.expandvars(raw_path)).expanduser()
        if not expanded.is_absolute():
            logs_base = options.get("logs_base_dir") or config.get("logs_base_dir") or os.getenv("LOGS_BASE_DIR") or ""
            expanded = Path(logs_base).expanduser() / expanded if logs_base else Path.cwd() / expanded
        if expanded.suffix == ".gz":
            expanded = expanded.with_suffix(JSON_SUFFIX)
        if expanded.suffix != JSON_SUFFIX:
            expanded = expanded.with_suffix(JSON_SUFFIX)
        return expanded

    @staticmethod
    def _resolve_music_library_path(config: dict[str, Any], options: dict[str, Any]) -> Path | None:
        library_path = options.get("music_library_path") or config.get("music_library_path")
        if not library_path:
            return None
        resolved = Path(os.path.expandvars(str(library_path))).expanduser()
        if not resolved.is_absolute():
            try:
                resolved = resolved.resolve()
            except OSError:
                resolved = resolved.absolute()
        return resolved
