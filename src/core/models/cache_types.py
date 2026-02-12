"""Pure data types for caching and verification.

These types are defined in core/ because they represent domain data structures
used across multiple layers. Moving them here eliminates layer violations where
core/ was importing from services/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

# Snapshot versioning
SNAPSHOT_VERSION: str = "1.0"

# Delta cache limits
DELTA_MAX_TRACKED_IDS: int = 50_000
DELTA_MAX_AGE: timedelta = timedelta(days=7)


def _utc_now() -> datetime:
    """Return naive UTC datetime for consistency."""
    utc_now = datetime.now(UTC)
    return utc_now.replace(tzinfo=None)


class VerificationReason(StrEnum):
    """Reasons why an album is pending verification."""

    # Original reasons
    NO_YEAR_FOUND = "no_year_found"
    PRERELEASE = "prerelease"

    # Rejection reasons from FALLBACK (Issue #75)
    SUSPICIOUS_YEAR_CHANGE = "suspicious_year_change"
    IMPLAUSIBLE_EXISTING_YEAR = "implausible_existing_year"
    ABSURD_YEAR_NO_EXISTING = "absurd_year_no_existing"
    SPECIAL_ALBUM_COMPILATION = "special_album_compilation"
    SPECIAL_ALBUM_SPECIAL = "special_album_special"
    SPECIAL_ALBUM_REISSUE = "special_album_reissue"
    SUSPICIOUS_ALBUM_NAME = "suspicious_album_name"

    # Additional rejection reasons from year_fallback.py
    VERY_LOW_CONFIDENCE_NO_EXISTING = "very_low_confidence_no_existing"
    IMPLAUSIBLE_MATCHING_YEAR = "implausible_matching_year"
    IMPLAUSIBLE_PROPOSED_YEAR = "implausible_proposed_year"

    @classmethod
    def from_string(cls, value: str) -> VerificationReason:
        """Convert string to VerificationReason, defaulting to NO_YEAR_FOUND."""
        try:
            return cls(value.strip().lower())
        except ValueError:
            return cls.NO_YEAR_FOUND


@dataclass(frozen=True, slots=True)
class PendingAlbumEntry:
    """Immutable entry representing a pending album verification.

    Attributes:
        timestamp: When the album was marked for verification
        artist: Artist name
        album: Album name
        reason: Why the album needs verification
        metadata: JSON-encoded metadata string
        attempt_count: Number of verification attempts made
    """

    timestamp: datetime
    artist: str
    album: str
    reason: VerificationReason
    metadata: str = ""
    attempt_count: int = 0


@dataclass(slots=True)
class AlbumCacheEntry:
    """Album cache entry with timestamp for TTL management."""

    artist: str
    album: str
    year: str
    timestamp: float
    confidence: int = 0  # 0-100, higher = more trustworthy


@dataclass
class LibraryCacheMetadata:
    """Metadata describing the stored snapshot."""

    last_full_scan: datetime
    library_mtime: datetime
    track_count: int
    snapshot_hash: str
    version: str = SNAPSHOT_VERSION
    last_force_scan_time: str | None = None  # ISO format datetime

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata to a JSON-friendly dict."""
        return {
            "version": self.version,
            "last_full_scan": self.last_full_scan.isoformat(),
            "library_mtime": self.library_mtime.isoformat(),
            "track_count": self.track_count,
            "snapshot_hash": self.snapshot_hash,
            "last_force_scan_time": self.last_force_scan_time,
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
            last_force_scan_time=data.get("last_force_scan_time"),
        )


@dataclass
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
        current_time = now or _utc_now()
        if len(self.processed_track_ids) >= DELTA_MAX_TRACKED_IDS:
            return True
        tracked_ref = self.tracked_since or self.last_run
        return current_time - tracked_ref > DELTA_MAX_AGE

    def add_processed_ids(self, track_ids: Iterable[str]) -> None:
        """Add processed track identifiers while respecting limits."""
        current_time = _utc_now()
        if self.should_reset(now=current_time):
            self.processed_track_ids.clear()
            self.tracked_since = current_time

        self.processed_track_ids.update(track_ids)
        if self.tracked_since is None:
            self.tracked_since = current_time
