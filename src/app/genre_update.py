"""Genre Update Service - standalone genre update operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging

    from app.track_cleaning import TrackCleaningService
    from core.models.track_models import TrackDict
    from core.tracks.artist_renamer import ArtistRenamer
    from core.tracks.genre_manager import GenreManager
    from core.tracks.track_processor import TrackProcessor


class GenreUpdateService:
    """Service for standalone genre update operations."""

    def __init__(
        self,
        track_processor: TrackProcessor,
        genre_manager: GenreManager,
        config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        cleaning_service: TrackCleaningService | None = None,
        artist_renamer: ArtistRenamer | None = None,
    ) -> None:
        """Initialize genre update service.

        Args:
            track_processor: Processor for fetching tracks.
            genre_manager: Manager for genre updates.
            config: Application configuration.
            console_logger: Logger for console output.
            error_logger: Logger for error output.
            cleaning_service: Optional service for metadata cleaning.
            artist_renamer: Optional service for artist renaming.
        """
        self._track_processor = track_processor
        self._genre_manager = genre_manager
        self._config = config
        self._console_logger = console_logger
        self._error_logger = error_logger
        self._cleaning_service = cleaning_service
        self._artist_renamer = artist_renamer
        self._test_artists: set[str] | None = None

    def set_test_artists(self, test_artists: set[str] | None) -> None:
        """Set test artists for filtering.

        Args:
            test_artists: Set of artist names to filter to, or None to process all.
        """
        self._test_artists = test_artists

    async def get_tracks_for_genre_update(self, artist: str | None) -> list[TrackDict] | None:
        """Get tracks for genre update based on artist filter.

        Args:
            artist: Optional artist filter.

        Returns:
            List of tracks or None if not found.
        """
        fetched_tracks: list[TrackDict]
        if artist is None:
            fetched_tracks = await self._track_processor.fetch_tracks_in_batches()
        else:
            fetched_tracks = await self._track_processor.fetch_tracks_async(artist=artist)

        # Filter by test_artists if in test mode
        if self._test_artists and fetched_tracks:
            fetched_tracks = [t for t in fetched_tracks if t.get("artist") in self._test_artists]
            self._console_logger.info(
                "Test mode: filtered to %d tracks for %d test artists",
                len(fetched_tracks),
                len(self._test_artists),
            )

        if not fetched_tracks:
            self._console_logger.warning(
                "No tracks found for genre update (artist=%s, test_mode=%s)",
                artist or "all",
                bool(self._test_artists),
            )
            return None
        return fetched_tracks

    async def run_update_genres(self, artist: str | None, force: bool) -> None:
        """Update genres for all or specific artist.

        Args:
            artist: Optional artist filter.
            force: Force update even if genre exists.
        """
        self._console_logger.info(
            "Starting genre update operation%s",
            f" for artist: {artist}" if artist else " for all artists",
        )

        tracks = await self.get_tracks_for_genre_update(artist)
        if not tracks:
            return

        # Preprocessing - clean metadata first
        if self._cleaning_service:
            self._console_logger.info("Preprocessing: Cleaning metadata...")
            await self._cleaning_service.clean_all_metadata_with_logs(tracks)

        # Preprocessing - rename artists
        if self._artist_renamer and self._artist_renamer.has_mapping:
            self._console_logger.info("Preprocessing: Renaming artists...")
            await self._artist_renamer.rename_tracks(tracks)

        # Update genres
        self._console_logger.info("Updating genres...")
        await self._genre_manager.update_genres_by_artist_async(tracks, force=force)

        self._console_logger.info("Genre update operation completed")
