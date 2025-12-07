"""Year determination logic for albums.

This module contains the core logic for determining album years
from various sources: local data, cache, and external APIs.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from core.debug_utils import debug
from core.models.track_status import is_prerelease_status
from core.models.validators import is_empty_year, is_valid_year

from .year_utils import resolve_non_negative_int, resolve_positive_int

if TYPE_CHECKING:
    import logging

    from core.models.protocols import (
        CacheServiceProtocol,
        ExternalApiServiceProtocol,
        PendingVerificationServiceProtocol,
    )
    from core.models.track_models import TrackDict

    from .year_consistency import YearConsistencyChecker
    from .year_fallback import YearFallbackHandler


# Constants
SUSPICIOUS_ALBUM_MIN_LEN = 3  # Album names with length <= 3 are suspicious
SUSPICIOUS_MANY_YEARS = 3  # If >= 3 unique years present, skip auto updates


class YearDeterminator:
    """Determines album year from various sources.

    Responsibilities:
    - Query cache for existing year data
    - Query external APIs when needed
    - Apply fallback logic for uncertain updates
    - Handle prerelease and suspicious albums
    - Skip albums that already have valid years
    """

    def __init__(
        self,
        *,
        cache_service: CacheServiceProtocol,
        external_api: ExternalApiServiceProtocol,
        pending_verification: PendingVerificationServiceProtocol,
        consistency_checker: YearConsistencyChecker,
        fallback_handler: YearFallbackHandler,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
    ) -> None:
        """Initialize the YearDeterminator.

        Args:
            cache_service: Cache service for storing/retrieving years
            external_api: External API service for fetching years
            pending_verification: Service for managing pending verifications
            consistency_checker: Checker for year consistency
            fallback_handler: Handler for fallback logic
            console_logger: Logger for console output
            error_logger: Logger for error messages
            config: Configuration dictionary

        """
        self.cache_service = cache_service
        self.external_api = external_api
        self.pending_verification = pending_verification
        self.consistency_checker = consistency_checker
        self.fallback_handler = fallback_handler
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.config = config

        # Extract configuration
        year_config = self.config.get("year_retrieval", {}) if isinstance(self.config, dict) else {}
        processing_config = year_config.get("processing", {}) if isinstance(year_config, dict) else {}

        self.skip_prerelease = bool(processing_config.get("skip_prerelease", True))
        self.future_year_threshold = resolve_non_negative_int(processing_config.get("future_year_threshold"), default=1)
        self.prerelease_recheck_days = resolve_positive_int(processing_config.get("prerelease_recheck_days"), default=30)

    async def determine_album_year(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
    ) -> str | None:
        """Determine year for album using simplified Linus approach.

        Order: dominant year -> consensus release_year -> cache -> API -> None

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks in the album

        Returns:
            Year string if found, None otherwise

        """
        if debug.year:
            self.console_logger.info(
                "determine_album_year called: artist='%s' album='%s'",
                artist,
                album,
            )

        # 1. Check for dominant year from track metadata
        if dominant_year := self.consistency_checker.get_dominant_year(album_tracks):
            return dominant_year

        # 2. Check cache (might have from previous API call)
        cached_year = await self.cache_service.get_album_year_from_cache(artist, album)
        if cached_year:
            return cached_year

        # 3. Check for consensus release_year
        if consensus_year := self.consistency_checker.get_consensus_release_year(album_tracks):
            await self.cache_service.store_album_year_in_cache(artist, album, consensus_year)
            return consensus_year

        # 4. API as last resort
        try:
            year_result, is_definitive = await self.external_api.get_album_year(artist, album)
        except (OSError, ValueError, RuntimeError) as e:
            if debug.year:
                self.console_logger.exception("Exception in get_album_year: %s", e)
                self.error_logger.exception("Full exception details:")
            return None

        if year_result:
            # Apply fallback logic to validate/skip the proposed year
            validated_year = await self.fallback_handler.apply_year_fallback(
                proposed_year=year_result,
                album_tracks=album_tracks,
                is_definitive=is_definitive,
                artist=artist,
                album=album,
            )

            if validated_year is not None:
                await self.cache_service.store_album_year_in_cache(artist, album, validated_year)
                return validated_year

        return None

    async def should_skip_album(
        self,
        album_tracks: list[TrackDict],
        artist: str,
        album: str,
        force: bool = False,
    ) -> bool:
        """Check if the album should be skipped due to existing years.

        Trusts cache (API data) over Music.app data.
        If cache year differs from library year, we process the album to update from cache.

        Args:
            album_tracks: List of tracks in the album
            artist: Artist name for logging
            album: Album name for logging
            force: If True, never skip - always process the album

        Returns:
            True if the album should be skipped, False otherwise

        """
        # Force mode bypasses all skip logic
        if force:
            self.console_logger.debug(
                "Force mode: processing '%s - %s' regardless of cache state",
                artist,
                album,
            )
            return False

        # Check cache first - API data is more reliable than Music.app
        cached_year = await self.cache_service.get_album_year_from_cache(artist, album)

        # Collect non-empty years from library
        non_empty_years = [
            str(track.get("year"))
            for track in album_tracks
            if track.get("year") and str(track.get("year")).strip() and is_valid_year(track.get("year"))
        ]

        if cached_year:
            if non_empty_years:
                year_counts = Counter(non_empty_years)
                library_year = year_counts.most_common(1)[0][0]

                if cached_year != library_year:
                    self.console_logger.info(
                        "Year mismatch for '%s - %s': cache=%s, library=%s - will update from cache",
                        artist,
                        album,
                        cached_year,
                        library_year,
                    )
                    return False

                self.console_logger.debug(
                    "Skipping '%s - %s' (cache=%s matches library)",
                    artist,
                    album,
                    cached_year,
                )
                return True

            self.console_logger.info(
                "Album '%s - %s' has no valid years but cache has %s - will apply from cache",
                artist,
                album,
                cached_year,
            )
            return False

        # No cache - need to query API to populate cache
        if tracks_with_empty_year := [track for track in album_tracks if is_empty_year(track.get("year"))]:
            self.console_logger.info(
                "Album '%s - %s' has %d tracks with empty year - will process",
                artist,
                album,
                len(tracks_with_empty_year),
            )
            return False

        if not non_empty_years:
            self.console_logger.debug("Album '%s - %s' has no valid years - will process", artist, album)
            return False

        self.console_logger.debug(
            "Album '%s - %s' not in cache - will query API to verify year",
            artist,
            album,
        )
        return False

    async def check_prerelease_status(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
    ) -> bool:
        """Check if the album should be skipped due to prerelease status.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks on the album

        Returns:
            True if the album should be skipped, False otherwise

        """
        if not self.skip_prerelease:
            return False

        prerelease_tracks = [
            track for track in album_tracks if is_prerelease_status(track.track_status if isinstance(track.track_status, str) else None)
        ]

        if prerelease_tracks:
            self.console_logger.info(
                "Skipping album '%s - %s': %d of %d tracks are prerelease (read-only)",
                artist,
                album,
                len(prerelease_tracks),
                len(album_tracks),
            )
            await self.pending_verification.mark_for_verification(
                artist,
                album,
                reason="prerelease",
                metadata={
                    "track_count": str(len(album_tracks)),
                    "prerelease_count": str(len(prerelease_tracks)),
                },
                recheck_days=self.prerelease_recheck_days,
            )
            return True

        return False

    async def check_suspicious_album(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
    ) -> bool:
        """Check if the album is suspicious and should be skipped.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: List of tracks

        Returns:
            True if album should be skipped, False otherwise

        """
        try:
            album_str = album or ""
            non_empty_years = [str(track.get("year")) for track in album_tracks if track.get("year") and str(track.get("year")).strip()]
            unique_years = set(non_empty_years) if non_empty_years else set()

            if len(album_str) <= SUSPICIOUS_ALBUM_MIN_LEN and len(unique_years) >= SUSPICIOUS_MANY_YEARS:
                self.console_logger.warning(
                    "Safety check: Suspicious album '%s - %s' detected (%d unique years, name length=%d). "
                    "Skipping automatic updates and marking for verification.",
                    artist,
                    album,
                    len(unique_years),
                    len(album_str),
                )
                await self.pending_verification.mark_for_verification(
                    artist,
                    album,
                    reason="suspicious_album_name",
                    metadata={
                        "unique_years": str(len(unique_years)),
                        "album_name_length": str(len(album_str)),
                    },
                )
                return True
        except (AttributeError, TypeError, ValueError) as e:
            self.error_logger.exception(
                "Error during suspicious album safety check for '%s - %s': %s",
                artist,
                album,
                e,
            )

        return False

    async def handle_future_years(
        self,
        artist: str,
        album: str,
        album_tracks: list[TrackDict],
        future_years: list[int],
    ) -> bool:
        """Handle case when future years are found in tracks.

        Args:
            artist: Artist name
            album: Album name
            album_tracks: Album tracks
            future_years: List of future years found

        Returns:
            True if album should be skipped

        """
        if not self.skip_prerelease or not future_years:
            return False

        current_year = datetime.now(UTC).year
        max_future_year = max(future_years)

        if max_future_year - current_year <= self.future_year_threshold:
            self.console_logger.debug(
                "Detected future year %s for '%s - %s' but within configured threshold (%d year(s)); continuing processing",
                max_future_year,
                artist,
                album,
                self.future_year_threshold,
            )
            return False

        self.console_logger.info(
            "Skipping prerelease album '%s - %s' with future year(s): %s",
            artist,
            album,
            max_future_year,
        )
        metadata = {
            "expected_year": str(max_future_year),
            "track_count": str(len(album_tracks)),
        }
        await self.pending_verification.mark_for_verification(
            artist,
            album,
            reason="prerelease",
            metadata=metadata,
            recheck_days=self.prerelease_recheck_days,
        )
        return True

    async def handle_release_years(
        self,
        artist: str,
        album: str,
        release_years: list[str],
    ) -> str | None:
        """Handle case when release years are found in track metadata.

        Args:
            artist: Artist name
            album: Album name
            release_years: List of release years found

        Returns:
            Year string if valid, None if it should skip

        """
        if not release_years:
            return None

        year_counts = Counter(release_years)
        most_common_year = year_counts.most_common(1)[0][0]

        self.console_logger.info(
            "Using release year %s from Music.app metadata for '%s - %s' (fallback)",
            most_common_year,
            artist,
            album,
        )

        await self.cache_service.store_album_year_in_cache(artist, album, most_common_year)
        return most_common_year

    @staticmethod
    def extract_future_years(album_tracks: list[TrackDict]) -> list[int]:
        """Extract future years from album tracks.

        Args:
            album_tracks: List of tracks from the album

        Returns:
            List of years that are in the future

        """
        current_year = datetime.now(UTC).year
        future_years: list[int] = []

        for track in album_tracks:
            year_value = track.get("year")
            if year_value is not None:
                try:
                    year_int = int(float(str(year_value)))
                    if year_int > current_year:
                        future_years.append(year_int)
                except (ValueError, TypeError):
                    continue

        return future_years

    @staticmethod
    def extract_release_years(album_tracks: list[TrackDict]) -> list[str]:
        """Extract valid release years from album tracks.

        Args:
            album_tracks: List of tracks from the album

        Returns:
            List of valid release years from track metadata

        """
        release_years: list[str] = []
        for track in album_tracks:
            release_year_value = track.get("release_year")
            if release_year_value and is_valid_year(release_year_value):
                release_years.append(str(release_year_value))
        return release_years
