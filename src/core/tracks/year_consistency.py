"""What an album's own tracks say about its year: the majority year, Apple's release dates and when it was added.

None of it is a verdict: the most common year goes to the providers for a match, Apple's release date to the fallback
rules that judge their answer, the date added to the placeholder check, and the majority fills outliers only when the
providers have no year.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import logging

    from core.models.track_models import TrackDict


# Share of all album tracks a year needs to count as the majority; YearRetriever ships this value
DOMINANCE_MIN_SHARE = 0.6


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
    """Reads the year hints an album's tracks carry.

    Responsibilities:
    - The majority year of the album's tracks
    - The most common year, for comparison with provider answers
    - The release year Apple stores on the tracks, when they agree
    - The earliest year a track was added

    Args:
        console_logger: Logger for console output
        dominance_min_share: Share of all tracks a year needs to be the majority (0.0-1.0)
    """

    def __init__(self, *, console_logger: logging.Logger, dominance_min_share: float = DOMINANCE_MIN_SHARE) -> None:
        self.console_logger = console_logger
        self.dominance_min_share = dominance_min_share

    def get_majority_year(self, tracks: list[TrackDict]) -> str | None:
        """Return the year most of the album's tracks carry, or None.

        A majority covers at least `dominance_min_share` of all tracks, unset years included, and has no runner-up with
        the same count, so an album that is mostly unset or evenly split has none.

        Args:
            tracks: All tracks of the album

        Returns:
            The majority year, or None when there is none
        """
        years = self._collect_valid_years(tracks)
        if not years:
            return None
        ranked = Counter(years).most_common(2)
        if len(ranked) == 2 and ranked[0][1] == ranked[1][1]:
            return None
        year, count = ranked[0]
        return year if count >= len(tracks) * self.dominance_min_share else None

    @staticmethod
    def _collect_valid_years(tracks: list[TrackDict]) -> list[str]:
        """Collect non-empty years other than "0" from tracks."""
        years: list[str] = []
        for track in tracks:
            year = track.get("year")
            if year and str(year).strip() not in ["", "0"]:
                years.append(str(year))
        return years

    @staticmethod
    def get_most_common_year(tracks: list[TrackDict]) -> str | None:
        """Get most common year among tracks for comparison purposes.

        Unlike get_majority_year(), this needs no share of the tracks: it is the library year handed to the providers
        for comparison with their answer.

        Args:
            tracks: List of tracks to analyze

        Returns:
            Most common year string, or None if no valid years found
        """
        years = YearConsistencyChecker._collect_valid_years(tracks)
        if not years:
            return None

        year_counts: Counter[str] = Counter(years)
        return year_counts.most_common(1)[0][0]

    def get_consensus_release_year(self, tracks: list[TrackDict]) -> str | None:
        """Get release_year if all tracks agree (consensus).

        Args:
            tracks: List of tracks to check

        Returns:
            Consensus release_year string if found, None otherwise

        """
        release_years = [str(release_year) for track in tracks if (release_year := track.get("release_year", ""))]

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

    @staticmethod
    def get_earliest_track_added_year(tracks: list[TrackDict]) -> int | None:
        """Extract earliest year any track was added to library.

        The placeholder check reads it: a current-year majority on tracks added this year is a real release, on tracks
        added earlier Apple's placeholder.

        Args:
            tracks: List of tracks to analyze

        Returns:
            Earliest year a track was added, or None if no dates found

        """
        earliest: int | None = None
        for track in tracks:
            date_added = track.get("date_added")
            if not date_added:
                continue
            try:
                # Parse date_added format: "2025-10-01 00:19:04"
                year = int(str(date_added)[:4])
                if earliest is None or year < earliest:
                    earliest = year
            except (ValueError, TypeError, IndexError):
                continue
        return earliest
