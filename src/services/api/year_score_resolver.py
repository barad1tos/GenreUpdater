"""Year score resolution logic extracted from ExternalApiOrchestrator.

This module handles the scoring and selection of the best release year
from multiple API responses with potentially conflicting years.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from src.core.tracks.year_retriever import is_valid_year

if TYPE_CHECKING:
    import logging

    from src.services.api.api_base import ScoredRelease


# Constants for year scoring thresholds
MAX_LOGGED_YEARS = 5
VERY_HIGH_SCORE_THRESHOLD = 75  # Score threshold for automatic acceptance
MIN_REISSUE_YEAR_DIFFERENCE = 2  # Minimum years between original and reissue
MIN_YEAR_GAP_FOR_REISSUE_DETECTION = 4  # Minimum year gap to detect reissue scenarios
MAX_SUSPICIOUS_YEAR_DIFFERENCE = 3  # Maximum years difference before suspicious
MIN_CONFIDENT_SCORE_THRESHOLD = 85  # Minimum score to consider result confident


class YearScoreResolver:
    """Resolves the best release year from scored API responses.

    Handles:
    - Aggregating scores by year across multiple API sources
    - Selecting the best year considering future dates and reissues
    - Determining if the result is definitive
    """

    def __init__(
        self,
        *,
        console_logger: logging.Logger,
        min_valid_year: int,
        current_year: int,
        definitive_score_threshold: int,
        definitive_score_diff: int,
    ) -> None:
        """Initialize the year score resolver.

        Args:
            console_logger: Logger for console output
            min_valid_year: Minimum year to consider valid (e.g., 1900)
            current_year: The current calendar year
            definitive_score_threshold: Min score to consider definitive
            definitive_score_diff: Min difference to prefer one year over another

        """
        self.console_logger = console_logger
        self.min_valid_year = min_valid_year
        self.current_year = current_year
        self.definitive_score_threshold = definitive_score_threshold
        self.definitive_score_diff = definitive_score_diff

    def aggregate_year_scores(
        self, all_releases: list[ScoredRelease]
    ) -> defaultdict[str, list[int]]:
        """Aggregate release scores by year, filtering out invalid years."""
        year_scores: defaultdict[str, list[int]] = defaultdict(list)

        for release in all_releases:
            year_value = release.get("year")
            year = str(year_value) if year_value is not None else None
            score = int(release.get("score", 0))
            if year and is_valid_year(year, self.min_valid_year, self.current_year):
                year_scores[year].append(score)

        return year_scores

    def select_best_year(
        self, year_scores: defaultdict[str, list[int]]
    ) -> tuple[str, bool]:
        """Select the best year from aggregated scores and determine if definitive."""
        final_year_scores = self._compute_final_year_scores(year_scores)
        sorted_years = self._sort_years_by_score(final_year_scores)
        self._log_ranked_years(sorted_years)

        best_year, best_score, best_year_is_future = self._determine_best_year_candidate(
            sorted_years
        )

        # Initialize variables to avoid potential unbound variable errors
        score_thresholds = self._calculate_score_thresholds(best_score)
        has_score_conflict = self._check_score_conflicts(sorted_years, best_year_is_future)

        # Check for suspiciously old years when we have only one result
        if len(sorted_years) == 1:
            best_year, is_definitive = self._validate_single_result(best_year, best_score)
            if not is_definitive:
                self.console_logger.warning(
                    "Single result validation failed for year %s - marking as non-definitive",
                    best_year,
                )
                return best_year, False
        else:
            is_definitive = self._determine_definitiveness(
                score_thresholds, best_year_is_future, has_score_conflict
            )

        if not is_definitive:
            self._log_non_definitive_reasons(
                best_year_is_future, score_thresholds, has_score_conflict, best_score
            )

        return best_year, is_definitive

    @staticmethod
    def _compute_final_year_scores(
        year_scores: defaultdict[str, list[int]]
    ) -> dict[str, int]:
        """Get the maximum score for each year."""
        return {year: max(scores) for year, scores in year_scores.items() if scores}

    @staticmethod
    def _sort_years_by_score(
        final_year_scores: dict[str, int]
    ) -> list[tuple[str, int]]:
        """Sort years primarily by score (desc), secondarily by year (asc)."""
        return sorted(final_year_scores.items(), key=lambda item: (-item[1], int(item[0])))

    def _log_ranked_years(self, sorted_years: list[tuple[str, int]]) -> None:
        """Log the ranked years for debugging."""
        log_scores = ", ".join([f"{y}:{s}" for y, s in sorted_years[:MAX_LOGGED_YEARS]])
        truncation_indicator = "..." if len(sorted_years) > MAX_LOGGED_YEARS else ""
        self.console_logger.info(
            "Ranked year scores (Year:MaxScore): %s%s", log_scores, truncation_indicator
        )

    def _determine_best_year_candidate(
        self, sorted_years: list[tuple[str, int]]
    ) -> tuple[str, int, bool]:
        """Determine the best year candidate.

        Handles future vs non-future and reissue vs original preferences.
        """
        best_year, best_score = sorted_years[0]
        best_year_is_future = int(best_year) > self.current_year

        # If we have multiple candidates, check for future vs non-future preference
        if len(sorted_years) > 1 and best_year_is_future:
            best_year, best_score, best_year_is_future = self._apply_future_year_preference(
                sorted_years, best_year, best_score, best_year_is_future
            )

        # After handling future year preference, check for original vs reissue preference
        if len(sorted_years) > 1 and not best_year_is_future:
            best_year, best_score = self._apply_original_release_preference(
                sorted_years, best_year, best_score
            )

        return best_year, best_score, best_year_is_future

    def _apply_future_year_preference(
        self,
        sorted_years: list[tuple[str, int]],
        best_year: str,
        best_score: int,
        best_year_is_future: bool,
    ) -> tuple[str, int, bool]:
        """Apply preference for non-future years when scores are close."""
        second_year, second_best_score = sorted_years[1]
        second_is_future = int(second_year) > self.current_year
        score_difference = best_score - second_best_score

        if score_difference < self.definitive_score_diff and not second_is_future:
            self.console_logger.info(
                "Preferring non-future year %s over future %s (scores: %d vs %d)",
                second_year,
                best_year,
                second_best_score,
                best_score,
            )
            return second_year, second_best_score, False

        return best_year, best_score, best_year_is_future

    def _apply_original_release_preference(
        self,
        sorted_years: list[tuple[str, int]],
        best_year: str,
        best_score: int,
    ) -> tuple[str, int]:
        """Apply preference for earlier years (original releases) over later years (reissues)."""
        # Only apply this logic if the best year seems like it could be a reissue
        # (i.e., there are significantly earlier years with similar scores)
        best_year_int = int(best_year)

        # Enhanced reissue detection: check for large year gaps
        all_years = [int(year_str) for year_str, _ in sorted_years]
        effective_score_threshold = self._calculate_reissue_threshold(
            all_years, best_year_int, best_year
        )

        # Look for earlier years that might be the original release
        valid_candidates = self._find_original_release_candidates(
            sorted_years, best_year_int, best_score, effective_score_threshold
        )

        # If we found valid candidates, pick the earliest year
        if valid_candidates:
            return self._select_earliest_candidate(
                valid_candidates, best_year, best_year_int, best_score, effective_score_threshold
            )

        return best_year, best_score

    def _calculate_reissue_threshold(
        self,
        all_years: list[int],
        best_year_int: int,
        best_year: str,
    ) -> int:
        """Calculate effective score threshold for reissue detection."""
        if len(all_years) > 1:
            earliest_year = min(all_years)
            latest_year = max(all_years)
            year_gap = latest_year - earliest_year

            # If there's a significant gap, and the best year is not the earliest,
            # it's likely a reissue scenario
            if year_gap > MIN_YEAR_GAP_FOR_REISSUE_DETECTION and best_year_int > earliest_year:
                self.console_logger.info(
                    "[ORIGINAL_RELEASE_FIX] Detected potential reissue scenario: "
                    "year range %d-%d (%d years gap), best year %s not earliest",
                    earliest_year,
                    latest_year,
                    year_gap,
                    best_year,
                )
                # Increase preference for earlier years in reissue scenarios
                return self.definitive_score_diff * 2

        return self.definitive_score_diff

    def _find_original_release_candidates(
        self,
        sorted_years: list[tuple[str, int]],
        best_year_int: int,
        best_score: int,
        effective_score_threshold: int,
    ) -> list[tuple[str, int]]:
        """Find earlier years that might be the original release."""
        valid_candidates: list[tuple[str, int]] = []

        for candidate_year, candidate_score in sorted_years[1:]:
            candidate_year_int = int(candidate_year)
            score_difference = best_score - candidate_score
            year_difference = best_year_int - candidate_year_int

            # If we find an earlier year within the score threshold, and it's at least
            # a few years earlier, add it as a candidate for the likely original release
            if (
                score_difference <= effective_score_threshold
                and year_difference >= MIN_REISSUE_YEAR_DIFFERENCE
            ):
                valid_candidates.append((candidate_year, candidate_score))

            # If the score difference is significant, stop looking
            if score_difference >= self.definitive_score_diff:
                break

        return valid_candidates

    def _select_earliest_candidate(
        self,
        valid_candidates: list[tuple[str, int]],
        best_year: str,
        best_year_int: int,
        best_score: int,
        effective_score_threshold: int,
    ) -> tuple[str, int]:
        """Select the earliest year from valid candidates."""
        earliest_candidate_tuple: tuple[str, int] = min(
            valid_candidates, key=lambda x: int(x[0])
        )
        selected_year: str = earliest_candidate_tuple[0]
        selected_score: int = earliest_candidate_tuple[1]
        year_difference = best_year_int - int(selected_year)

        self.console_logger.info(
            "[ORIGINAL_RELEASE_FIX] Preferring earliest year %s over later year %s "
            "(likely original vs reissue, scores: %d vs %d, year diff: %d, threshold: %d)",
            selected_year,
            best_year,
            selected_score,
            best_score,
            year_difference,
            effective_score_threshold,
        )
        return selected_year, selected_score

    def _validate_single_result(
        self, best_year: str, best_score: int
    ) -> tuple[str, bool]:
        """Validate single API results for suspicious old years."""
        year_int = int(best_year)
        current_year = self.current_year

        # If the year is suspiciously old compared to current year (>3 years difference)
        # and we only got one result with a low-to-medium score, be cautious
        year_diff = current_year - year_int

        if (
            year_diff > MAX_SUSPICIOUS_YEAR_DIFFERENCE
            and best_score < MIN_CONFIDENT_SCORE_THRESHOLD
        ):
            self.console_logger.warning(
                "SINGLE_RESULT_VALIDATION: Year %s is %d years old with only score %d "
                "from single API - this could be incorrect metadata, marking as non-definitive",
                best_year,
                year_diff,
                best_score,
            )
            return best_year, False

        # Otherwise, apply normal score thresholds
        score_thresholds = self._calculate_score_thresholds(best_score)
        is_definitive = score_thresholds["high_score_met"]

        return best_year, is_definitive

    def _calculate_score_thresholds(self, best_score: int) -> dict[str, bool]:
        """Calculate various score threshold checks."""
        return {
            "very_high_score": best_score >= VERY_HIGH_SCORE_THRESHOLD,
            "high_score_met": best_score >= self.definitive_score_threshold,
        }

    def _check_score_conflicts(
        self, sorted_years: list[tuple[str, int]], best_year_is_future: bool
    ) -> bool:
        """Check for score conflicts between competing years."""
        if len(sorted_years) <= 1:
            self.console_logger.debug("Only one candidate year found.")
            return False

        best_year, best_score = sorted_years[0]
        second_year, second_best_score = sorted_years[1]
        score_difference = best_score - second_best_score

        if score_difference >= self.definitive_score_diff:
            self.console_logger.debug(
                "Clear score winner: %s:%d vs %s:%d (diff=%d)",
                best_year,
                best_score,
                second_year,
                second_best_score,
                score_difference,
            )
            return False

        return self._evaluate_score_conflict(
            best_year,
            best_score,
            second_year,
            second_best_score,
            score_difference,
            best_year_is_future,
        )

    def _evaluate_score_conflict(
        self,
        best_year: str,
        best_score: int,
        second_year: str,
        second_best_score: int,
        score_difference: int,
        best_year_is_future: bool,
    ) -> bool:
        """Evaluate whether close scores constitute a conflict."""
        second_is_future = int(second_year) > self.current_year

        if not best_year_is_future and second_is_future:
            self.console_logger.debug(
                "Keeping non-future year %s over future %s", best_year, second_year
            )
            return False

        # Both future or both non-future with similar scores = conflict
        self.console_logger.debug(
            "Score conflict: %s:%d vs %s:%d (diff=%d, threshold=%d)",
            best_year,
            best_score,
            second_year,
            second_best_score,
            score_difference,
            self.definitive_score_diff,
        )
        return True

    @staticmethod
    def _determine_definitiveness(
        score_thresholds: dict[str, bool],
        best_year_is_future: bool,
        has_score_conflict: bool,
    ) -> bool:
        """Determine if the year selection is definitive."""
        return (
            score_thresholds["high_score_met"]
            and not best_year_is_future
            and (score_thresholds["very_high_score"] or not has_score_conflict)
        )

    def _log_non_definitive_reasons(
        self,
        best_year_is_future: bool,
        score_thresholds: dict[str, bool],
        has_score_conflict: bool,
        best_score: int,
    ) -> None:
        """Log reasons why the year selection is not definitive."""
        reason: list[str] = []
        if best_year_is_future:
            reason.append("future year")
        if not score_thresholds["high_score_met"]:
            reason.append(f"score {best_score} < {self.definitive_score_threshold}")
        if has_score_conflict and not score_thresholds["very_high_score"]:
            reason.append("competing years with similar scores")
        self.console_logger.debug("Not definitive: %s", ", ".join(reason))
