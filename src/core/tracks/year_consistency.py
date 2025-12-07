"""Year consistency checking logic extracted from YearRetriever.

This module handles year dominance calculation, parity detection,
consensus checking, and anomalous track identification.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.models.validators import is_empty_year

if TYPE_CHECKING:
    import logging

    from core.models.track_models import TrackDict


# Constants for year consistency checking
TOP_YEARS_COUNT = 2
PARITY_THRESHOLD = 1
DOMINANCE_MIN_SHARE = 0.5
DEFAULT_SUSPICION_THRESHOLD_YEARS = 10


def _is_reasonable_year(year: str) -> bool:
    """Check if year looks reasonable.

    Args:
        year: Year string to validate

    Returns:
        True if year looks reasonable, False otherwise

    """
    try:
        y = int(year)
        # Reasonable = 1900 to current year + 1
        return 1900 <= y <= datetime.now(tz=UTC).year + 1
    except (ValueError, TypeError):
        return False


class YearConsistencyChecker:
    """Handles year consistency analysis for album tracks.

    Responsibilities:
    - Calculate dominant year using majority rule
    - Detect year parity between top candidates
    - Find consensus release year across tracks
    - Identify tracks with anomalous years
    """

    def __init__(
        self,
        *,
        console_logger: logging.Logger,
        top_years_count: int = TOP_YEARS_COUNT,
        parity_threshold: int = PARITY_THRESHOLD,
        dominance_min_share: float = DOMINANCE_MIN_SHARE,
        suspicion_threshold_years: int = DEFAULT_SUSPICION_THRESHOLD_YEARS,
    ) -> None:
        """Initialize the year consistency checker.

        Args:
            console_logger: Logger for console output
            top_years_count: Number of top years to consider for parity
            parity_threshold: Max difference for parity detection
            dominance_min_share: Min share of tracks for dominance (0.0-1.0)
            suspicion_threshold_years: If dominant year is this many years older
                than earliest track added date, trigger API verification

        """
        self.console_logger = console_logger
        self.top_years_count = top_years_count
        self.parity_threshold = parity_threshold
        self.dominance_min_share = dominance_min_share
        self.suspicion_threshold_years = suspicion_threshold_years

    def get_dominant_year(self, tracks: list[TrackDict]) -> str | None:
        """Find dominant year among tracks using majority rule.

        Calculates dominance based on ALL tracks in album, not just tracks for years.
        A year is dominant only if >50% of ALL album tracks have that year.

        Note: Years "0" and empty strings are excluded from dominance calculation
        as they represent placeholder/default values in Music.app.

        Args:
            tracks: List of ALL tracks in the album to analyze

        Returns:
            Dominant year string if found, None if no clear majority or parity

        """
        years = self._collect_valid_years(tracks)
        if not years:
            return None

        year_counts: Counter[str] = Counter(years)
        total_tracks = len(tracks)
        most_common: tuple[str, int] = year_counts.most_common(1)[0]

        # Check for release_year inconsistency case
        if result := self._check_release_year_inconsistency(tracks, years, most_common[0]):
            return result

        # Check for clear majority
        if result := self._check_majority_dominance(most_common, total_tracks, tracks):
            return result

        # Handle collaboration albums (some empty years but otherwise consistent)
        if result := self._check_collaboration_pattern(year_counts, years, most_common, total_tracks, tracks):
            return result

        # Check for parity
        if self._check_year_parity(year_counts):
            return None

        # Most frequent year but not a strong majority
        self.console_logger.info(
            "No dominant year (below %.0f%%): %s has %d/%d album tracks (%.1f%%) - need API",
            self.dominance_min_share * 100,
            most_common[0],
            most_common[1],
            total_tracks,
            (most_common[1] / total_tracks) * 100,
        )
        return None

    @staticmethod
    def _collect_valid_years(tracks: list[TrackDict]) -> list[str]:
        """Collect non-empty, non-placeholder years from tracks."""
        years: list[str] = []
        for track in tracks:
            year = track.get("year")
            if year and str(year).strip() not in ["", "0"]:
                years.append(str(year))
        return years

    def _check_majority_dominance(self, most_common: tuple[str, int], total_tracks: int, tracks: list[TrackDict]) -> str | None:
        """Check if most common year has clear majority (>50% of all tracks)."""
        if most_common[1] < total_tracks * self.dominance_min_share:
            return None

        # Before trusting, check if year is suspiciously old
        if self._is_year_suspiciously_old(most_common[0], tracks):
            return None  # Trigger API verification

        self.console_logger.info(
            "Dominant year %s found (%d/%d tracks - %.1f%%)",
            most_common[0],
            most_common[1],
            total_tracks,
            (most_common[1] / total_tracks) * 100,
        )
        return most_common[0]

    def _check_collaboration_pattern(
        self,
        year_counts: Counter[str],
        years: list[str],
        most_common: tuple[str, int],
        total_tracks: int,
        tracks: list[TrackDict],
    ) -> str | None:
        """Handle collaboration albums: some empty years but otherwise consistent.

        Requires at least 50% of TOTAL tracks have this year to avoid
        wrong metadata pollution.
        """
        tracks_with_empty = [t for t in tracks if is_empty_year(t.get("year"))]
        if not (len(year_counts) == 1 and tracks_with_empty and years):
            return None

        year_ratio = len(years) / total_tracks
        if year_ratio < self.dominance_min_share:
            self.console_logger.info(
                "Not trusting year %s - only %d/%d tracks (%.1f%%) have it, rest are empty. Need API verification.",
                most_common[0],
                len(years),
                total_tracks,
                year_ratio * 100,
            )
            return None

        # Check for suspiciously old year before trusting
        if self._is_year_suspiciously_old(most_common[0], tracks):
            self.console_logger.info(
                "Not trusting year %s - suspiciously old. Need API.",
                most_common[0],
            )
            return None

        self.console_logger.info(
            "Using available year %s for %d tracks without years (collaboration album pattern, %.1f%% have year)",
            most_common[0],
            len(tracks_with_empty),
            year_ratio * 100,
        )
        return most_common[0]

    def _check_release_year_inconsistency(self, tracks: list[TrackDict], years: list[str], most_common_year: str) -> str | None:
        """Check if all tracks have same year but different release_years."""
        if len(set(years)) != 1:  # Not all tracks have same year
            return None

        release_years = [str(track.get("release_year")) for track in tracks if track.get("release_year") and str(track.get("release_year")).strip()]
        if len(set(release_years)) > 1:
            self.console_logger.info(
                "All tracks have same year %s but inconsistent release_years %s - using consistent track year",
                most_common_year,
                ", ".join(sorted(set(release_years))),
            )
            return most_common_year
        return None

    def _check_year_parity(self, year_counts: Counter[str]) -> bool:
        """Check if there's parity between top years."""
        top_two: list[tuple[str, int]] = year_counts.most_common(self.top_years_count)
        if len(top_two) != self.top_years_count:
            return False

        diff = abs(top_two[0][1] - top_two[1][1])
        if diff <= self.parity_threshold:
            self.console_logger.info(
                "Year parity detected: %s (%d) vs %s (%d) - need API",
                top_two[0][0],
                top_two[0][1],
                top_two[1][0],
                top_two[1][1],
            )
            return True
        return False

    def _is_year_suspiciously_old(self, dominant_year: str, tracks: list[TrackDict]) -> bool:
        """Check if dominant year is suspiciously old compared to when tracks were added.

        This catches cases where all tracks have an incorrect year (e.g., 2001)
        but were added to the library recently (e.g., 2025).

        Args:
            dominant_year: The dominant year found in tracks
            tracks: List of tracks to analyze

        Returns:
            True if year is suspiciously old (needs API verification)

        """
        try:
            dominant_year_int = int(dominant_year)
        except (ValueError, TypeError):
            return False

        # Find earliest track added date
        earliest_added_year: int | None = None
        for track in tracks:
            date_added = track.get("date_added")
            if not date_added:
                continue
            try:
                # Parse date_added format: "2025-10-01 00:19:04"
                added_year = int(str(date_added)[:4])
                if earliest_added_year is None or added_year < earliest_added_year:
                    earliest_added_year = added_year
            except (ValueError, TypeError, IndexError):
                continue

        if earliest_added_year is None:
            return False

        # Check if dominant year is suspiciously old
        year_gap = earliest_added_year - dominant_year_int
        if year_gap > self.suspicion_threshold_years:
            self.console_logger.warning(
                "Suspicious year detected: dominant year %s is %d years older than earliest track added (%d) - triggering API verification",
                dominant_year,
                year_gap,
                earliest_added_year,
            )
            return True

        return False

    def get_consensus_release_year(self, tracks: list[TrackDict]) -> str | None:
        """Get release_year if all tracks agree (consensus).

        Args:
            tracks: List of tracks to check

        Returns:
            Consensus release_year string if found, None otherwise

        """
        release_years = [str(track.get("release_year")) for track in tracks if track.get("release_year")]

        if not release_years:
            return None

        # Check if ALL tracks have the same release_year (consensus)
        unique_years = set(release_years)
        if len(unique_years) == 1:
            year = next(iter(unique_years))
            if _is_reasonable_year(year):
                self.console_logger.info(
                    "Consensus release_year: %s (all %d tracks agree)",
                    year,
                    len(release_years),
                )
                return year

        # Multiple release years - no consensus
        if len(unique_years) > 1:
            self.console_logger.info(
                "Multiple release_years found: %s - no consensus",
                ", ".join(f"{y} ({release_years.count(y)})" for y in unique_years),
            )

        return None

    def identify_anomalous_tracks(self, tracks: list[TrackDict], dominant_year: str) -> list[TrackDict]:
        """Identify tracks with years different from dominant year.

        Args:
            tracks: List of tracks to check
            dominant_year: The dominant year to compare against

        Returns:
            List of tracks with anomalous years

        """
        anomalous_tracks: list[TrackDict] = []
        for track in tracks:
            track_year = str(track.get("year", ""))

            # Track has year but it's not a dominant anomaly
            if track_year and track_year.strip() not in ["", "0"] and track_year != dominant_year:
                anomalous_tracks.append(track)
                self.console_logger.info(
                    "Track '%s' has anomalous year %s (dominant: %s)",
                    track.get("name", "Unknown"),
                    track_year,
                    dominant_year,
                )

        return anomalous_tracks
