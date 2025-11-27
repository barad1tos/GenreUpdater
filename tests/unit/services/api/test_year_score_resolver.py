"""Tests for YearScoreResolver - resolving best release year from API scores."""

import logging
from collections import defaultdict
from typing import Any

import pytest

from src.services.api.api_base import ScoredRelease
from src.services.api.year_score_resolver import (
    MAX_LOGGED_YEARS,
    MIN_REISSUE_YEAR_DIFFERENCE,
    MIN_YEAR_GAP_FOR_REISSUE_DETECTION,
    VERY_HIGH_SCORE_THRESHOLD,
    YearScoreResolver,
)


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test.year_score_resolver")


@pytest.fixture
def resolver(logger: logging.Logger) -> YearScoreResolver:
    """Create a YearScoreResolver instance with default settings."""
    return YearScoreResolver(
        console_logger=logger,
        min_valid_year=1900,
        current_year=2024,
        definitive_score_threshold=70,
        definitive_score_diff=15,
    )


def create_scored_release(year: str, score: int, **kwargs: Any) -> ScoredRelease:
    """Helper to create a ScoredRelease dict."""
    release: ScoredRelease = {
        "title": kwargs.get("title", "Test Album"),
        "year": year,
        "score": score,
        "artist": kwargs.get("artist", "Test Artist"),
        "album_type": kwargs.get("album_type", "Album"),
        "country": kwargs.get("country", "US"),
        "status": kwargs.get("status", "official"),
        "format": kwargs.get("format", "CD"),
        "label": kwargs.get("label"),
        "catalog_number": kwargs.get("catalog_number"),
        "barcode": kwargs.get("barcode"),
        "disambiguation": kwargs.get("disambiguation", ""),
        "source": kwargs.get("source", "musicbrainz"),
    }
    return release


class TestInitialization:
    """Tests for YearScoreResolver initialization."""

    def test_init_stores_parameters(self, logger: logging.Logger) -> None:
        """Test initialization stores all parameters."""
        resolver = YearScoreResolver(
            console_logger=logger,
            min_valid_year=1950,
            current_year=2025,
            definitive_score_threshold=80,
            definitive_score_diff=20,
        )

        assert resolver.min_valid_year == 1950
        assert resolver.current_year == 2025
        assert resolver.definitive_score_threshold == 80
        assert resolver.definitive_score_diff == 20


class TestAggregateYearScores:
    """Tests for aggregate_year_scores method."""

    def test_aggregates_scores_by_year(self, resolver: YearScoreResolver) -> None:
        """Test aggregates scores correctly."""
        releases = [
            create_scored_release("2020", 85),
            create_scored_release("2020", 90),
            create_scored_release("2021", 75),
        ]

        result = resolver.aggregate_year_scores(releases)

        assert len(result["2020"]) == 2
        assert 85 in result["2020"]
        assert 90 in result["2020"]
        assert len(result["2021"]) == 1

    def test_filters_invalid_years(self, resolver: YearScoreResolver) -> None:
        """Test filters out invalid years."""
        releases = [
            create_scored_release("2020", 85),
            create_scored_release("1800", 90),  # Too old
            create_scored_release("2030", 80),  # Future year (beyond 2024)
        ]

        result = resolver.aggregate_year_scores(releases)

        assert "2020" in result
        assert "1800" not in result
        assert "2030" not in result

    def test_handles_none_year(self, resolver: YearScoreResolver) -> None:
        """Test handles None year values."""
        releases = [
            create_scored_release("2020", 85),
            {"year": None, "score": 90, "title": "Test", "artist": "Test"},  # type: ignore[typeddict-item]
        ]

        result = resolver.aggregate_year_scores(releases)  # type: ignore[arg-type]

        assert "2020" in result
        assert len(result) == 1

    def test_handles_empty_list(self, resolver: YearScoreResolver) -> None:
        """Test handles empty release list."""
        result = resolver.aggregate_year_scores([])

        assert len(result) == 0


class TestSelectBestYear:
    """Tests for select_best_year method."""

    def test_selects_highest_score_year(self, resolver: YearScoreResolver) -> None:
        """Test selects year with highest score."""
        year_scores: defaultdict[str, list[int]] = defaultdict(list)
        year_scores["2020"] = [85, 90]
        year_scores["2021"] = [75, 80]

        best_year, _is_definitive = resolver.select_best_year(year_scores)

        assert best_year == "2020"

    def test_is_definitive_with_high_score(self, resolver: YearScoreResolver) -> None:
        """Test result is definitive with high score and no conflict."""
        year_scores: defaultdict[str, list[int]] = defaultdict(list)
        year_scores["2020"] = [90]

        best_year, is_definitive = resolver.select_best_year(year_scores)

        assert best_year == "2020"
        assert is_definitive is True

    def test_not_definitive_with_low_score(self, resolver: YearScoreResolver) -> None:
        """Test result is not definitive with low score."""
        year_scores: defaultdict[str, list[int]] = defaultdict(list)
        year_scores["2020"] = [50]

        best_year, is_definitive = resolver.select_best_year(year_scores)

        assert best_year == "2020"
        assert is_definitive is False

    def test_prefers_non_future_year(self, resolver: YearScoreResolver) -> None:
        """Test prefers non-future year when scores are close."""
        year_scores: defaultdict[str, list[int]] = defaultdict(list)
        year_scores["2025"] = [85]  # Future year
        year_scores["2023"] = [80]  # Past year

        best_year, _is_definitive = resolver.select_best_year(year_scores)

        # Should prefer 2023 (non-future) over 2025 (future) when scores are close
        assert best_year == "2023"

    def test_keeps_future_year_with_large_score_diff(
        self, resolver: YearScoreResolver
    ) -> None:
        """Test keeps future year when score difference is large."""
        year_scores: defaultdict[str, list[int]] = defaultdict(list)
        year_scores["2025"] = [95]  # Future year with much higher score
        year_scores["2023"] = [50]  # Past year

        best_year, _is_definitive = resolver.select_best_year(year_scores)

        assert best_year == "2025"


class TestComputeFinalYearScores:
    """Tests for _compute_final_year_scores static method."""

    def test_returns_max_score_per_year(self) -> None:
        """Test returns maximum score for each year."""
        year_scores: defaultdict[str, list[int]] = defaultdict(list)
        year_scores["2020"] = [70, 85, 90]
        year_scores["2021"] = [60, 75]

        result = YearScoreResolver._compute_final_year_scores(year_scores)

        assert result["2020"] == 90
        assert result["2021"] == 75

    def test_skips_empty_score_lists(self) -> None:
        """Test skips years with empty score lists."""
        year_scores: defaultdict[str, list[int]] = defaultdict(list)
        year_scores["2020"] = [85]
        year_scores["2021"] = []

        result = YearScoreResolver._compute_final_year_scores(year_scores)

        assert "2020" in result
        assert "2021" not in result


class TestSortYearsByScore:
    """Tests for _sort_years_by_score static method."""

    def test_sorts_by_score_descending(self) -> None:
        """Test sorts years by score descending."""
        year_scores = {"2020": 90, "2021": 75, "2019": 85}

        result = YearScoreResolver._sort_years_by_score(year_scores)

        assert result[0] == ("2020", 90)
        assert result[1] == ("2019", 85)
        assert result[2] == ("2021", 75)

    def test_sorts_by_year_ascending_on_tie(self) -> None:
        """Test sorts by year ascending when scores are equal."""
        year_scores = {"2021": 85, "2019": 85, "2020": 85}

        result = YearScoreResolver._sort_years_by_score(year_scores)

        assert result[0][0] == "2019"
        assert result[1][0] == "2020"
        assert result[2][0] == "2021"


class TestDetermineBestYearCandidate:
    """Tests for _determine_best_year_candidate method."""

    def test_returns_top_year_when_single(self, resolver: YearScoreResolver) -> None:
        """Test returns top year when only one candidate."""
        sorted_years = [("2020", 90)]

        year, score, is_future = resolver._determine_best_year_candidate(sorted_years)

        assert year == "2020"
        assert score == 90
        assert is_future is False


class TestApplyFutureYearPreference:
    """Tests for _apply_future_year_preference method."""

    def test_prefers_non_future_when_scores_close(
        self, resolver: YearScoreResolver
    ) -> None:
        """Test prefers non-future year when scores are close."""
        sorted_years = [("2025", 85), ("2023", 80)]

        year, _score, is_future = resolver._apply_future_year_preference(
            sorted_years, "2025", 85, True
        )

        assert year == "2023"
        assert is_future is False

    def test_keeps_future_when_score_diff_large(
        self, resolver: YearScoreResolver
    ) -> None:
        """Test keeps future year when score difference is large."""
        sorted_years = [("2025", 95), ("2023", 60)]

        year, _score, is_future = resolver._apply_future_year_preference(
            sorted_years, "2025", 95, True
        )

        assert year == "2025"
        assert is_future is True


class TestApplyOriginalReleasePreference:
    """Tests for _apply_original_release_preference method."""

    def test_prefers_earlier_year_for_reissue(
        self, resolver: YearScoreResolver
    ) -> None:
        """Test prefers earlier year when scores are similar (reissue detection)."""
        sorted_years = [("2020", 85), ("2010", 80)]

        year, _score = resolver._apply_original_release_preference(
            sorted_years, "2020", 85
        )

        # With 10 year gap and close scores, should prefer 2010 (original)
        assert year == "2010"

    def test_keeps_later_year_with_large_score_diff(
        self, resolver: YearScoreResolver
    ) -> None:
        """Test keeps later year when score difference is significant."""
        sorted_years = [("2020", 95), ("2010", 50)]

        year, _score = resolver._apply_original_release_preference(
            sorted_years, "2020", 95
        )

        assert year == "2020"


class TestValidateSingleResult:
    """Tests for _validate_single_result method."""

    def test_marks_old_low_score_as_non_definitive(
        self, resolver: YearScoreResolver
    ) -> None:
        """Test marks suspiciously old year with low score as non-definitive."""
        best_year, is_definitive = resolver._validate_single_result("2015", 50)

        # 2015 is >3 years old (current_year=2024) with low score
        assert best_year == "2015"
        assert is_definitive is False

    def test_accepts_recent_year_with_good_score(
        self, resolver: YearScoreResolver
    ) -> None:
        """Test accepts recent year with good score."""
        best_year, is_definitive = resolver._validate_single_result("2023", 80)

        assert best_year == "2023"
        assert is_definitive is True


class TestCalculateScoreThresholds:
    """Tests for _calculate_score_thresholds method."""

    def test_very_high_score(self, resolver: YearScoreResolver) -> None:
        """Test very high score threshold."""
        thresholds = resolver._calculate_score_thresholds(VERY_HIGH_SCORE_THRESHOLD + 5)

        assert thresholds["very_high_score"] is True

    def test_high_score_met(self, resolver: YearScoreResolver) -> None:
        """Test high score threshold met."""
        # definitive_score_threshold is 70
        thresholds = resolver._calculate_score_thresholds(75)

        assert thresholds["high_score_met"] is True

    def test_low_score(self, resolver: YearScoreResolver) -> None:
        """Test low score doesn't meet thresholds."""
        thresholds = resolver._calculate_score_thresholds(50)

        assert thresholds["very_high_score"] is False
        assert thresholds["high_score_met"] is False


class TestCheckScoreConflicts:
    """Tests for _check_score_conflicts method."""

    def test_no_conflict_with_single_year(self, resolver: YearScoreResolver) -> None:
        """Test no conflict when only one year."""
        sorted_years = [("2020", 90)]

        result = resolver._check_score_conflicts(sorted_years, False)

        assert result is False

    def test_no_conflict_with_clear_winner(self, resolver: YearScoreResolver) -> None:
        """Test no conflict when score difference is large."""
        sorted_years = [("2020", 90), ("2019", 60)]

        result = resolver._check_score_conflicts(sorted_years, False)

        assert result is False

    def test_conflict_with_close_scores(self, resolver: YearScoreResolver) -> None:
        """Test conflict when scores are close."""
        sorted_years = [("2020", 85), ("2019", 82)]

        result = resolver._check_score_conflicts(sorted_years, False)

        assert result is True


class TestDeterminDefinitiveness:
    """Tests for _determine_definitiveness static method."""

    def test_definitive_with_high_score_no_conflict(self) -> None:
        """Test definitive when high score and no conflict."""
        thresholds = {"very_high_score": False, "high_score_met": True}

        result = YearScoreResolver._determine_definitiveness(
            thresholds, best_year_is_future=False, has_score_conflict=False
        )

        assert result is True

    def test_not_definitive_with_future_year(self) -> None:
        """Test not definitive when best year is future."""
        thresholds = {"very_high_score": True, "high_score_met": True}

        result = YearScoreResolver._determine_definitiveness(
            thresholds, best_year_is_future=True, has_score_conflict=False
        )

        assert result is False

    def test_not_definitive_with_low_score(self) -> None:
        """Test not definitive when score threshold not met."""
        thresholds = {"very_high_score": False, "high_score_met": False}

        result = YearScoreResolver._determine_definitiveness(
            thresholds, best_year_is_future=False, has_score_conflict=False
        )

        assert result is False

    def test_definitive_with_very_high_score_despite_conflict(self) -> None:
        """Test definitive when very high score despite conflict."""
        thresholds = {"very_high_score": True, "high_score_met": True}

        result = YearScoreResolver._determine_definitiveness(
            thresholds, best_year_is_future=False, has_score_conflict=True
        )

        assert result is True


class TestConstants:
    """Tests for module constants."""

    def test_max_logged_years(self) -> None:
        """Test MAX_LOGGED_YEARS is positive."""
        assert MAX_LOGGED_YEARS > 0

    def test_very_high_score_threshold(self) -> None:
        """Test VERY_HIGH_SCORE_THRESHOLD is reasonable."""
        assert 70 <= VERY_HIGH_SCORE_THRESHOLD <= 100

    def test_min_reissue_year_difference(self) -> None:
        """Test MIN_REISSUE_YEAR_DIFFERENCE is positive."""
        assert MIN_REISSUE_YEAR_DIFFERENCE > 0

    def test_min_year_gap_for_reissue_detection(self) -> None:
        """Test MIN_YEAR_GAP_FOR_REISSUE_DETECTION is positive."""
        assert MIN_YEAR_GAP_FOR_REISSUE_DETECTION > 0


class TestIntegration:
    """Integration tests combining aggregate and select."""

    def test_full_workflow_clear_winner(self, resolver: YearScoreResolver) -> None:
        """Test full workflow with clear winner."""
        releases = [
            create_scored_release("2020", 95),
            create_scored_release("2020", 90),
            create_scored_release("2019", 60),
        ]

        year_scores = resolver.aggregate_year_scores(releases)
        best_year, is_definitive = resolver.select_best_year(year_scores)

        assert best_year == "2020"
        assert is_definitive is True

    def test_full_workflow_reissue_detection(
        self, resolver: YearScoreResolver
    ) -> None:
        """Test full workflow detects reissue and prefers original."""
        releases = [
            create_scored_release("2020", 85),  # Reissue
            create_scored_release("2005", 82),  # Original
        ]

        year_scores = resolver.aggregate_year_scores(releases)
        best_year, _is_definitive = resolver.select_best_year(year_scores)

        # Should prefer 2005 (original) due to large year gap
        assert best_year == "2005"

    def test_full_workflow_multiple_sources(self, resolver: YearScoreResolver) -> None:
        """Test full workflow with multiple API sources."""
        releases = [
            create_scored_release("2020", 85, source="musicbrainz"),
            create_scored_release("2020", 90, source="discogs"),
            create_scored_release("2021", 75, source="lastfm"),
        ]

        year_scores = resolver.aggregate_year_scores(releases)
        best_year, _is_definitive = resolver.select_best_year(year_scores)

        assert best_year == "2020"
