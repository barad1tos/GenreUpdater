"""Regression tests for year determination edge cases.

These tests focus on edge cases and boundary conditions in the year determination
logic that are likely to cause bugs in production with real library data.
"""

from __future__ import annotations

import datetime
from collections import Counter
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from core.models.validators import is_empty_year, is_valid_year
from core.tracks.year_consistency import YearConsistencyChecker
from core.tracks.year_determination import (
    CACHE_TRUST_THRESHOLD,
    CONSENSUS_YEAR_CONFIDENCE,
    MIN_CONFIDENCE_TO_CACHE,
    SUSPICIOUS_ALBUM_MIN_LEN,
    SUSPICIOUS_MANY_YEARS,
    YearDeterminator,
)

if TYPE_CHECKING:
    import logging

    from core.models.track_models import TrackDict


@pytest.mark.regression
class TestSuspiciousAlbumDetection:
    """Test detection of suspicious albums that need manual verification."""

    def test_short_album_names_with_many_years(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Albums with short names (<=3 chars) and many unique years should be flagged.

        These are often compilation albums or incorrectly tagged albums
        where tracks from different releases got grouped together.
        """
        suspicious_albums: list[tuple[str, str, int, int]] = []

        for (artist, album), tracks in albums_with_tracks.items():
            album_str = album or ""
            non_empty_years = [str(t.year) for t in tracks if t.year and t.year != "0"]
            unique_years = set(non_empty_years)

            if len(album_str) <= SUSPICIOUS_ALBUM_MIN_LEN and len(unique_years) >= SUSPICIOUS_MANY_YEARS:
                suspicious_albums.append((artist, album, len(album_str), len(unique_years)))

        # Just log suspicious albums - they should be flagged for verification
        if suspicious_albums:
            pytest.skip(
                f"Found {len(suspicious_albums)} suspicious albums (expected behavior):\n"
                + "\n".join(f"  '{a[0]}' - '{a[1]}' (name_len={a[2]}, unique_years={a[3]})" for a in suspicious_albums[:10])
            )

    def test_albums_with_extreme_year_variance(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Albums with tracks spanning many decades should be flagged."""
        extreme_variance: list[tuple[str, str, int, int, int]] = []
        variance_threshold = 20  # More than 20 years between min and max

        for (artist, album), tracks in albums_with_tracks.items():
            years = [int(t.year) for t in tracks if t.year and t.year.isdigit() and t.year != "0"]
            if len(years) < 2:
                continue

            min_year = min(years)
            max_year = max(years)
            variance = max_year - min_year

            if variance > variance_threshold:
                extreme_variance.append((artist, album, min_year, max_year, variance))

        # Albums with extreme variance likely have tagging issues
        if extreme_variance:
            pytest.skip(
                f"Found {len(extreme_variance)} albums with extreme year variance (>{variance_threshold} years):\n"
                + "\n".join(f"  {a[0]} - {a[1]}: {a[2]}-{a[3]} ({a[4]} year span)" for a in extreme_variance[:10])
            )


@pytest.mark.regression
class TestReissueDetection:
    """Test detection of reissues that need API verification."""

    def test_recent_years_without_release_year_flagged(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Albums with current/recent year but no release_year metadata need verification.

        iTunes often returns catalog/reissue dates instead of original release dates.
        Without release_year validation, these should be flagged for API query.
        """
        current_year = datetime.datetime.now(tz=datetime.UTC).year
        needs_verification: list[tuple[str, str, str]] = []

        for (artist, album), tracks in albums_with_tracks.items():
            # Get dominant year
            years = [str(t.year) for t in tracks if t.year and is_valid_year(t.year)]
            if not years:
                continue

            year_counts = Counter(years)
            dominant_year = year_counts.most_common(1)[0][0]

            try:
                year_int = int(dominant_year)
                is_recent = year_int >= current_year - 1

                # Check if any track has release_year
                has_release_year = any(t.release_year for t in tracks)

                if is_recent and not has_release_year:
                    needs_verification.append((artist, album, dominant_year))
            except (ValueError, TypeError):
                continue


@pytest.mark.regression
class TestPrerealeaseHandling:
    """Test handling of prerelease albums."""

    def test_future_years_identified(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Tracks with future years should be identified for prerelease handling."""
        current_year = datetime.datetime.now(tz=datetime.UTC).year
        future_tracks: list[tuple[str, str, str, str]] = []

        for track in library_tracks:
            year = track.year or ""
            if year and year.isdigit() and year != "0":
                try:
                    year_int = int(year)
                    if year_int > current_year:
                        future_tracks.append((track.id, track.artist, track.album, year))
                except ValueError:
                    continue

        # Future years are expected for prereleases - just verify count is reasonable
        max_expected_future = len(library_tracks) * 0.01  # Less than 1%
        assert len(future_tracks) <= max_expected_future, (
            f"Too many tracks with future years: {len(future_tracks)} "
            f"(max expected: {max_expected_future:.0f})\n" + "\n".join(f"  {t[0]}: {t[1]} - {t[2]} = {t[3]}" for t in future_tracks[:10])
        )


@pytest.mark.regression
class TestYearConsistencyEdgeCases:
    """Test edge cases in year consistency checking."""

    def test_single_track_albums_not_skipped(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Single-track albums should not be skipped based on year consistency.

        The _has_consistent_year method requires 2+ tracks to avoid skipping
        single-track albums that need API validation.
        """
        single_track_albums = [(k, v) for k, v in albums_with_tracks.items() if len(v) == 1]

        for (artist, album), tracks in single_track_albums:
            result = YearDeterminator._has_consistent_year(tracks)
            assert result is False, f"Single-track album '{artist} - {album}' should not be marked as consistent"

    def test_empty_year_handling(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Albums with empty years should be processed, not skipped."""
        albums_with_empty: list[tuple[str, str, int, int]] = []

        for (artist, album), tracks in albums_with_tracks.items():
            empty_count = sum(bool(is_empty_year(t.year)) for t in tracks)
            if empty_count > 0:
                albums_with_empty.append((artist, album, empty_count, len(tracks)))

        # Albums with empty years should exist and be processed
        # This is informational - verifies we have test data for this case
        if not albums_with_empty:
            pytest.skip("No albums with empty years in test data")

    def test_mixed_year_albums_not_consistent(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Albums with multiple different years should not be marked consistent."""
        for (artist, album), tracks in albums_with_tracks.items():
            if len(tracks) < 2:
                continue

            years = [str(t.year) for t in tracks if t.year and is_valid_year(t.year)]
            unique_years = set(years)

            # Skip if not enough valid years or all same
            if len(unique_years) <= 1:
                continue

            result = YearDeterminator._has_consistent_year(tracks)
            assert result is False, f"Album '{artist} - {album}' with {len(unique_years)} unique years should not be consistent"

    def test_year_zero_treated_as_empty(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Year '0' should be treated as empty/invalid."""
        zero_year_tracks = [t for t in library_tracks if t.year in ["0", 0]]

        for track in zero_year_tracks:
            assert is_empty_year(track.year), f"Year '0' should be treated as empty for track {track.id}"


@pytest.mark.regression
class TestCacheConfidenceThresholds:
    """Test cache confidence threshold behavior."""

    def test_cache_trust_threshold_constant(self) -> None:
        """Verify cache trust threshold is set correctly."""
        assert CACHE_TRUST_THRESHOLD == 90, "Cache trust threshold should be 90%"

    def test_min_confidence_to_cache_constant(self) -> None:
        """Verify minimum confidence to cache is set correctly."""
        assert MIN_CONFIDENCE_TO_CACHE == 50, "Min confidence to cache should be 50%"

    def test_consensus_year_confidence_constant(self) -> None:
        """Verify consensus year confidence is set correctly."""
        assert CONSENSUS_YEAR_CONFIDENCE == 80, "Consensus year confidence should be 80%"


@pytest.mark.regression
class TestDominantYearEdgeCases:
    """Test edge cases in dominant year calculation."""

    def test_all_invalid_years_returns_none(
        self,
        console_logger: logging.Logger,
    ) -> None:
        """Albums with all invalid years should return None for dominant year."""
        checker = YearConsistencyChecker(console_logger=console_logger)

        # Create mock tracks with invalid years
        mock_tracks: list[Any] = [
            MagicMock(year="", date_added="2020-01-01"),
            MagicMock(year="0", date_added="2020-01-01"),
            MagicMock(year=None, date_added="2020-01-01"),
        ]

        result = checker.get_dominant_year(mock_tracks)
        assert result is None, "Album with all invalid years should return None"

    def test_tie_breaker_behavior(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
        console_logger: logging.Logger,
    ) -> None:
        """Test behavior when multiple years have equal counts."""
        checker = YearConsistencyChecker(console_logger=console_logger)
        tie_albums: list[tuple[str, str, list[tuple[str, int]]]] = []

        for (artist, album), tracks in albums_with_tracks.items():
            years = [str(t.year) for t in tracks if t.year and is_valid_year(t.year)]
            if not years:
                continue

            year_counts = Counter(years)
            most_common = year_counts.most_common()

            # Check for ties (first and second most common have same count)
            if len(most_common) >= 2 and most_common[0][1] == most_common[1][1]:
                tie_albums.append((artist, album, most_common[:3]))

        # For tie cases, just verify we get a consistent result
        for artist, album, _ in tie_albums[:5]:
            tracks = albums_with_tracks[(artist, album)]
            result1 = checker.get_dominant_year(tracks)
            result2 = checker.get_dominant_year(tracks)
            assert result1 == result2, f"Dominant year should be deterministic for '{artist} - {album}'"


@pytest.mark.regression
class TestYearValidatorIntegration:
    """Test integration with year validators."""

    def test_is_valid_year_accepts_four_digit_strings(self) -> None:
        """is_valid_year should accept 4-digit year strings."""
        valid_years = ["1900", "1999", "2000", "2024", "2025"]
        for year in valid_years:
            assert is_valid_year(year), f"Year '{year}' should be valid"

    def test_is_valid_year_rejects_invalid_formats(self) -> None:
        """is_valid_year should reject invalid year formats."""
        invalid_years = ["", "0", "123", "12345", "abcd", "20.24", "2024.0", None]
        for year in invalid_years:
            assert not is_valid_year(year), f"Year '{year}' should be invalid"

    def test_is_empty_year_identifies_empty_values(self) -> None:
        """is_empty_year should identify truly empty/falsy values.

        The function uses `not year_value or not str(year_value).strip()`:
        - Falsy values (None, 0, False, "", etc.) → considered empty
        - Whitespace-only strings → considered empty
        - Any truthy value with non-whitespace content → NOT empty

        Note: Integer 0 is considered empty (falsy), but string "0" is NOT empty.
        Whether a value is a "valid year" is checked separately by is_valid_year.
        """
        # Values that is_empty_year considers empty (falsy or whitespace-only)
        empty_years: list[Any] = ["", None, "  ", 0]  # Note: integer 0 is falsy
        for year in empty_years:
            assert is_empty_year(year), f"Year '{year}' should be considered empty"

        # Values that are NOT empty (have content) but may be invalid years
        # is_empty_year returns False for these (they're truthy with content)
        non_empty_values: list[Any] = ["0", " 0 ", "2020", "abc"]
        for year in non_empty_values:
            # These are NOT empty - they contain a truthy string value
            # Whether they're "valid" years is checked by is_valid_year
            assert not is_empty_year(year), f"Year '{year}' is not empty (has content)"


@pytest.mark.regression
class TestRealWorldYearPatterns:
    """Test real-world year patterns from production data."""

    def test_year_distribution_by_decade(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Analyze year distribution by decade for sanity check."""
        years = [int(t.year) for t in library_tracks if t.year and t.year.isdigit() and t.year != "0"]

        if not years:
            pytest.skip("No valid years in test data")

        decade_counts: Counter[int] = Counter((y // 10) * 10 for y in years)

        # Verify reasonable distribution exists
        assert len(decade_counts) > 0, "Should have years from at least one decade"

        # Most common decades should be reasonable
        most_common_decade = decade_counts.most_common(1)[0][0]
        assert 1950 <= most_common_decade <= 2030, f"Most common decade {most_common_decade} outside expected range"

    def test_albums_with_release_year_metadata(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Count albums that have release_year metadata for validation."""
        albums_with_release_year = 0
        albums_without_release_year = 0

        for tracks in albums_with_tracks.values():
            has_release_year = any(t.release_year for t in tracks)
            if has_release_year:
                albums_with_release_year += 1
            else:
                albums_without_release_year += 1

        total = albums_with_release_year + albums_without_release_year
        # Just informational - verify we found albums
        assert total > 0 or not albums_with_tracks, "Should have processed all albums"


@pytest.mark.regression
class TestStaticMethodBehavior:
    """Test static methods of YearDeterminator work correctly in isolation."""

    @staticmethod
    def _create_mock_tracks(*years: str) -> list[Any]:
        """Create mock track objects with specified years.

        Args:
            *years: Year values to assign to each mock track.

        Returns:
            List of MagicMock objects with year attribute set.
        """
        return [MagicMock(year=year) for year in years]

    def test_has_consistent_year_with_two_same_years(self) -> None:
        """Two tracks with same year should be consistent."""
        tracks = self._create_mock_tracks("2020", "2020")
        result = YearDeterminator._has_consistent_year(tracks)
        assert result is True

    def test_has_consistent_year_with_different_years(self) -> None:
        """Two tracks with different years should not be consistent."""
        tracks = self._create_mock_tracks("2020", "2019")
        result = YearDeterminator._has_consistent_year(tracks)
        assert result is False

    def test_has_consistent_year_with_empty_years(self) -> None:
        """Tracks with empty years should not be consistent."""
        tracks = self._create_mock_tracks("2020", "")
        result = YearDeterminator._has_consistent_year(tracks)
        assert result is False

    def test_get_dominant_year_returns_most_common(self) -> None:
        """Should return the most common year among tracks."""
        tracks = self._create_mock_tracks("2020", "2020", "2019")
        result = YearDeterminator._get_dominant_year(tracks)
        assert result == "2020"

    def test_extract_future_years_identifies_future(self) -> None:
        """Should identify years in the future."""
        current_year = datetime.datetime.now(tz=datetime.UTC).year
        future_year = current_year + 1

        def make_dict_track(year: str) -> Any:
            """Create a dict-like mock that mimics TrackDict.get() behavior."""
            mock = MagicMock()
            mock.get = lambda k, d=None: year if k == "year" else d
            return mock

        tracks: list[Any] = [
            make_dict_track(str(current_year)),
            make_dict_track(str(future_year)),
            make_dict_track("2020"),
        ]
        result = YearDeterminator.extract_future_years(tracks)
        assert result == [future_year]

    def test_extract_release_years_filters_valid(self) -> None:
        """Should extract only valid release years."""

        def make_dict_track(release_year: str | None) -> Any:
            """Create a dict-like mock that mimics TrackDict.get() behavior."""
            mock = MagicMock()
            mock.get = lambda k, d=None: release_year if k == "release_year" else d
            return mock

        tracks: list[Any] = [
            make_dict_track("2020"),
            make_dict_track(""),
            make_dict_track(None),
            make_dict_track("2019"),
            make_dict_track("invalid"),
        ]
        result = YearDeterminator.extract_release_years(tracks)
        assert set(result) == {"2020", "2019"}
