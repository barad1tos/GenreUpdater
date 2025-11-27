"""Year fallback logic extracted from YearRetriever.

This module handles the decision logic for when to apply, skip, or preserve
year values based on confidence levels and existing data.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from src.core.models.album_type import (
    AlbumType,
    YearHandlingStrategy,
    detect_album_type,
)
from src.core.models.validators import is_empty_year

if TYPE_CHECKING:
    import logging

    from src.core.models.protocols import PendingVerificationServiceProtocol
    from src.core.models.track_models import TrackDict


class YearFallbackHandler:
    """Handles fallback logic for year decisions.

    Decision Tree:
    1. IF is_definitive=True → APPLY year (high confidence from API)
    2. IF proposed_year < absurd_threshold AND no existing year → MARK + SKIP
    3. IF existing year is EMPTY → APPLY year (nothing to preserve)
    4. IF is_special_album_type → MARK for verification + SKIP/UPDATE
    5. IF |proposed - existing| > THRESHOLD → MARK + PRESERVE existing
    6. ELSE → APPLY year
    """

    def __init__(
        self,
        *,
        console_logger: logging.Logger,
        pending_verification: PendingVerificationServiceProtocol,
        fallback_enabled: bool,
        absurd_year_threshold: int,
        year_difference_threshold: int,
    ) -> None:
        """Initialize the year fallback handler.

        Args:
            console_logger: Logger for console output
            pending_verification: Service for marking albums for verification
            fallback_enabled: Whether fallback logic is enabled
            absurd_year_threshold: Years below this are considered absurd
            year_difference_threshold: Max allowed year difference before suspicious

        """
        self.console_logger = console_logger
        self.pending_verification = pending_verification
        self.fallback_enabled = fallback_enabled
        self.absurd_year_threshold = absurd_year_threshold
        self.year_difference_threshold = year_difference_threshold

    async def apply_year_fallback(
        self,
        proposed_year: str,
        album_tracks: list[TrackDict],
        is_definitive: bool,
        artist: str,
        album: str,
    ) -> str | None:
        """Apply fallback logic for year decisions.

        Args:
            proposed_year: Year from API
            album_tracks: List of tracks in the album
            is_definitive: Whether API is confident in the year
            artist: Artist name
            album: Album name

        Returns:
            Year to apply (proposed or existing), or None to skip update

        """
        # Fallback disabled - use original behavior (always apply, mark low confidence)
        if not self.fallback_enabled:
            if not is_definitive:
                await self.pending_verification.mark_for_verification(artist, album)
            return proposed_year

        # Rule 1: High confidence from API - apply directly
        if is_definitive:
            self.console_logger.debug(
                "[FALLBACK] Applying year %s for %s - %s (high confidence)",
                proposed_year,
                artist,
                album,
            )
            return proposed_year

        # Get existing year from tracks (needed for subsequent rules)
        existing_year = self.get_existing_year_from_tracks(album_tracks)

        # Rule 2: Absurd year detection (when no existing year to compare)
        if await self._handle_absurd_year(proposed_year, existing_year, artist, album):
            return None

        # Rule 3: No existing year - nothing to preserve (year passed absurd check)
        if not existing_year:
            self.console_logger.debug(
                "[FALLBACK] Applying year %s for %s - %s (no existing year to preserve)",
                proposed_year,
                artist,
                album,
            )
            return proposed_year

        # Rule 4: Check for special album types
        special_result = await self._handle_special_album_type(
            proposed_year, existing_year, artist, album
        )
        if special_result is not None:
            return special_result if special_result != "" else None

        # Rule 5: Check for dramatic year change
        if await self._handle_dramatic_year_change(
            proposed_year, existing_year, artist, album
        ):
            return None

        # Rule 6: Apply year (low confidence but reasonable change)
        self.console_logger.debug(
            "[FALLBACK] Applying year %s for %s - %s (low confidence but reasonable change)",
            proposed_year,
            artist,
            album,
        )
        return proposed_year

    async def _handle_absurd_year(
        self,
        proposed_year: str,
        existing_year: str | None,
        artist: str,
        album: str,
    ) -> bool:
        """Handle absurd year detection. Returns True if should skip."""
        try:
            proposed_int = int(proposed_year)
            is_absurd = proposed_int < self.absurd_year_threshold
        except (ValueError, TypeError):
            is_absurd = False

        if is_absurd and not existing_year:
            await self.pending_verification.mark_for_verification(
                artist=artist,
                album=album,
                reason="absurd_year_no_existing",
                metadata={
                    "proposed_year": proposed_year,
                    "absurd_threshold": self.absurd_year_threshold,
                    "confidence": "very_low",
                },
            )
            self.console_logger.warning(
                "[FALLBACK] Skipping absurd year %s for %s - %s "
                "(year < %d threshold, no existing year to validate)",
                proposed_year,
                artist,
                album,
                self.absurd_year_threshold,
            )
            return True
        return False

    async def _handle_special_album_type(
        self,
        proposed_year: str,
        existing_year: str,
        artist: str,
        album: str,
    ) -> str | None:
        """Handle special album types. Returns year to apply, empty string to skip, or None to continue."""
        album_info = detect_album_type(album)
        if album_info.album_type == AlbumType.NORMAL:
            return None  # Continue to next rule

        reason = f"special_album_{album_info.album_type.value}"
        await self.pending_verification.mark_for_verification(
            artist=artist,
            album=album,
            reason=reason,
            metadata={
                "existing_year": existing_year,
                "proposed_year": proposed_year,
                "album_type": album_info.album_type.value,
                "detected_pattern": album_info.detected_pattern,
                "confidence": "low",
            },
        )

        if album_info.strategy == YearHandlingStrategy.MARK_AND_SKIP:
            self.console_logger.warning(
                "[FALLBACK] Skipping year update for %s - %s "
                "(special album type: %s, pattern: '%s'). "
                "Existing: %s, Proposed: %s",
                artist,
                album,
                album_info.album_type.value,
                album_info.detected_pattern,
                existing_year,
                proposed_year,
            )
            return ""  # Signal to skip

        # MARK_AND_UPDATE: continue with proposed year
        self.console_logger.info(
            "[FALLBACK] Updating year for %s - %s (reissue detected: %s)",
            artist,
            album,
            album_info.detected_pattern,
        )
        return proposed_year

    async def _handle_dramatic_year_change(
        self,
        proposed_year: str,
        existing_year: str,
        artist: str,
        album: str,
    ) -> bool:
        """Handle dramatic year changes. Returns True if should skip."""
        if not self.is_year_change_dramatic(existing_year, proposed_year):
            return False

        await self.pending_verification.mark_for_verification(
            artist=artist,
            album=album,
            reason="suspicious_year_change",
            metadata={
                "existing_year": existing_year,
                "proposed_year": proposed_year,
                "year_difference": abs(int(existing_year) - int(proposed_year)),
                "confidence": "low",
            },
        )
        self.console_logger.warning(
            "[FALLBACK] Preserving existing year %s for %s - %s "
            "(dramatic change to %s detected, diff > %d years)",
            existing_year,
            artist,
            album,
            proposed_year,
            self.year_difference_threshold,
        )
        return True

    @staticmethod
    def get_existing_year_from_tracks(tracks: list[TrackDict]) -> str | None:
        """Extract most common existing year from tracks.

        Uses Counter to find the most frequently occurring year among tracks.

        Args:
            tracks: List of tracks to analyze

        Returns:
            Most common year string, or None if no valid years found

        """
        years = [
            str(track.get("year"))
            for track in tracks
            if track.get("year") and not is_empty_year(track.get("year"))
        ]
        if not years:
            return None
        counter = Counter(years)
        most_common = counter.most_common(1)
        return most_common[0][0] if most_common else None

    def is_year_change_dramatic(self, existing: str, proposed: str) -> bool:
        """Check if year change exceeds threshold.

        A dramatic change (e.g., 2018→1998) suggests the API returned
        a reissue/compilation year rather than the original.

        Args:
            existing: Current year value
            proposed: Proposed new year value

        Returns:
            True if difference exceeds year_difference_threshold

        """
        try:
            existing_int = int(existing)
            proposed_int = int(proposed)
            difference = abs(existing_int - proposed_int)
            return difference > self.year_difference_threshold
        except (ValueError, TypeError):
            return False
