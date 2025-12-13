"""Regression tests for year determination using real library data.

These tests verify that year-related business logic works correctly
with real production data from the library snapshot.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from core.tracks.year_consistency import YearConsistencyChecker

if TYPE_CHECKING:
    from core.models.track import TrackDict


@pytest.mark.regression
class TestYearDataValidity:
    """Test that library data meets basic year requirements."""

    def test_all_tracks_have_year_field(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Every track should have a year field (may be empty)."""
        for track in library_tracks:
            assert "year" in track, f"Track {track.get('id')} missing year field"

    def test_year_format_valid(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """All non-empty years should be 4-digit strings."""
        invalid_years: list[tuple[str, str, str, str]] = []

        for track in library_tracks:
            year = track.get("year", "")
            # Allow empty, "0", or 4-digit year
            if year and year != "0":
                if not (year.isdigit() and len(year) == 4):
                    invalid_years.append(
                        (
                            str(track.get("id")),
                            track.get("artist", ""),
                            track.get("album", ""),
                            year,
                        )
                    )

        assert len(invalid_years) == 0, (
            f"Found {len(invalid_years)} invalid year formats:\n"
            + "\n".join(f"  {v[0]}: {v[1]} - {v[2]} = '{v[3]}'" for v in invalid_years[:10])
        )

    def test_years_in_reasonable_range(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Years should be between 1900 and current year + 1."""
        import datetime

        current_year = datetime.datetime.now(tz=datetime.UTC).year
        min_year = 1900
        max_year = current_year + 1  # Allow releases announced for next year

        out_of_range: list[tuple[str, str, str, str]] = []

        for track in library_tracks:
            year = track.get("year", "")
            if year and year != "0" and year.isdigit():
                year_int = int(year)
                if not (min_year <= year_int <= max_year):
                    out_of_range.append(
                        (
                            str(track.get("id")),
                            track.get("artist", ""),
                            track.get("album", ""),
                            year,
                        )
                    )

        assert len(out_of_range) == 0, (
            f"Found {len(out_of_range)} years outside {min_year}-{max_year}:\n"
            + "\n".join(f"  {v[0]}: {v[1]} - {v[2]} = {v[3]}" for v in out_of_range[:10])
        )


@pytest.mark.regression
class TestDominantYearCalculation:
    """Test dominant year calculation with real data."""

    def test_dominant_year_returns_string_or_none(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
        console_logger: logging.Logger,
    ) -> None:
        """get_dominant_year should return string or None for all albums."""
        checker = YearConsistencyChecker(console_logger=console_logger)

        for (artist, album), tracks in albums_with_tracks.items():
            result = checker.get_dominant_year(tracks)
            assert result is None or isinstance(
                result, str
            ), f"Album {artist} - {album}: expected str|None, got {type(result)}"

    def test_dominant_year_format_valid(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
        console_logger: logging.Logger,
    ) -> None:
        """Dominant years should be valid 4-digit strings."""
        checker = YearConsistencyChecker(console_logger=console_logger)
        invalid: list[tuple[str, str, str]] = []

        for (artist, album), tracks in albums_with_tracks.items():
            result = checker.get_dominant_year(tracks)
            if result is not None:
                if not (result.isdigit() and len(result) == 4):
                    invalid.append((artist, album, result))

        assert len(invalid) == 0, (
            f"Found {len(invalid)} invalid dominant years:\n"
            + "\n".join(f"  {v[0]} - {v[1]} = '{v[2]}'" for v in invalid[:10])
        )

    def test_albums_with_consistent_years_get_dominant(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
        console_logger: logging.Logger,
    ) -> None:
        """Albums where all tracks have same year should return that year.

        Note: Albums with "suspiciously old" years (release year much older than
        dateAdded) are expected to return None to trigger API verification.
        This test only checks albums where the year gap is reasonable.
        """
        checker = YearConsistencyChecker(console_logger=console_logger)
        failures: list[tuple[str, str, str, str | None]] = []

        suspicion_threshold = 10  # Same as DEFAULT_SUSPICION_THRESHOLD_YEARS

        for (artist, album), tracks in albums_with_tracks.items():
            years = {t.get("year", "") for t in tracks}
            years.discard("")  # Remove empty
            years.discard("0")  # Remove placeholder

            # Only test albums with single consistent non-empty year
            if len(years) == 1:
                expected = years.pop()

                # Skip albums that would be flagged as "suspicious"
                # (where release year is much older than dateAdded)
                try:
                    expected_int = int(expected)
                    earliest_added_year = min(
                        int(str(t.get("date_added", ""))[:4])
                        for t in tracks
                        if t.get("date_added")
                    )
                    if earliest_added_year - expected_int > suspicion_threshold:
                        continue  # Skip - expected to return None for API verification
                except (ValueError, TypeError):
                    pass

                result = checker.get_dominant_year(tracks)
                if result != expected:
                    failures.append((artist, album, expected, result))

        # Count only non-suspicious albums
        total_testable = 0
        for (_, _), tracks in albums_with_tracks.items():
            years = {t.get("year", "") for t in tracks} - {"", "0"}
            if len(years) == 1:
                expected_year = years.pop()
                try:
                    expected_int = int(expected_year)
                    earliest_added = min(
                        int(str(t.get("date_added", ""))[:4])
                        for t in tracks
                        if t.get("date_added")
                    )
                    if earliest_added - expected_int <= suspicion_threshold:
                        total_testable += 1
                except (ValueError, TypeError):
                    total_testable += 1

        failure_ratio = len(failures) / total_testable if total_testable > 0 else 0

        assert failure_ratio < 0.05, (
            f"Too many failures for consistent albums: "
            f"{len(failures)}/{total_testable} ({failure_ratio:.1%})\n"
            f"First 10:\n"
            + "\n".join(f"  {f[0]} - {f[1]}: expected {f[2]}, got {f[3]}" for f in failures[:10])
        )


@pytest.mark.regression
class TestYearDistribution:
    """Test year distribution characteristics."""

    def test_most_tracks_have_years(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Majority of tracks should have non-empty years."""
        tracks_with_year = sum(
            1 for t in library_tracks if t.get("year") and t.get("year") != "0"
        )
        total = len(library_tracks)

        year_ratio = tracks_with_year / total if total > 0 else 0

        # At least 80% of tracks should have years
        assert year_ratio >= 0.8, (
            f"Too few tracks with years: {tracks_with_year}/{total} ({year_ratio:.1%})"
        )

    def test_year_distribution_reasonable(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Years should cluster around recent decades (basic sanity check)."""
        from collections import Counter

        years = [
            int(t.get("year", "0"))
            for t in library_tracks
            if t.get("year") and t.get("year", "").isdigit() and t.get("year") != "0"
        ]

        if not years:
            pytest.skip("No valid years in library")

        decade_counts: Counter[int] = Counter(y // 10 * 10 for y in years)

        # At least some music from 2000s onwards
        recent_decades = sum(decade_counts.get(d, 0) for d in [2000, 2010, 2020])
        total = len(years)

        recent_ratio = recent_decades / total if total > 0 else 0

        # At least 50% should be from 2000+
        assert recent_ratio >= 0.5, (
            f"Unexpectedly few recent tracks: {recent_decades}/{total} ({recent_ratio:.1%})\n"
            f"Decade distribution: {dict(decade_counts.most_common(10))}"
        )
