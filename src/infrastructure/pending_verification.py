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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from src.infrastructure.cache.hash_service import UnifiedHashService
from src.shared.core.logger import get_full_log_path
from src.shared.data.metadata import clean_names


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


# noinspection PyTypeChecker
class PendingVerificationService:
    """Service to track albums needing future verification of their release year.

    Uses hash-based keys for album data. File operations are asynchronous.
    Initializes asynchronously.

    Attributes:
        pending_file_path (str): Path to the CSV file storing pending verifications
        console_logger: Logger for console output
    def __init__(self, config: dict[str, Any], console_logger: Logger, error_logger: Logger):
        verification_interval_days (int): Days to wait before re-checking an album
        pending_albums: Cache of pending albums using hash keys.
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
        # Ensure the config path exists before accessing
        year_retrieval_config = config.get("year_retrieval", {})
        processing_config = year_retrieval_config.get(
            "processing",
            {},
        )  # Get processing subsection
        self.verification_interval_days = processing_config.get(
            "pending_verification_interval_days",
            30,
        )  # Get from processing

        # Set up the pending file path using the utility function
        self.pending_file_path = get_full_log_path(
            config,
            "pending_verification_file",
            "csv/pending_year_verification.csv",
        )
        # Initialize the in-memory cache of pending albums - it will be populated in async initialize
        # key: hash of "artist|album", value: (timestamp, artist, album, reason, metadata)
        self.pending_albums: dict[str, tuple[datetime, str, str, str, str]] = {}

        # Initialize an asyncio Lock for thread-safe access to the in-memory cache
        self._lock = asyncio.Lock()

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
                print(
                    f"WARNING: Invalid timestamp format in pending file for '{artist} - {album}': {timestamp_str}",
                    file=sys.stderr,
                )
                return None

    def _process_csv_row(self, row: dict[str, str]) -> tuple[str, tuple[datetime, str, str, str, str]] | None:
        """Process a single CSV row into album data.

        Args:
            row: Dictionary representing a CSV row

        Returns:
            tuple[str, tuple[datetime, str, str, str, str]] | None:
            (key_hash, (timestamp, artist, album, reason, metadata)) or None if row invalid

        """
        artist = row.get("artist", "").strip()
        album = row.get("album", "").strip()
        timestamp_str = row.get("timestamp", "").strip()
        reason = row.get("reason", "no_year_found").strip()
        metadata = row.get("metadata", "").strip()

        if not (artist and album and timestamp_str):
            print(
                f"WARNING: Skipping malformed row in pending file: {row}",
                file=sys.stderr,
            )
            return None

        timestamp = self._parse_timestamp(timestamp_str, artist, album)
        if timestamp is None:
            return None

        try:
            key_hash = self.generate_album_key(artist, album)
            return key_hash, (timestamp, artist, album, reason, metadata)
        except (ValueError, TypeError) as row_e:
            print(
                f"WARNING: Error processing row in pending file for '{artist} - {album}': {row_e}",
                file=sys.stderr,
            )
            return None

    def _read_csv_data(self) -> dict[str, tuple[datetime, str, str, str, str]]:
        """Read and parse CSV data from the pending file.

        Returns:
            dict[str, tuple[datetime, str, str, str, str]]: Dictionary of album data

        """
        pending_data: dict[str, tuple[datetime, str, str, str, str]] = {}

        try:
            with Path(self.pending_file_path).open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames) if reader.fieldnames else None

                if not self._validate_csv_headers(fieldnames):
                    print(
                        f"WARNING: Pending verification CSV header missing expected fields in "
                        f"{self.pending_file_path}. Found: {fieldnames}. Skipping load.",
                        file=sys.stderr,
                    )
                    return pending_data

                albums_data = list(reader)

            # Process data after closing the file
            for row in albums_data:
                result = self._process_csv_row(row)
                if result is not None:
                    key_hash, album_data = result
                    pending_data[key_hash] = album_data

            return pending_data

        except FileNotFoundError as csv_error:
            print(
                f"ERROR reading pending verification file {self.pending_file_path}: {csv_error}",
                file=sys.stderr,
            )
            return {}
        except (OSError, csv.Error, UnicodeDecodeError) as csv_error:
            print(
                f"UNEXPECTED ERROR during CSV read from {self.pending_file_path}: {csv_error}",
                file=sys.stderr,
            )
            return {}

    async def _load_pending_albums(self) -> None:
        """Load the list of pending albums from the CSV file into memory asynchronously.

        Uses loop.run_in_executor for blocking file operations.
        Read artist, album, and timestamp from CSV and stores using hash keys.
        """
        loop = asyncio.get_running_loop()

        def blocking_load() -> dict[str, tuple[datetime, str, str, str, str]]:
            """Blocking file operation to be run in executor."""
            self._ensure_pending_file_directory()

            if not os.path.exists(self.pending_file_path):
                self.console_logger.info(f"Pending verification file not found, will create at: {self.pending_file_path}")
                return {}

            print(
                f"DEBUG: Reading pending verification file: {self.pending_file_path}",
                file=sys.stderr,
            )
            return self._read_csv_data()

        # Run the blocking load operation in the default executor
        async with self._lock:
            self.pending_albums = await loop.run_in_executor(None, blocking_load)

        # Log success/failure after the executor task is complete
        if self.pending_albums:
            self.console_logger.info(f"Loaded {len(self.pending_albums)} pending albums for verification from {self.pending_file_path}")
        else:
            self.console_logger.info(f"No pending albums loaded from {self.pending_file_path} (file not found or empty/corrupt).")

    async def _save_pending_albums(self) -> None:
        """Save the current list of pending albums to the CSV file asynchronously.

        Uses loop.run_in_executor for blocking file operations.
        Writes artist, album, and timestamp columns.
        """
        # Use the asyncio loop to run blocking file I/O in a thread pool
        loop = asyncio.get_event_loop()

        # Define the blocking file writing operation
        def blocking_save() -> None:
            """Blocking save operation to be run in executor."""
            # First, write to a temporary file
            temp_file = f"{self.pending_file_path}.tmp"

            try:
                # Create directories if they do not exist
                Path(self.pending_file_path).parent.mkdir(parents=True, exist_ok=True)

                with Path(temp_file).open("w", newline="", encoding="utf-8") as f:
                    # Define fieldnames for the CSV file - now includes reason and metadata
                    fieldnames = ["artist", "album", "timestamp", "reason", "metadata"]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()

                    # Acquire lock is done outside the blocking function now
                    # Iterate through values in the in-memory cache
                    # (which are (timestamp, artist, album, reason, metadata) tuples)
                    # Create a list copy to iterate safely in a separate thread
                    pending_items_to_save = list(self.pending_albums.values())

                    for (
                        timestamp,
                        artist,
                        album,
                        reason,
                        metadata,
                    ) in pending_items_to_save:
                        # Write artist, album, timestamp, reason, and metadata
                        writer.writerow(
                            {
                                "artist": artist,
                                "album": album,
                                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                                "reason": reason,
                                "metadata": metadata,
                            },
                        )

                # Rename the temporary file (atomic operation)
                Path(temp_file).replace(self.pending_file_path)

            except (OSError, csv.Error) as save_error:
                # Log error and clean up the temp file
                print(
                    f"ERROR during blocking save of pending verification file to {self.pending_file_path}: {save_error}",
                    file=sys.stderr,
                )
                if Path(temp_file).exists():
                    try:
                        Path(temp_file).unlink()
                    except OSError as cleanup_e:
                        print(
                            f"WARNING: Could not remove temporary pending file {temp_file}: {cleanup_e}",
                            file=sys.stderr,
                        )
                # Re-raise the exception so run_in_executor propagates it
                raise

        # Acquire lock before accessing the in-memory cache for saving
        async with self._lock:
            # Run the blocking save operation in the default executor
            try:
                await loop.run_in_executor(None, blocking_save)
                self.console_logger.info(
                    f"Saved {len(self.pending_albums)} pending albums for verification to {self.pending_file_path}",
                )  # Log after a successful save
            except (OSError, csv.Error) as e:
                # The exception from blocking_save is caught here
                self.error_logger.exception(f"Error saving pending verification file: {e}")

    # Mark album for future verification (now an async method because it calls async _save_pending_albums)
    async def mark_for_verification(
        self,
        artist: str,
        album: str,
        reason: str = "no_year_found",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Mark an album for future verification with reason and optional metadata.

        Uses a hash key for storage. Saves asynchronously.

        Args:
            artist: Artist name
            album: Album name
            reason: Reason for verification (default: "no_year_found", can be "prerelease", etc.)
            metadata: Optional metadata dictionary to store additional information

        """
        # Acquire lock before modifying the in-memory cache
        async with self._lock:
            # Generate the hash key for the album
            key_hash = self._generate_album_key(artist, album)

            # Serialize metadata dict to JSON string to preserve type information
            metadata_str = ""
            if metadata:
                metadata_str = json.dumps(metadata)

            # Store the current timestamp, original artist, album, reason, and metadata in the value
            self.pending_albums[key_hash] = (
                datetime.now(UTC),
                artist.strip(),
                album.strip(),
                reason,
                metadata_str,
            )

        # Log with the appropriate message based on reason
        if reason == "prerelease":
            self.console_logger.info(
                f"Marked prerelease album '{artist} - {album}' for future verification",
            )
        else:
            self.console_logger.info(
                f"Marked '{artist} - {album}' for verification in {self.verification_interval_days} days (reason: {reason})",
            )

        # Save asynchronously after modifying the cache
        await self._save_pending_albums()

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

            # The value is a tuple (timestamp, artist, album, reason, metadata)
            timestamp, stored_artist, stored_album, _reason, _metadata = self.pending_albums[key_hash]
            verification_time = timestamp + timedelta(
                days=self.verification_interval_days,
            )

            if datetime.now(UTC) >= verification_time:
                # Verification period has elapsed
                # Retrieve original artist/album from the stored tuple for logging
                self.console_logger.info(
                    f"Verification period elapsed for '{stored_artist} - {stored_album}'",
                )
                return True

            # Log when verification is NOT needed, for debugging
            # self.console_logger.debug(

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
                # Retrieve the original artist / album from the stored tuple for logging
                _, stored_artist, stored_album, _, _ = self.pending_albums[key_hash]
                del self.pending_albums[key_hash]
                self.console_logger.info(
                    f"Removed '{stored_artist} - {stored_album}' from pending verification",
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
    async def get_all_pending_albums(
        self,
    ) -> list[tuple[datetime, str, str, str, str]]:  # Updated return type to include reason and metadata
        """Get a list of all pending albums with their verification timestamps.

        Retrieves timestamp, artist, album, reason, and metadata from the stored tuples.
        Accesses the in-memory cache asynchronously with a lock.

        Returns:
            List of tuples containing (timestamp, artist, album, reason, metadata)

        """
        # Acquire lock before accessing the in-memory cache
        async with self._lock:
            # Return all values from the cache
            return list(self.pending_albums.values())

    async def get_pending_albums_by_reason(
        self,
        reason: str,
    ) -> list[tuple[str, str, datetime, str]]:
        """Get pending albums filtered by reason.

        Args:
            reason: The reason to filter by (e.g., "prerelease", "no_year_found")

        Returns:
            List of tuples containing (artist, album, timestamp, metadata)

        """
        async with self._lock:
            result: list[tuple[str, str, datetime, str]] = []
            result.extend(
                (artist, album, timestamp, metadata)
                for timestamp, artist, album, stored_reason, metadata in self.pending_albums.values()
                if stored_reason == reason
            )
        return result

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
            # Iterate over a list copy to allow potential future modifications during iteration if needed,
            # though in this specific method it's just reading.
            for key_hash, value_tuple in self.pending_albums.items():
                # The value is a tuple (timestamp, artist, album, reason, metadata)
                timestamp, stored_artist, stored_album, _reason, _metadata = value_tuple
                verification_time = timestamp + timedelta(
                    days=self.verification_interval_days,
                )
                if now >= verification_time:
                    self.console_logger.info(
                        f"Album '{stored_artist} - {stored_album}' needs verification",
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
            updates: list[tuple[str, str]] = []  # (old_key, new_key)
            for old_key, (
                timestamp,
                artist,
                album,
                reason,
                metadata,
            ) in self.pending_albums.items():
                new_key = self._generate_album_key(artist, album)
                if new_key != old_key:
                    # Clean album name for storage as well
                    _, cleaned_album = clean_names(
                        artist,
                        "",
                        album,
                        config=self.config,
                        console_logger=self.console_logger,
                        error_logger=self.error_logger,
                    )
                    # Move to the new key, preserving reason and metadata
                    self.pending_albums[new_key] = (
                        timestamp,
                        artist,
                        cleaned_album,
                        reason,
                        metadata,
                    )
                    del self.pending_albums[old_key]
                    updates.append((old_key, new_key))
            if updates:
                self.console_logger.info(
                    "Normalized %d pending album keys by removing suffixes.",
                    len(updates),
                )
        if updates:
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

            for key, (
                timestamp,
                _artist,
                _album,
                _reason,
                _metadata,
            ) in self.pending_albums.items():
                # Calculate how many verification periods have passed
                time_diff = current_time - timestamp
                periods_passed = int(time_diff.total_seconds() / (self.verification_interval_days * 86400))

                if periods_passed >= min_attempts - 1:
                    if key not in album_attempts:
                        album_attempts[key] = []

                    # Reconstruct verification dates
                    for i in range(periods_passed + 1):
                        attempt_time = timestamp + timedelta(seconds=i * self.verification_interval_days * 86400)
                        album_attempts[key].append(attempt_time)

        # Generate report
        try:
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)

            # Use run_in_executor for async file operation
            loop = asyncio.get_event_loop()

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
                        _, artist, album, _, _ = self.pending_albums[album_key]
                        first_attempt = min(attempts)
                        last_attempt = max(attempts)
                        days_since_first = (datetime.now(UTC) - first_attempt).days

                        writer.writerow(
                            [
                                artist,
                                album,
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
