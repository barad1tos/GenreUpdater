"""Pending Verification Module.

This module maintains a list of albums that need re-verification in the future.
When an album's year cannot be definitely determined from external sources,
it is added to this list with a timestamp. On future runs, albums whose
verification period has elapsed will be checked again.

File operations (_load_pending_albums, _save_pending_albums) are asynchronous
using asyncio's run_in_executor to avoid blocking the event loop.

Refactored: Initial asynchronous loading handled in a separate async initialize method,
called by DependencyContainer after service instantiation.

Usage:
    service = PendingVerificationService(config, console_logger, error_logger)
    await service.initialize() # IMPORTANT: Call this after creating the instance

    # Mark album for future verification (now an async method)
    await service.mark_for_verification("Pink Floyd", "The Dark Side of the Moon")

    # Check if album needs verification now (now an async method)
    if await service.is_verification_needed("Pink Floyd", "The Dark Side of the Moon"):
        # Perform verification
        pass

    # Get all pending albums (now an async method)
    pending_list = await service.get_all_pending_albums()

    # Get verified album keys (now an async method)
    verified_keys = await service.get_verified_album_keys()

"""

import asyncio
import csv
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from core.logger import LogFormat, get_full_log_path
from core.models.metadata_utils import clean_names
from services.cache.hash_service import UnifiedHashService

# Suffix for the file that tracks the last auto-verification timestamp
PENDING_LAST_VERIFY_SUFFIX = "_last_verify.txt"


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
    def from_string(cls, value: str) -> "VerificationReason":
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


class Logger(Protocol):
    """Protocol defining the interface for loggers used in the application.

    This Protocol specifies the required methods that any logger implementation
    must provide to be compatible with the PendingVerificationService.
    Implementations should handle different log levels appropriately.
    """

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log an informational message."""

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log a warning message."""

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log an error message."""

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log a debug message."""


# Type alias for error callback used in blocking I/O operations
ErrorCallback = Callable[[str], None]


# noinspection PyTypeChecker
class PendingVerificationService:
    """Service to track albums needing future verification of their release year.

    Uses hash-based keys for album data. File operations are asynchronous.
    Initializes asynchronously.

    Attributes:
        pending_file_path (str): Path to the CSV file storing pending verifications
        console_logger: Logger for console output
        verification_interval_days (int): Days to wait before re-checking an album
        pending_albums: Cache of pending albums using hash keys (PendingAlbumEntry).
        _lock: asyncio.Lock for synchronizing access to pending_albums cache

    """

    def __init__(
        self,
        config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Initialize the PendingVerificationService.

        Does NOT perform file loading here. Use the async initialize method.

        Args:
            config: Application configuration dictionary
            console_logger: Logger for console output
            error_logger: Logger for error logging.

        """
        self.config = config
        self.console_logger = console_logger
        self.error_logger = error_logger

        # Get verification interval from config or use default (30 days)
        year_retrieval_config = config.get("year_retrieval", {})
        processing_config = year_retrieval_config.get("processing", {})
        self.verification_interval_days = processing_config.get(
            "pending_verification_interval_days",
            30,
        )
        self.prerelease_recheck_days = (
            self._normalize_recheck_days(processing_config.get("prerelease_recheck_days")) or self.verification_interval_days
        )

        # Set up the pending file path using the utility function
        self.pending_file_path = get_full_log_path(
            config,
            "pending_verification_file",
            "csv/pending_year_verification.csv",
        )
        # In-memory cache: key -> PendingAlbumEntry
        self.pending_albums: dict[str, PendingAlbumEntry] = {}

        # asyncio.Lock for thread-safe access to pending_albums cache
        self._lock = asyncio.Lock()

        # Error callback for blocking operations (used instead of print)
        self._error_callback: ErrorCallback = lambda msg: print(msg, file=sys.stderr)

    @staticmethod
    def _normalize_recheck_days(value: Any) -> int | None:
        """Convert value to a positive integer suitable for recheck interval."""
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        return candidate if candidate > 0 else None

    @staticmethod
    def _parse_metadata(metadata_str: str) -> dict[str, Any]:
        """Safely parse metadata JSON string into a dictionary."""
        if not metadata_str:
            return {}
        try:
            parsed = json.loads(metadata_str)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def initialize(self) -> None:
        """Asynchronously initializes the PendingVerificationService by loading data from the disk.

        This method must be called after instantiation.
        """
        self.console_logger.info(
            "Initializing PendingVerificationService asynchronously...",
        )
        await self._load_pending_albums()

        # Normalize keys to ensure compatibility with cleaned album names
        await self._normalize_pending_album_keys()

        self.console_logger.info(
            "PendingVerificationService asynchronous initialization complete.",
        )

    @staticmethod
    def _generate_album_key(artist: str, album: str) -> str:
        """Generate a unique hash key for an album based on artist and album names.

        TASK-004: Consolidated to use UnifiedHashService for consistent hashing
        across all services. Use cleaned album names for compatibility.
        """
        track_id = f"{artist}|{album}"
        return UnifiedHashService.hash_pending_key(track_id)

    def generate_album_key(self, artist: str, album: str) -> str:
        """Public method to generate a unique hash key for an album."""
        return self._generate_album_key(artist, album)

    def _ensure_pending_file_directory(self) -> None:
        """Ensure pending file directory exists."""
        Path(self.pending_file_path).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_csv_headers(fieldnames: list[str] | None) -> bool:
        """Validate that CSV has required headers.

        Args:
            fieldnames: List of CSV header field names

        Returns:
            bool: True if headers are valid, False otherwise

        """
        if not fieldnames:
            return False

        required_fields = {"artist", "album", "timestamp"}
        return all(field in fieldnames for field in required_fields)

    def _parse_timestamp(self, timestamp_str: str, artist: str, album: str) -> datetime | None:
        """Parse a timestamp string with fallback handling.

        Args:
            timestamp_str: The timestamp string to parse
            artist: Artist name for error reporting
            album: Album name for error reporting

        Returns:
            datetime | None: Parsed timestamp or None if parsing failed

        """
        try:
            # Try the primary format first
            return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            try:
                # Fallback to date-only format
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d").replace(tzinfo=UTC)
                self.error_logger.warning(
                    f"Pending file timestamp had date-only format for '{artist} - {album}': {timestamp_str}. Parsed as date only."
                )
                return timestamp
            except ValueError:
                self._error_callback(f"WARNING: Invalid timestamp format in pending file for '{artist} - {album}': {timestamp_str}")
                return None

    def _process_csv_row(self, row: dict[str, str]) -> tuple[str, PendingAlbumEntry] | None:
        """Process a single CSV row into album data.

        Args:
            row: Dictionary representing a CSV row

        Returns:
            Tuple of (key_hash, PendingAlbumEntry) or None if row invalid

        """
        artist = row.get("artist", "").strip()
        album = row.get("album", "").strip()
        timestamp_str = row.get("timestamp", "").strip()
        reason_str = row.get("reason", "").strip()
        metadata = row.get("metadata", "").strip()
        attempt_count_str = row.get("attempt_count", "0").strip()

        if not (artist and album and timestamp_str):
            self._error_callback(f"WARNING: Skipping malformed row in pending file: {row}")
            return None

        timestamp = self._parse_timestamp(timestamp_str, artist, album)
        if timestamp is None:
            return None

        # Parse attempt_count with fallback for legacy CSVs without this column
        try:
            attempt_count = int(attempt_count_str) if attempt_count_str else 0
        except ValueError:
            attempt_count = 0

        try:
            key_hash = self.generate_album_key(artist, album)
            entry = PendingAlbumEntry(
                timestamp=timestamp,
                artist=artist,
                album=album,
                reason=VerificationReason.from_string(reason_str),
                metadata=metadata,
                attempt_count=attempt_count,
            )
            return key_hash, entry
        except (ValueError, TypeError) as row_e:
            self._error_callback(f"WARNING: Error processing row in pending file for '{artist} - {album}': {row_e}")
            return None

    def _read_csv_data(self) -> dict[str, PendingAlbumEntry]:
        """Read and parse CSV data from the pending file.

        Returns:
            Dictionary mapping hash keys to PendingAlbumEntry objects

        """
        pending_data: dict[str, PendingAlbumEntry] = {}

        try:
            with Path(self.pending_file_path).open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames) if reader.fieldnames else None

                if not self._validate_csv_headers(fieldnames):
                    self._error_callback(
                        f"WARNING: Pending verification CSV header missing expected fields in "
                        f"{self.pending_file_path}. Found: {fieldnames}. Skipping load."
                    )
                    return pending_data

                albums_data = list(reader)

            # Process data after closing the file
            for row in albums_data:
                result = self._process_csv_row(row)
                if result is not None:
                    key_hash, entry = result
                    pending_data[key_hash] = entry

            return pending_data

        except FileNotFoundError as csv_error:
            self._error_callback(f"ERROR reading pending verification file {self.pending_file_path}: {csv_error}")
            return {}
        except (OSError, csv.Error, UnicodeDecodeError) as csv_error:
            self._error_callback(f"UNEXPECTED ERROR during CSV read from {self.pending_file_path}: {csv_error}")
            return {}

    def _blocking_load(self) -> dict[str, PendingAlbumEntry]:
        """Blocking file operation to load pending albums. Run in executor."""
        self._ensure_pending_file_directory()

        if not os.path.exists(self.pending_file_path):
            self.console_logger.info(f"Pending verification file not found, will create at: {self.pending_file_path}")
            return {}

        self.console_logger.debug(f"Reading pending verification file: {self.pending_file_path}")
        return self._read_csv_data()

    async def _load_pending_albums(self) -> None:
        """Load the list of pending albums from the CSV file into memory asynchronously.

        Uses loop.run_in_executor for blocking file operations.
        """
        loop = asyncio.get_running_loop()

        async with self._lock:
            self.pending_albums = await loop.run_in_executor(None, self._blocking_load)

        if self.pending_albums:
            self.console_logger.info(f"Loaded {len(self.pending_albums)} pending albums for verification")
        else:
            self.console_logger.info("No pending albums loaded (file not found or empty).")

    def _blocking_save(self, entries: list[PendingAlbumEntry]) -> None:
        """Blocking save operation to write pending albums to CSV.
        Run in executor.

        Args:
            entries: List of PendingAlbumEntry objects to save
        """
        temp_file = f"{self.pending_file_path}.tmp"

        try:
            Path(self.pending_file_path).parent.mkdir(parents=True, exist_ok=True)

            with Path(temp_file).open("w", newline="", encoding="utf-8") as f:
                fieldnames = ["artist", "album", "timestamp", "reason", "metadata", "attempt_count"]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for entry in entries:
                    writer.writerow(
                        {
                            "artist": entry.artist,
                            "album": entry.album,
                            "timestamp": entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            "reason": entry.reason.value,
                            "metadata": entry.metadata,
                            "attempt_count": str(entry.attempt_count),
                        }
                    )

            # Atomic rename
            Path(temp_file).replace(self.pending_file_path)

        except (OSError, csv.Error) as save_error:
            self._error_callback(f"ERROR during blocking save of pending verification file: {save_error}")
            if Path(temp_file).exists():
                try:
                    Path(temp_file).unlink()
                except OSError as cleanup_e:
                    self._error_callback(f"WARNING: Could not remove temporary pending file {temp_file}: {cleanup_e}")
            raise

    async def _save_pending_albums(self) -> None:
        """Save the current list of pending albums to the CSV file asynchronously."""
        loop = asyncio.get_running_loop()

        async with self._lock:
            entries = list(self.pending_albums.values())

        try:
            await loop.run_in_executor(None, self._blocking_save, entries)
            self.console_logger.info(f"Saved {len(entries)} pending albums for verification")
        except (OSError, csv.Error) as e:
            self.error_logger.exception(f"Error saving pending verification file: {e}")

    async def mark_for_verification(
        self,
        artist: str,
        album: str,
        reason: VerificationReason | str = VerificationReason.NO_YEAR_FOUND,
        metadata: dict[str, Any] | None = None,
        recheck_days: int | None = None,
    ) -> None:
        """Mark an album for future verification with reason and optional metadata.

        Uses a hash key for storage. Saves asynchronously.
        If the album is already pending, increments the attempt counter.

        Args:
            artist: Artist name
            album: Album name
            reason: Reason for verification (default: NO_YEAR_FOUND, can be PRERELEASE, etc.)
            metadata: Optional metadata dictionary to store additional information
            recheck_days: Optional override for verification interval in days

        """
        # Normalize reason to enum
        reason_enum = VerificationReason.from_string(reason) if isinstance(reason, str) else reason

        interval_override = self._normalize_recheck_days(recheck_days)
        if interval_override is None and reason_enum == VerificationReason.PRERELEASE:
            interval_override = self.prerelease_recheck_days

        # Acquire lock before modifying the in-memory cache
        async with self._lock:
            # Generate the hash key for the album
            key_hash = self._generate_album_key(artist, album)

            # Check if entry already exists to get previous attempt count
            existing_entry = self.pending_albums.get(key_hash)
            new_attempt_count = (existing_entry.attempt_count + 1) if existing_entry else 1

            # Serialize metadata dict to JSON string to preserve type information
            metadata_payload: dict[str, Any] = {}
            if metadata:
                metadata_payload |= metadata

            if interval_override is not None:
                metadata_payload["recheck_days"] = interval_override

            metadata_str = json.dumps(metadata_payload) if metadata_payload else ""

            # Store the entry using PendingAlbumEntry with updated attempt count
            self.pending_albums[key_hash] = PendingAlbumEntry(
                timestamp=datetime.now(UTC),
                artist=artist.strip(),
                album=album.strip(),
                reason=reason_enum,
                metadata=metadata_str,
                attempt_count=new_attempt_count,
            )

        # Log with the appropriate message based on reason
        effective_interval = interval_override if interval_override is not None else self.verification_interval_days

        if reason_enum == VerificationReason.PRERELEASE:
            self.console_logger.info(
                f"Marked prerelease album '{artist} - {album}' for future verification in {effective_interval} days (attempt #{new_attempt_count})",
            )
        else:
            self.console_logger.info(
                "Marked '%s - %s' for verification in %d days (reason: %s, attempt #%d)",
                artist,
                album,
                effective_interval,
                reason_enum.value,
                new_attempt_count,
            )

        # Save asynchronously after modifying the cache
        await self._save_pending_albums()

    async def get_entry(self, artist: str, album: str) -> PendingAlbumEntry | None:
        """Get pending entry for artist/album if exists.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            PendingAlbumEntry if found, None otherwise.

        """
        async with self._lock:
            album_key = self._generate_album_key(artist, album)
            return self.pending_albums.get(album_key)

    async def get_attempt_count(self, artist: str, album: str) -> int:
        """Get current verification attempt count for an album.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Number of verification attempts made (0 if not in pending list).

        """
        async with self._lock:
            album_key = self._generate_album_key(artist, album)
            entry = self.pending_albums.get(album_key)
            return entry.attempt_count if entry else 0

    # is_verification_needed is now an async method because it acquires the lock
    async def is_verification_needed(self, artist: str, album: str) -> bool:
        """Check if an album needs verification now.

        Uses the hash key for lookup. Reads from an in-memory cache (async with lock).

        Args:
            artist: Artist name
            album: Album name

        Returns:
            True if the verification period has elapsed, False otherwise

        """
        # Acquire lock before reading from the in-memory cache
        async with self._lock:
            # Generate the hash key for the album
            key_hash = self._generate_album_key(artist, album)

            # Check if the hash key exists in the in-memory cache
            if key_hash not in self.pending_albums:
                return False

            # Get the entry
            entry = self.pending_albums[key_hash]
            metadata = self._parse_metadata(entry.metadata)
            interval_days = self.verification_interval_days

            if entry.reason == VerificationReason.PRERELEASE:
                override = self._normalize_recheck_days(metadata.get("recheck_days"))
                interval_days = override if override is not None else self.prerelease_recheck_days

            verification_time = entry.timestamp + timedelta(days=interval_days)

            if datetime.now(UTC) >= verification_time:
                # Verification period has elapsed
                self.console_logger.info(
                    f"Verification period elapsed for '{entry.artist} - {entry.album}'",
                )
                return True

            return False

    # remove_from_pending is now an async method because it calls async _save_pending_albums
    async def remove_from_pending(self, artist: str, album: str) -> None:
        """Remove an album from the pending verification list.

        Uses the hash key for removal. Saves asynchronously.

        Args:
            artist: Artist name
            album: Album name

        """
        # Acquire lock before modifying the in-memory cache
        async with self._lock:
            # Generate the hash key for the album
            key_hash = self._generate_album_key(artist, album)

            # Remove from the in-memory cache if the hash key exists
            if key_hash in self.pending_albums:
                # Retrieve the original artist / album from the stored entry for logging
                entry = self.pending_albums[key_hash]
                del self.pending_albums[key_hash]
                self.console_logger.info(
                    f"Removed '{entry.artist} - {entry.album}' from pending verification",
                )
            # No need to save if the item wasn't found
            else:
                self.console_logger.debug(
                    f"Attempted to remove '{artist} - {album}' from pending verification, but it was not found.",
                )
                return  # Exit without saving if no removal occurred

        # Save asynchronously after modifying the cache
        await self._save_pending_albums()

    # get_all_pending_albums is now an async method because it needs to acquire the lock
    # to safely access the in-memory cache.
    async def get_all_pending_albums(self) -> list[PendingAlbumEntry]:
        """Get a list of all pending albums with their verification data.

        Retrieves all PendingAlbumEntry objects from the in-memory cache.
        Accesses the in-memory cache asynchronously with a lock.

        Returns:
            List of PendingAlbumEntry objects

        """
        # Acquire lock before accessing the in-memory cache
        async with self._lock:
            # Return all values from the cache
            return list(self.pending_albums.values())

    async def get_pending_albums_by_reason(
        self,
        reason: VerificationReason | str,
    ) -> list[PendingAlbumEntry]:
        """Get pending albums filtered by reason.

        Args:
            reason: The reason to filter by (e.g., PRERELEASE, NO_YEAR_FOUND)

        Returns:
            List of PendingAlbumEntry objects matching the reason

        """
        # Normalize reason to enum
        reason_enum = VerificationReason.from_string(reason) if isinstance(reason, str) else reason

        async with self._lock:
            return [entry for entry in self.pending_albums.values() if entry.reason == reason_enum]

    # get_verified_album_keys is now an async method because it needs to acquire the lock
    # to safely access the in-memory cache.
    async def get_verified_album_keys(self) -> set[str]:
        """Get the set of album hash keys that need verification now.

        Checks the timestamp in the stored tuple.
        Accesses the in-memory cache asynchronously with a lock.

        Returns:
            Set of album hash keys needing verification

        """
        now = datetime.now(UTC)
        verified_keys: set[str] = set()

        # Acquire lock before accessing the in-memory cache
        async with self._lock:
            # Iterate through items (key_hash, value_tuple) in the in-memory cache
            # Iterate over entries
            for key_hash, entry in self.pending_albums.items():
                verification_time = entry.timestamp + timedelta(
                    days=self.verification_interval_days,
                )
                if now >= verification_time:
                    self.console_logger.info(
                        f"Album '{entry.artist} - {entry.album}' needs verification",
                    )
                    verified_keys.add(key_hash)
                # self.console_logger.debug(

        return verified_keys

    async def _normalize_pending_album_keys(self) -> None:
        """Ensure all in-memory pending keys use cleaned album names.

        Converts legacy keys that still contain raw album names with suffixes.
        After migration, saves the updated pending list to disk.
        """
        async with self._lock:
            # Collect updates first, then apply them to avoid modifying dict during iteration
            pending_updates: dict[str, PendingAlbumEntry] = {}
            keys_to_delete: list[str] = []

            for old_key, entry in self.pending_albums.items():
                new_key = self._generate_album_key(entry.artist, entry.album)
                if new_key != old_key:
                    # Clean album name for storage as well
                    _, cleaned_album = clean_names(
                        entry.artist,
                        "",
                        entry.album,
                        config=self.config,
                        console_logger=self.console_logger,
                        error_logger=self.error_logger,
                    )
                    # Stage the update
                    pending_updates[new_key] = PendingAlbumEntry(
                        timestamp=entry.timestamp,
                        artist=entry.artist,
                        album=cleaned_album,
                        reason=entry.reason,
                        metadata=entry.metadata,
                    )
                    keys_to_delete.append(old_key)

            # Apply all updates
            for new_key, new_entry in pending_updates.items():
                self.pending_albums[new_key] = new_entry
            for key_to_delete in keys_to_delete:
                del self.pending_albums[key_to_delete]

            if pending_updates:
                self.console_logger.info(
                    "Normalized %d pending album keys by removing suffixes.",
                    len(pending_updates),
                )
        if pending_updates:
            # Save outside the lock to avoid prolonged blocking
            await self._save_pending_albums()

    async def generate_problematic_albums_report(
        self,
        min_attempts: int = 3,
        report_path: str | None = None,
    ) -> int:
        """Generate a report of albums that failed to get year after multiple attempts.

        Args:
            min_attempts: Minimum number of verification attempts to include in the report
            report_path: Path to save the report (uses config default if None)

        Returns:
            Number of problematic albums found

        """
        if report_path is None:
            report_path = get_full_log_path(
                self.config,
                "reporting",
                self.config.get("reporting", {}).get(
                    "problematic_albums_path",
                    "reports/albums_without_year.csv",
                ),
            )

        # Track attempts per album
        album_attempts: dict[str, list[datetime]] = {}

        async with self._lock:
            current_time = datetime.now(UTC)

            for key, entry in self.pending_albums.items():
                # Calculate how many verification periods have passed
                time_diff = current_time - entry.timestamp
                periods_passed = int(time_diff.total_seconds() / (self.verification_interval_days * 86400))

                if periods_passed >= min_attempts - 1:
                    if key not in album_attempts:
                        album_attempts[key] = []

                    # Reconstruct verification dates
                    for i in range(periods_passed + 1):
                        attempt_time = entry.timestamp + timedelta(seconds=i * self.verification_interval_days * 86400)
                        album_attempts[key].append(attempt_time)

        # Generate report
        try:
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)

            # Use run_in_executor for async file operation
            loop = asyncio.get_running_loop()

            def _write_report() -> None:
                with Path(report_path).open("w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            "Artist",
                            "Album",
                            "First Attempt",
                            "Last Attempt",
                            "Total Attempts",
                            "Days Since First Attempt",
                            "Status",
                        ]
                    )

                    for album_key, attempts in sorted(
                        album_attempts.items(),
                        key=lambda x: len(x[1]),
                        reverse=True,
                    ):
                        album_entry = self.pending_albums[album_key]
                        first_attempt = min(attempts)
                        last_attempt = max(attempts)
                        days_since_first = (datetime.now(UTC) - first_attempt).days

                        writer.writerow(
                            [
                                album_entry.artist,
                                album_entry.album,
                                first_attempt.strftime("%Y-%m-%d"),
                                last_attempt.strftime("%Y-%m-%d"),
                                len(attempts),
                                days_since_first,
                                "Pending verification",
                            ]
                        )

            await loop.run_in_executor(None, _write_report)

            self.console_logger.info(
                "Generated problematic albums report: %s (%d albums)",
                report_path,
                len(album_attempts),
            )

            return len(album_attempts)

        except (OSError, csv.Error) as e:
            self.error_logger.exception(
                "Failed to generate problematic albums report: %s",
                e,
            )
            return 0

    async def should_auto_verify(self) -> bool:
        """Check if automatic pending verification should run.

        Returns True if:
        - auto_verify_days has passed since last verification
        - No previous verification exists
        - auto_verify_days > 0 (feature enabled)

        Returns:
            True if auto-verify should run, False otherwise

        """
        pending_config = self.config.get("pending_verification", {})
        auto_verify_days = pending_config.get("auto_verify_days", 14)

        if auto_verify_days <= 0:
            return False

        last_verify_file = self.pending_file_path.replace(".csv", PENDING_LAST_VERIFY_SUFFIX)
        last_verify_path = Path(last_verify_file)

        if not last_verify_path.exists():
            self.console_logger.debug("No previous pending verification found, auto-verify needed")
            return True

        try:
            loop = asyncio.get_running_loop()

            def _read_last_verify() -> str:
                with last_verify_path.open(encoding="utf-8") as f:
                    return f.read().strip()

            last_verify_str = await loop.run_in_executor(None, _read_last_verify)
            last_verify = datetime.fromisoformat(last_verify_str)

            if last_verify.tzinfo is None:
                last_verify = last_verify.replace(tzinfo=UTC)

            days_since = (datetime.now(tz=UTC) - last_verify).days

            if days_since >= auto_verify_days:
                self.console_logger.info(
                    "%s needed: %s days since last check %s",
                    LogFormat.label("AUTO-VERIFY-PENDING"),
                    LogFormat.number(days_since),
                    LogFormat.dim(f"(threshold: {auto_verify_days})"),
                )
                return True

            self.console_logger.debug(
                "Auto-verify pending not needed: %d days since last check (threshold: %d)",
                days_since,
                auto_verify_days,
            )
            return False

        except (OSError, ValueError, RuntimeError) as e:
            self.error_logger.warning(
                "Error checking auto-verify pending status for %s; auto-verify fallback due to error: %s",
                last_verify_path,
                e,
            )
            return True  # Run verification if we can't determine last run

    async def update_verification_timestamp(self) -> None:
        """Update the last pending verification timestamp file."""
        last_verify_file = self.pending_file_path.replace(".csv", PENDING_LAST_VERIFY_SUFFIX)
        last_verify_path = Path(last_verify_file)

        try:
            loop = asyncio.get_running_loop()

            def _write_last_verify() -> None:
                with last_verify_path.open("w", encoding="utf-8") as f:
                    f.write(datetime.now(tz=UTC).isoformat())

            await loop.run_in_executor(None, _write_last_verify)
        except (OSError, ValueError, RuntimeError) as e:
            self.error_logger.warning(
                "Error updating last pending verification date at %s: %s",
                last_verify_path,
                e,
            )
