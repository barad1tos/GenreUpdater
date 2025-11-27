"""Track Cleaning Service.

Handles metadata cleaning operations for tracks (removing promotional text,
normalizing names, etc.).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.core.models.metadata_utils import clean_names
from src.core.models.track_models import ChangeLogEntry, TrackFieldValue

if TYPE_CHECKING:
    import logging

    from src.core.models.track_models import TrackDict
    from src.core.tracks.track_processor import TrackProcessor


class TrackCleaningService:
    """Service for cleaning track and album metadata.

    Removes promotional text, normalizes names, and handles batch cleaning
    operations with proper change logging.
    """

    def __init__(
        self,
        track_processor: TrackProcessor,
        config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Initialize the cleaning service.

        Args:
            track_processor: Processor for updating tracks.
            config: Application configuration.
            console_logger: Logger for console output.
            error_logger: Logger for error output.
        """
        self._track_processor = track_processor
        self._config = config
        self._console_logger = console_logger
        self._error_logger = error_logger

    def extract_and_clean_metadata(self, track: TrackDict) -> tuple[TrackFieldValue, str, TrackFieldValue, TrackFieldValue, str, str]:
        """Extract and clean track metadata.

        Args:
            track: Track data to process.

        Returns:
            Tuple of (track_id, artist_name, track_name, album_name,
                     cleaned_track_name, cleaned_album_name).
        """
        artist_name = str(track.get("artist", ""))
        track_name = track.get("name", "")
        album_name = track.get("album", "")
        track_id = track.get("id", "")

        cleaned_track_name, cleaned_album_name = clean_names(
            artist=artist_name,
            track_name=str(track_name),
            album_name=str(album_name),
            config=self._config,
            console_logger=self._console_logger,
            error_logger=self._error_logger,
        )

        return track_id, artist_name, track_name, album_name, cleaned_track_name, cleaned_album_name

    @staticmethod
    def _create_change_log_entry(
        track_id: str,
        artist: str,
        original_track_name: str,
        original_album_name: str,
        cleaned_track_name: str,
        cleaned_album_name: str,
    ) -> ChangeLogEntry:
        """Create a change log entry for metadata cleaning.

        Args:
            track_id: Track ID.
            artist: Artist name.
            original_track_name: Original track name.
            original_album_name: Original album name.
            cleaned_track_name: Cleaned track name.
            cleaned_album_name: Cleaned album name.

        Returns:
            ChangeLogEntry with cleaning details.
        """
        return ChangeLogEntry(
            change_type="metadata_cleaning",
            track_id=track_id,
            artist=artist,
            album_name=cleaned_album_name,
            track_name=cleaned_track_name,
            old_track_name=original_track_name,
            new_track_name=cleaned_track_name,
            old_album_name=original_album_name,
            new_album_name=cleaned_album_name,
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def process_single_track(
        self,
        track: TrackDict,
        artist_override: str | None = None,
    ) -> tuple[TrackDict | None, ChangeLogEntry | None]:
        """Process a single track for cleaning.

        This unified method handles both standalone cleaning and pipeline cleaning.

        Args:
            track: Track data to process.
            artist_override: Optional artist name override for logging.
                            If None, uses artist from track metadata.

        Returns:
            Tuple of (updated_track, change_entry) or (None, None) if no update needed.
        """
        # Extract and clean track metadata
        track_id, artist_name, track_name, album_name, cleaned_track_name, cleaned_album_name = self.extract_and_clean_metadata(track)

        if not track_id:
            return None, None

        # Check if update needed
        if cleaned_track_name == track_name and cleaned_album_name == album_name:
            return None, None

        # Update track
        success = await self._track_processor.update_track_async(
            track_id=str(track_id),
            new_track_name=(cleaned_track_name if cleaned_track_name != track_name else None),
            new_album_name=(cleaned_album_name if cleaned_album_name != album_name else None),
            original_artist=artist_name,
            original_album=str(album_name) if album_name is not None else None,
            original_track=str(track_name) if track_name is not None else None,
        )

        if not success:
            return None, None

        # Create updated track
        updated_track = track.copy()
        updated_track.name = cleaned_track_name
        updated_track.album = cleaned_album_name

        # Create change entry (use override if provided, otherwise use artist from track)
        change_entry = self._create_change_log_entry(
            track_id=str(track_id),
            artist=artist_override or artist_name,
            original_track_name=str(track_name) if track_name is not None else "",
            original_album_name=str(album_name) if album_name is not None else "",
            cleaned_track_name=cleaned_track_name,
            cleaned_album_name=cleaned_album_name,
        )

        return updated_track, change_entry

    async def process_track_for_pipeline(self, track: TrackDict) -> TrackDict | None:
        """Process a single track for pipeline cleaning (no change log).

        Args:
            track: Track data to process.

        Returns:
            Updated track or None if no update needed.
        """
        cleaned_track_name, cleaned_album_name = clean_names(
            artist=str(track.get("artist", "")),
            track_name=str(track.get("name", "")),
            album_name=str(track.get("album", "")),
            config=self._config,
            console_logger=self._console_logger,
            error_logger=self._error_logger,
        )

        track_name = track.get("name", "")
        album_name = track.get("album", "")
        if cleaned_track_name == track_name and cleaned_album_name == album_name:
            return None

        track_id = track.get("id", "")
        if not track_id:
            return None

        success = await self._track_processor.update_track_async(
            track_id=str(track_id),
            new_track_name=(cleaned_track_name if cleaned_track_name != track_name else None),
            new_album_name=(cleaned_album_name if cleaned_album_name != album_name else None),
            original_artist=str(track.get("artist", "")),
            original_album=str(album_name) if album_name is not None else None,
            original_track=str(track_name) if track_name is not None else None,
        )

        if not success:
            return None

        updated_track = track.copy()
        updated_track.name = cleaned_track_name
        updated_track.album = cleaned_album_name
        return updated_track

    async def process_all_tracks(
        self,
        tracks: list[TrackDict],
        artist: str,
    ) -> tuple[list[TrackDict], list[ChangeLogEntry]]:
        """Process multiple tracks for cleaning with change logging.

        Args:
            tracks: List of tracks to process.
            artist: Artist name for logging.

        Returns:
            Tuple of (updated_tracks, changes_log).
        """
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        for track in tracks:
            updated_track, change_entry = await self.process_single_track(track, artist_override=artist)
            if updated_track:
                updated_tracks.append(updated_track)
            if change_entry:
                changes_log.append(change_entry)

        return updated_tracks, changes_log

    async def clean_all_metadata(self, tracks: list[TrackDict]) -> list[TrackDict]:
        """Clean metadata for all tracks (Step 1 of pipeline).

        Args:
            tracks: List of tracks to clean.

        Returns:
            List of tracks that were updated.
        """
        cleaned_tracks: list[TrackDict] = []

        for track in tracks:
            cleaned_track = await self.process_track_for_pipeline(track)
            if cleaned_track:
                cleaned_tracks.append(cleaned_track)

        if cleaned_tracks:
            self._console_logger.info("Cleaned metadata for %d tracks", len(cleaned_tracks))

        return cleaned_tracks

    async def clean_all_metadata_with_logs(self, tracks: list[TrackDict]) -> list[ChangeLogEntry]:
        """Clean metadata for all tracks with change logging.

        Args:
            tracks: List of tracks to clean.

        Returns:
            List of change log entries for cleaned tracks.
        """
        changes_log: list[ChangeLogEntry] = []

        for track in tracks:
            _, change_entry = await self.process_single_track(track)
            if change_entry:
                changes_log.append(change_entry)

        if changes_log:
            self._console_logger.info("Cleaned metadata for %d tracks", len(changes_log))

        return changes_log
