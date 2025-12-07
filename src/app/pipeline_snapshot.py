"""Pipeline Snapshot Manager.

Manages track snapshots during pipeline execution for efficient delta processing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterable
    from datetime import datetime

    from core.models.track_models import TrackDict
    from core.tracks.track_delta import TrackDelta
    from core.tracks.track_processor import TrackProcessor


class PipelineSnapshotManager:
    """Manages track snapshots during pipeline execution.

    Maintains an in-memory cache of tracks for efficient updates during
    the pipeline run, avoiding repeated fetches from Music.app.
    """

    def __init__(
        self,
        track_processor: TrackProcessor,
        console_logger: logging.Logger,
    ) -> None:
        """Initialize the snapshot manager.

        Args:
            track_processor: Processor for fetching tracks by ID.
            console_logger: Logger for console output.
        """
        self._track_processor = track_processor
        self._console_logger = console_logger
        self._tracks_snapshot: list[TrackDict] | None = None
        self._tracks_index: dict[str, TrackDict] = {}
        self._captured_library_mtime: datetime | None = None

    def _reset_state(
        self,
        tracks: list[TrackDict] | None,
        library_mtime: datetime | None,
    ) -> None:
        """Reset internal state with new values.

        Args:
            tracks: New tracks snapshot or None to clear.
            library_mtime: Library modification time or None to clear.
        """
        self._tracks_snapshot = tracks
        self._tracks_index = {}
        self._captured_library_mtime = library_mtime

    def reset(self) -> None:
        """Reset cached pipeline tracks before a fresh run."""
        self._reset_state(None, None)

    def set_snapshot(
        self,
        tracks: list[TrackDict],
        *,
        library_mtime: datetime | None = None,
    ) -> None:
        """Store the current pipeline track snapshot for downstream reuse.

        Args:
            tracks: List of tracks to cache.
            library_mtime: Library modification time captured BEFORE fetching tracks.
                This prevents race conditions where new tracks added during fetch
                would be missed but metadata would incorrectly show library as unchanged.
        """
        self._reset_state(tracks, library_mtime)
        for track in tracks:
            if track_id := str(track.get("id", "")):
                self._tracks_index[track_id] = track

    def update_tracks(self, updated_tracks: Iterable[TrackDict]) -> None:
        """Apply field updates from processed tracks to the cached snapshot."""
        if not self._tracks_index:
            return

        for updated in updated_tracks:
            track_id = str(updated.get("id", ""))
            if not track_id:
                continue

            current_track = self._tracks_index.get(track_id)
            if current_track is None:
                continue

            for field, value in updated.model_dump().items():
                try:
                    setattr(current_track, field, value)
                except (AttributeError, TypeError, ValueError):
                    current_track.__dict__[field] = value

    def get_snapshot(self) -> list[TrackDict] | None:
        """Return the currently cached pipeline track snapshot."""
        return self._tracks_snapshot

    def clear(self) -> None:
        """Release cached pipeline track data after finishing the run."""
        self._reset_state(None, None)

    async def persist_to_disk(self) -> bool:
        """Persist the current in-memory snapshot to disk.

        This should be called at the end of the pipeline to ensure
        that genre/year changes are reflected in the disk snapshot.

        Returns:
            True if snapshot was persisted, False if no snapshot available.
        """
        if self._tracks_snapshot is None:
            self._console_logger.debug("No snapshot to persist")
            return False

        try:
            await self._track_processor.cache_manager.update_snapshot(
                self._tracks_snapshot,
                processed_track_ids=[str(t.id) for t in self._tracks_snapshot if t.id],
                library_mtime_override=self._captured_library_mtime,
            )
            self._console_logger.info(
                "Persisted pipeline snapshot to disk (%d tracks)",
                len(self._tracks_snapshot),
            )
            return True
        except Exception as exc:
            self._console_logger.warning("Failed to persist snapshot: %s", exc)
            return False

    async def merge_smart_delta(
        self,
        snapshot_tracks: list[TrackDict],
        delta: TrackDelta,
    ) -> list[TrackDict] | None:
        """Merge snapshot with Smart Delta changes.

        Args:
            snapshot_tracks: Current snapshot tracks.
            delta: Delta containing new, updated, and removed track IDs.

        Returns:
            Updated list of tracks if successful, otherwise None to indicate
            fallback should be used.
        """
        removed_ids = {str(track_id) for track_id in delta.removed_ids if track_id}
        changed_ids = [str(track_id) for track_id in delta.updated_ids if track_id]
        new_ids = [str(track_id) for track_id in delta.new_ids if track_id]

        fetch_order: list[str] = list(dict.fromkeys(new_ids + changed_ids))

        fetched_map: dict[str, TrackDict]
        if fetch_order:
            fetched_tracks = await self._track_processor.fetch_tracks_by_ids(fetch_order)
            fetched_map = {str(track.id): track for track in fetched_tracks}
            if missing_ids := [track_id for track_id in fetch_order if track_id not in fetched_map]:
                self._console_logger.warning(
                    "Smart Delta missing %d changed tracks (%s); falling back to batch scan",
                    len(missing_ids),
                    ", ".join(missing_ids[:5]),
                )
                return None
        else:
            fetched_map = {}

        merged_tracks: list[TrackDict] = []
        seen_ids: set[str] = set()

        for track in snapshot_tracks:
            track_id = str(track.id)
            if track_id in removed_ids:
                continue
            merged_tracks.append(fetched_map.get(track_id, track))
            seen_ids.add(track_id)

        for track_id, track in fetched_map.items():
            if track_id not in seen_ids:
                merged_tracks.append(track)
                seen_ids.add(track_id)

        self._console_logger.info(
            "Smart Delta merged: %d updated, %d new, %d removed tracks",
            len(changed_ids),
            len(new_ids),
            len(removed_ids),
        )

        return merged_tracks
