"""Track cache management module.

This module handles track caching operations including:
- Memory cache retrieval and validation
- Library snapshot persistence
- Delta track merging
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.core.models.track_models import TrackDict
from src.core.models.validators import is_valid_track_item
from src.services.cache.snapshot import LibraryCacheMetadata, LibraryDeltaCache

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable, Sequence

    from src.core.models.protocols import CacheServiceProtocol
    from src.services.cache.snapshot import LibrarySnapshotService


class TrackCacheManager:
    """Manages track caching and snapshot operations.

    This class handles:
    - Retrieving and validating cached tracks
    - Loading and saving library snapshots
    - Merging delta updates into snapshots
    """

    def __init__(
        self,
        cache_service: CacheServiceProtocol,
        snapshot_service: LibrarySnapshotService | None,
        console_logger: logging.Logger,
        current_time_func: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize the cache manager.

        Args:
            cache_service: Service for memory/disk cache operations
            snapshot_service: Service for library snapshot operations (optional)
            console_logger: Logger for info/debug messages
            current_time_func: Optional function to get current time (for testing)
        """
        self.cache_service = cache_service
        self.snapshot_service = snapshot_service
        self.console_logger = console_logger
        self._current_time_func = current_time_func or (lambda: datetime.now(UTC))

    def _current_time(self) -> datetime:
        """Get current UTC time."""
        return self._current_time_func()

    async def get_cached_tracks(self, cache_key: str) -> Sequence[TrackDict] | None:
        """Retrieve tracks from the cache with type validation.

        Args:
            cache_key: Cache key to retrieve

        Returns:
            List of tracks if found and valid, None otherwise
        """
        cached_value = await self.cache_service.get_async(cache_key)
        if cached_value is None:
            return None

        cached_list = cached_value
        validated_tracks: list[TrackDict] = []

        for i, item in enumerate(cached_list):
            if not is_valid_track_item(item):
                self.console_logger.warning(
                    "Cached data for %s contains invalid track dict at index %d. Ignoring cache.",
                    cache_key,
                    i,
                )
                return None
            # TypeGuard validates structure; model_validate handles both dict and TrackDict
            validated_tracks.append(TrackDict.model_validate(item))

        return validated_tracks

    async def load_snapshot(self) -> list[TrackDict] | None:
        """Load tracks from library snapshot if available and valid.

        Returns:
            List of tracks if snapshot is valid, None otherwise
        """
        if self.snapshot_service is None:
            return None

        snapshot_tracks = await self.snapshot_service.load_snapshot()
        if snapshot_tracks is None:
            self.console_logger.debug("Snapshot cache missing on disk")
            return None

        if await self.snapshot_service.is_snapshot_valid():
            return snapshot_tracks

        self.console_logger.debug("Snapshot exists but is stale")
        return None

    async def get_snapshot_for_delta_update(self) -> tuple[list[TrackDict] | None, datetime | None]:
        """Get snapshot tracks and minimum date for delta update.

        Returns:
            Tuple of (snapshot_tracks, min_date) or (None, None) if not available
        """
        if self.snapshot_service is None:
            return None, None

        snapshot_tracks = await self.snapshot_service.load_snapshot()
        if snapshot_tracks is None:
            return None, None

        if await self.snapshot_service.is_snapshot_valid():
            # Snapshot is valid, no delta needed
            return snapshot_tracks, None

        if not self.snapshot_service.is_delta_enabled():
            self.console_logger.warning("Snapshot stale and delta updates disabled; full rescan required")
            return None, None

        # Calculate minimum date for delta fetch
        metadata = await self.snapshot_service.get_snapshot_metadata()
        min_date = metadata.last_full_scan if metadata else None

        delta_cache = await self.snapshot_service.load_delta()
        if delta_cache and (candidates := [c for c in (min_date, delta_cache.last_run) if c is not None]):
            min_date = max(candidates)

        if min_date is None:
            self.console_logger.info("Unable to determine delta window; falling back to full scan")
            return None, None

        self.console_logger.info(
            "Attempting delta update: %d cached tracks + new changes since last scan",
            len(snapshot_tracks),
        )
        return snapshot_tracks, min_date

    @staticmethod
    def merge_tracks(existing: list[TrackDict], updates: list[TrackDict]) -> list[TrackDict]:
        """Merge delta updates into the existing snapshot while preserving order.

        Args:
            existing: Existing snapshot tracks
            updates: New/updated tracks from delta fetch

        Returns:
            Merged list of tracks
        """
        update_map = {str(track.id): track for track in updates}
        merged: list[TrackDict] = []
        seen_ids: set[str] = set()

        for track in existing:
            track_id = str(track.id)
            if track_id in update_map:
                merged.append(update_map[track_id])
            else:
                merged.append(track)
            seen_ids.add(track_id)

        for track in updates:
            track_id = str(track.id)
            if track_id not in seen_ids:
                merged.append(track)
                seen_ids.add(track_id)

        return merged

    async def update_snapshot(
        self,
        tracks: list[TrackDict],
        processed_track_ids: Sequence[str] | None = None,
    ) -> None:
        """Persist the latest snapshot, metadata, and delta state.

        Args:
            tracks: Full list of tracks to save
            processed_track_ids: Optional list of track IDs that were processed in this delta
        """
        if self.snapshot_service is None or not self.snapshot_service.is_enabled():
            return

        snapshot_hash = await self.snapshot_service.save_snapshot(tracks)
        current_time = self._current_time()
        library_mtime = await self.snapshot_service.get_library_mtime()

        metadata = LibraryCacheMetadata(
            last_full_scan=current_time,
            library_mtime=library_mtime,
            track_count=len(tracks),
            snapshot_hash=snapshot_hash,
        )
        await self.snapshot_service.update_snapshot_metadata(metadata)

        if not self.snapshot_service.is_delta_enabled():
            return

        delta_cache = await self.snapshot_service.load_delta()
        if delta_cache is None:
            delta_cache = LibraryDeltaCache(last_run=current_time)

        delta_cache.last_run = current_time
        if processed_track_ids and (ids_as_str := [str(track_id) for track_id in processed_track_ids if str(track_id)]):
            delta_cache.add_processed_ids(ids_as_str)

        await self.snapshot_service.save_delta(delta_cache)

    def can_use_snapshot(self) -> bool:
        """Check if snapshot service is available and enabled."""
        return self.snapshot_service is not None and self.snapshot_service.is_enabled()
