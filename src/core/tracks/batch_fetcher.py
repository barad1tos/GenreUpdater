"""Batch track fetching module.

This module handles fetching tracks from Music.app in batches to avoid
timeouts when processing large libraries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.models.metadata_utils import parse_tracks

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

    from src.core.models.protocols import AppleScriptClientProtocol, CacheServiceProtocol
    from src.core.models.track_models import TrackDict


# Maximum consecutive parse failures before aborting batch processing
MAX_CONSECUTIVE_PARSE_FAILURES = 3


class BatchTrackFetcher:
    """Fetches tracks from Music.app in batches.

    This class handles:
    - Batch-based track fetching to avoid AppleScript timeouts
    - Parse failure tracking and recovery
    - Caching and snapshot persistence of fetched tracks
    """

    def __init__(
        self,
        ap_client: AppleScriptClientProtocol,
        cache_service: CacheServiceProtocol,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        *,
        track_validator: Callable[[list[TrackDict]], list[TrackDict]],
        artist_processor: Callable[[list[TrackDict]], Awaitable[None]],
        snapshot_loader: Callable[[], Awaitable[list[TrackDict] | None]],
        snapshot_persister: Callable[[list[TrackDict], list[str] | None], Awaitable[None]],
        can_use_snapshot: Callable[[str | None], bool],
        dry_run: bool = False,
    ) -> None:
        """Initialize the batch track fetcher.

        Args:
            ap_client: AppleScript client for Music.app communication
            cache_service: Cache service for storing fetched tracks
            console_logger: Logger for info/debug messages
            error_logger: Logger for error messages
            config: Configuration dictionary
            track_validator: Callback to validate tracks for security
            artist_processor: Async callback to process artist renames
            snapshot_loader: Async callback to load tracks from snapshot
            snapshot_persister: Async callback to persist tracks to snapshot
            can_use_snapshot: Callback to check if snapshot can be used
            dry_run: Whether running in dry-run mode
        """
        self.ap_client = ap_client
        self.cache_service = cache_service
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.config = config
        self._track_validator = track_validator
        self._artist_processor = artist_processor
        self._snapshot_loader = snapshot_loader
        self._snapshot_persister = snapshot_persister
        self._can_use_snapshot = can_use_snapshot
        self.dry_run = dry_run

    async def fetch_all_tracks(
        self,
        batch_size: int = 1000,
        *,
        skip_snapshot_check: bool = False,
    ) -> list[TrackDict]:
        """Fetch all tracks from Music.app in batches.

        Args:
            batch_size: Number of tracks to fetch per batch
            skip_snapshot_check: Skip snapshot validation (used when already validated upstream)

        Returns:
            List of all track dictionaries
        """
        # Try loading from snapshot first
        if not skip_snapshot_check and self._can_use_snapshot(None):
            snapshot_tracks = await self._snapshot_loader()
            if snapshot_tracks is not None:
                self.console_logger.info(
                    "\u2713 Loaded %d tracks from snapshot cache; skipping batch fetch",
                    len(snapshot_tracks),
                )
                return snapshot_tracks

        # Snapshot not available - proceed with batch processing
        all_tracks = await self._fetch_tracks_in_batches(batch_size)

        # Cache and persist results
        await self._cache_and_persist_results(all_tracks)

        return all_tracks

    async def _fetch_tracks_in_batches(self, batch_size: int) -> list[TrackDict]:
        """Execute the batch fetching loop.

        Args:
            batch_size: Number of tracks per batch

        Returns:
            List of all fetched and validated tracks
        """
        all_tracks: list[TrackDict] = []
        offset = 1  # AppleScript indices start from 1
        batch_number = 0
        consecutive_failures = 0

        self.console_logger.info("Starting batch processing with batch_size=%d", batch_size)

        while True:
            batch_number += 1
            self.console_logger.info(
                "Fetching batch %d (offset=%d, limit=%d)...",
                batch_number,
                offset,
                batch_size,
            )

            try:
                batch_result = await self._fetch_and_validate_batch(batch_number, offset, batch_size)
                if batch_result is None:
                    break

                validated_tracks, should_continue, parse_failed = batch_result

                consecutive_failures, should_continue_loop = self._update_failure_counter(
                    consecutive_failures,
                    parse_failed,
                    batch_number,
                )
                if not should_continue_loop:
                    break

                all_tracks.extend(validated_tracks)

                if not should_continue:
                    break

                offset += batch_size

            except (OSError, ValueError, RuntimeError) as error:
                self.error_logger.exception(
                    "Error in batch %d (offset=%d): %s",
                    batch_number,
                    offset,
                    error,
                )
                break

        self.console_logger.info(
            "Batch processing completed: %d batches processed, %d total tracks fetched",
            batch_number,
            len(all_tracks),
        )

        return all_tracks

    async def _fetch_and_validate_batch(
        self,
        batch_number: int,
        offset: int,
        batch_size: int,
    ) -> tuple[list[TrackDict], bool, bool] | None:
        """Fetch and validate a single batch of tracks.

        Args:
            batch_number: Current batch number for logging
            offset: Starting offset for this batch
            batch_size: Number of tracks to fetch

        Returns:
            Tuple of (validated_tracks, should_continue, parse_failed) or None if end/error
        """
        args = ["", str(offset), str(batch_size)]  # empty artist, offset, limit

        raw_output = await self.ap_client.run_script(
            "fetch_tracks.scpt",
            args,
            timeout=300,  # 5 minutes per batch
        )

        if not raw_output:
            self.console_logger.info("Batch %d returned empty result, assuming end of tracks", batch_number)
            return None

        # Check for AppleScript status codes
        if raw_output.startswith("ERROR:"):
            self.error_logger.error("Batch %d AppleScript error: %s", batch_number, raw_output)
            return None
        if raw_output == "NO_TRACKS_FOUND":
            self.console_logger.info("Batch %d: no tracks found", batch_number)
            return None

        # Parse the batch
        batch_tracks = parse_tracks(raw_output, self.error_logger)

        if not batch_tracks:
            raw_row_count = self._count_raw_track_rows(raw_output)
            if raw_row_count == 0:
                self.console_logger.info("Batch %d contained no raw track rows, assuming end", batch_number)
                return None

            self.error_logger.warning(
                "Batch %d produced %d raw rows but none parsed successfully",
                batch_number,
                raw_row_count,
            )
            return [], True, True  # Empty tracks, should continue, parse failed

        # Validate and process tracks
        validated_tracks = self._track_validator(batch_tracks)
        await self._artist_processor(validated_tracks)

        self.console_logger.info(
            "Batch %d: fetched %d tracks, validated %d/%d",
            batch_number,
            len(batch_tracks),
            len(validated_tracks),
            len(batch_tracks),
        )

        # Check if we should continue (batch might be smaller due to filtering)
        should_continue = True
        if len(batch_tracks) < batch_size:
            self.console_logger.info(
                "Batch %d returned %d < %d tracks (some tracks filtered by AppleScript), continuing...",
                batch_number,
                len(batch_tracks),
                batch_size,
            )

        return validated_tracks, should_continue, False

    @staticmethod
    def _count_raw_track_rows(raw_output: str) -> int:
        """Count the number of raw track rows in AppleScript output.

        Args:
            raw_output: Raw AppleScript output string

        Returns:
            Number of track rows (separated by record separator)
        """
        if not raw_output:
            return 0
        record_separator = "\x1D"
        rows = raw_output.strip().split(record_separator)
        return len([row for row in rows if row.strip()])

    def _update_failure_counter(
        self,
        consecutive_failures: int,
        parse_failed: bool,
        batch_number: int,
    ) -> tuple[int, bool]:
        """Update parse failure tracking and determine if processing should continue.

        Args:
            consecutive_failures: Current count of consecutive failures
            parse_failed: Whether the current batch failed to parse
            batch_number: Current batch number for logging

        Returns:
            Tuple of (updated_failure_count, should_continue)
        """
        if not parse_failed:
            return 0, True

        updated_failures = consecutive_failures + 1
        self.error_logger.warning(
            "Parse failure %d/%d for batch %d",
            updated_failures,
            MAX_CONSECUTIVE_PARSE_FAILURES,
            batch_number,
        )

        if updated_failures >= MAX_CONSECUTIVE_PARSE_FAILURES:
            self.error_logger.error(
                "Aborting batch processing: %d consecutive parse failures indicate systematic issue",
                updated_failures,
            )
            return updated_failures, False

        return updated_failures, True

    async def _cache_and_persist_results(self, tracks: list[TrackDict]) -> None:
        """Cache fetched tracks in memory and persist to snapshot on disk.

        Args:
            tracks: List of fetched tracks to cache and persist
        """
        await self.cache_service.set_async("tracks_all", tracks)
        self.console_logger.info("Cached %d tracks for key: tracks_all", len(tracks))

        should_persist = tracks and self._can_use_snapshot(None) and not self.dry_run
        if not should_persist:
            return

        try:
            track_ids = [track.id for track in tracks]
            await self._snapshot_persister(tracks, track_ids)
        except Exception as error:
            self.error_logger.warning("Failed to persist library snapshot after batch fetch: %s", error)
