"""Year fallback logic extracted from YearRetriever.

This module handles the decision logic for when to apply, skip, or preserve
year values based on confidence levels and existing data.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from core.models.album_type import (
    AlbumType,
    YearHandlingStrategy,
    detect_album_type,
)
from core.models.validators import is_empty_year

if TYPE_CHECKING:
    import logging

    from core.models.protocols import (
        ExternalApiServiceProtocol,
        PendingVerificationServiceProtocol,
    )
    from core.models.track_models import TrackDict

# Confidence threshold for trusting API over library
DEFAULT_TRUST_API_SCORE_THRESHOLD = 70  # Trust API if confidence >= this

# Minimum confidence to apply year when no existing year to validate against (Issue #105)
MIN_CONFIDENCE_FOR_NEW_YEAR = 30


class YearFallbackHandler:
    """Handles fallback logic for year decisions.

    Decision Tree:
    1. IF is_definitive=True → APPLY proposed year (high confidence from API)
    2. IF proposed_year < absurd_threshold AND no existing year → MARK + SKIP
    3. IF existing year is EMPTY → APPLY proposed year (nothing to preserve)
    4. IF is_special_album_type → MARK + PROPAGATE existing year to all tracks
    5. IF |proposed - existing| > THRESHOLD → MARK + PROPAGATE existing year to all tracks
    6. ELSE → APPLY proposed year

    Key principle: When we trust existing year over proposed year, we PROPAGATE
    the existing year to ALL tracks (including empty ones), not just preserve it.
    """

    def __init__(
        self,
        *,
        console_logger: logging.Logger,
        pending_verification: PendingVerificationServiceProtocol,
        fallback_enabled: bool,
        absurd_year_threshold: int,
        year_difference_threshold: int,
        trust_api_score_threshold: int = DEFAULT_TRUST_API_SCORE_THRESHOLD,
        api_orchestrator: ExternalApiServiceProtocol | None = None,
    ) -> None:
        """Initialize the year fallback handler.

        Args:
            console_logger: Logger for console output
            pending_verification: Service for marking albums for verification
            fallback_enabled: Whether fallback logic is enabled
            absurd_year_threshold: Years below this are considered absurd
            year_difference_threshold: Max allowed year difference before dramatic change
            trust_api_score_threshold: Trust API if confidence >= this value
            api_orchestrator: API orchestrator for artist data lookups (optional)

        """
        self.console_logger = console_logger
        self.pending_verification = pending_verification
        self.fallback_enabled = fallback_enabled
        self.absurd_year_threshold = absurd_year_threshold
        self.year_difference_threshold = year_difference_threshold
        self.trust_api_score_threshold = trust_api_score_threshold
        self.api_orchestrator = api_orchestrator

    async def apply_year_fallback(
        self,
        proposed_year: str,
        album_tracks: list[TrackDict],
        is_definitive: bool,
        confidence_score: int,
        artist: str,
        album: str,
    ) -> str | None:
        """Apply fallback logic for year decisions.

        Args:
            proposed_year: Year from API
            album_tracks: List of tracks in the album
            is_definitive: Whether API is confident in the year
            confidence_score: API confidence score (0-100)
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

        # Early exit: No change needed if existing year equals proposed year
        # BUT: still check plausibility - both could be wrong (e.g., year before artist started)
        if existing_year and existing_year == proposed_year:
            # Check if this matching year is plausible for the artist
            is_implausible = await self._check_matching_year_plausibility(
                year=existing_year,
                artist=artist,
                album=album,
            )
            if is_implausible:
                # Both existing and proposed years are implausible - mark for verification, skip update
                return None

            self.console_logger.debug(
                "[FALLBACK] No change needed for %s - %s (existing year %s matches proposed)",
                artist,
                album,
                existing_year,
            )
            return proposed_year

        # Rule 2: Absurd year detection (when no existing year to compare)
        if await self._handle_absurd_year(proposed_year, existing_year, artist, album):
            return None

        # Rule 2.5: Very low confidence with no existing year (Issue #105)
        # When no existing year to validate against, require minimum confidence
        if not existing_year and confidence_score < MIN_CONFIDENCE_FOR_NEW_YEAR:
            await self.pending_verification.mark_for_verification(
                artist=artist,
                album=album,
                reason="very_low_confidence_no_existing",
                metadata={
                    "proposed_year": proposed_year,
                    "confidence_score": confidence_score,
                    "threshold": MIN_CONFIDENCE_FOR_NEW_YEAR,
                },
            )
            self.console_logger.warning(
                "[FALLBACK] Skipping year %s for %s - %s (confidence %d%% below %d%% threshold, no existing year)",
                proposed_year,
                artist,
                album,
                confidence_score,
                MIN_CONFIDENCE_FOR_NEW_YEAR,
            )
            return None

        # Rule 3: No existing year - nothing to preserve (year passed absurd check and confidence check)
        if not existing_year:
            self.console_logger.debug(
                "[FALLBACK] Applying year %s for %s - %s (no existing year to preserve)",
                proposed_year,
                artist,
                album,
            )
            return proposed_year

        # Delegate remaining rules to helper method
        return await self._apply_existing_year_rules(proposed_year, existing_year, confidence_score, artist, album)

    async def _apply_existing_year_rules(
        self,
        proposed_year: str,
        existing_year: str,
        confidence_score: int,
        artist: str,
        album: str,
    ) -> str:
        """Apply rules when existing year is present.

        Handles special album types and dramatic year changes.

        Args:
            proposed_year: Year from API
            existing_year: Existing year from tracks
            confidence_score: API confidence score (0-100)
            artist: Artist name
            album: Album name

        Returns:
            Year to apply (proposed or existing)

        """
        # Rule 4: Check for special album types
        special_result = await self._handle_special_album_type(proposed_year, existing_year, artist, album)
        if special_result is not None:
            # If special album handler says "skip" (empty string), propagate existing year to all tracks
            return special_result if special_result != "" else existing_year

        # Rule 5: Check for dramatic year change (now considers confidence score and suspicious years)
        if await self._handle_dramatic_year_change(proposed_year, existing_year, confidence_score, artist, album):
            # Propagate existing year to all tracks instead of skipping entirely
            return existing_year

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
        """Handle absurd year detection. Returns True if it should skip."""
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
                "[FALLBACK] Skipping absurd year %s for %s - %s (year < %d threshold, no existing year to validate)",
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
                "[FALLBACK] Propagating existing year %s to all tracks for %s - %s (special album type: %s, pattern: '%s', rejected proposed: %s)",
                existing_year,
                artist,
                album,
                album_info.album_type.value,
                album_info.detected_pattern,
                proposed_year,
            )
            return ""  # Signal to propagate existing_year (handled by caller)

        # MARK_AND_UPDATE: continue with proposed year
        self.console_logger.info(
            "[FALLBACK] Updating year for %s - %s (reissue detected: %s)",
            artist,
            album,
            album_info.detected_pattern,
        )
        return proposed_year

    async def _check_year_plausibility(
        self,
        existing_year: str,
        proposed_year: str,
        artist: str,
    ) -> bool | None:
        """Check if years are plausible for the artist.

        Uses artist's career start year to determine if years are possible.
        If artist formed in 2015, a year of 2000 is impossible.

        Args:
            existing_year: Current year in library
            proposed_year: Year proposed by API
            artist: Artist name (for API lookup)

        Returns:
            True: proposed year is implausible → preserve existing year (skip update)
            False: existing year is implausible → apply API year
            None: can't determine or both are plausible → continue to next rule

        """
        if self.api_orchestrator is None:
            # No orchestrator available, can't check plausibility
            return None

        try:
            existing_int = int(existing_year)
        except (ValueError, TypeError):
            # Invalid existing year, apply API year
            return False

        try:
            proposed_int = int(proposed_year)
        except (ValueError, TypeError):
            # Invalid proposed year, preserve existing
            return True

        # Get artist's career start year
        artist_start = await self.api_orchestrator.get_artist_start_year(artist)

        if artist_start is None:
            # Can't verify artist data → continue to next rule (don't blindly apply)
            self.console_logger.debug(
                "[PLAUSIBILITY] No artist data found for '%s', continuing to next rule",
                artist,
            )
            return None

        # Check if PROPOSED year is before artist started → IMPOSSIBLE → preserve existing
        if proposed_int < artist_start:
            self.console_logger.info(
                "[PLAUSIBILITY] Proposed year %d is before artist '%s' started (%d), preserving existing %s",
                proposed_int,
                artist,
                artist_start,
                existing_year,
            )
            return True  # Skip update, preserve existing

        # Check if EXISTING year is before artist started → IMPOSSIBLE → apply API year
        if existing_int < artist_start:
            self.console_logger.info(
                "[PLAUSIBILITY] Existing year %d is before artist '%s' started (%d), applying API year %s",
                existing_int,
                artist,
                artist_start,
                proposed_year,
            )
            return False

        # Both years are plausible (after artist started)
        self.console_logger.debug(
            "[PLAUSIBILITY] Both years plausible for '%s' (started %d): existing=%d, proposed=%d",
            artist,
            artist_start,
            existing_int,
            proposed_int,
        )
        return None

    async def _check_matching_year_plausibility(
        self,
        year: str,
        artist: str,
        album: str,
    ) -> bool:
        """Check if a single year (when existing == proposed) is plausible for the artist.

        This handles the case where both library and API agree on the same wrong year.
        For example: Evanescence (formed 1995) with year 1994 - both sources are wrong.

        Args:
            year: The year to check (same for both existing and proposed)
            artist: Artist name (for API lookup)
            album: Album name (for marking verification)

        Returns:
            True: year is implausible (before artist started) - should skip and mark for verification
            False: year is plausible or can't determine - proceed normally

        """
        if self.api_orchestrator is None:
            return False

        try:
            year_int = int(year)
        except (ValueError, TypeError):
            return False

        # Get artist's career start year
        artist_start = await self.api_orchestrator.get_artist_start_year(artist)

        if artist_start is None:
            return False

        # Check if the year is before artist started → IMPOSSIBLE
        if year_int < artist_start:
            self.console_logger.warning(
                "[PLAUSIBILITY] Year %d is before artist '%s' started (%d) for album '%s' - "
                "both library and API agree on impossible year, marking for verification",
                year_int,
                artist,
                artist_start,
                album,
            )
            await self.pending_verification.mark_for_verification(
                artist=artist,
                album=album,
                reason="implausible_matching_year",
                metadata={
                    "year": year,
                    "artist_start_year": artist_start,
                    "plausibility": "year_before_artist_start",
                    "note": "Both library and API returned same impossible year",
                },
            )
            return True

        return False

    # noinspection PySimplifyBooleanCheck
    async def _handle_dramatic_year_change(
        self,
        proposed_year: str,
        existing_year: str,
        confidence_score: int,
        artist: str,
        album: str,
    ) -> bool:
        """Handle dramatic year changes. Returns True if should skip update.

        Logic:
        1. Not dramatic change → apply API year
        2. High confidence API (>=70%) → trust API, apply year
        3. NEW: Check plausibility → if existing is impossible → apply API year
        4. Low confidence + dramatic + plausible → preserve existing, mark for verification
        """
        if not self.is_year_change_dramatic(existing_year, proposed_year):
            return False

        # High confidence API → trust API despite dramatic change
        if confidence_score >= self.trust_api_score_threshold:
            self.console_logger.info(
                "[FALLBACK] Applying API year %s (confidence %d%%) despite dramatic change from %s for %s - %s",
                proposed_year,
                confidence_score,
                existing_year,
                artist,
                album,
            )
            return False  # Don't skip - apply the year

        # Check if years are plausible for the artist
        plausibility_result = await self._check_year_plausibility(
            existing_year=existing_year,
            proposed_year=proposed_year,
            artist=artist,
        )
        if plausibility_result is True:
            # Proposed year is implausible (before artist started) → propagate existing
            await self.pending_verification.mark_for_verification(
                artist=artist,
                album=album,
                reason="implausible_proposed_year",
                metadata={
                    "existing_year": existing_year,
                    "proposed_year": proposed_year,
                    "confidence_score": confidence_score,
                    "plausibility": "proposed_year_before_artist_start",
                },
            )
            return True  # Signals caller to propagate existing_year

        if plausibility_result is False:
            # Existing year is implausible → apply API year
            await self.pending_verification.mark_for_verification(
                artist=artist,
                album=album,
                reason="implausible_existing_year",
                metadata={
                    "existing_year": existing_year,
                    "proposed_year": proposed_year,
                    "confidence_score": confidence_score,
                    "plausibility": "existing_year_impossible",
                },
            )
            return False  # Don't skip - apply the year

        # Low confidence + dramatic change + both years plausible → preserve existing year
        await self.pending_verification.mark_for_verification(
            artist=artist,
            album=album,
            reason="suspicious_year_change",
            metadata={
                "existing_year": existing_year,
                "proposed_year": proposed_year,
                "year_difference": abs(int(existing_year) - int(proposed_year)),
                "confidence_score": confidence_score,
                "confidence": "low",
            },
        )
        self.console_logger.warning(
            "[FALLBACK] Propagating existing year %s to all tracks for %s - %s (rejected dramatic change to %s, diff > %d years, confidence %d%%)",
            existing_year,
            artist,
            album,
            proposed_year,
            self.year_difference_threshold,
            confidence_score,
        )
        return True  # Signals caller to propagate existing_year

    @staticmethod
    def get_existing_year_from_tracks(tracks: list[TrackDict]) -> str | None:
        """Extract the most common existing year from tracks.

        Uses Counter to find the most frequently occurring year among tracks.

        Args:
            tracks: List of tracks to analyze

        Returns:
            Most common year string, or None if no valid years found

        """
        years = [str(track.get("year")) for track in tracks if track.get("year") and not is_empty_year(track.get("year"))]
        if not years:
            return None
        counter = Counter(years)
        most_common = counter.most_common(1)
        return most_common[0][0] if most_common else None

    def is_year_change_dramatic(self, existing: str, proposed: str) -> bool:
        """Check if year change exceeds the threshold.

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
