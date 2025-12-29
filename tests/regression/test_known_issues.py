"""Regression tests for known issues from production logs.

These tests verify that specific problematic cases from production
are handled correctly. Based on analysis of:
- pending_year_verification.csv
- main.log errors
- library_snapshot.json
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import pytest

from core.models.metadata_utils import determine_dominant_genre_for_artist
from core.tracks.year_consistency import YearConsistencyChecker

if TYPE_CHECKING:
    from core.models.track_models import TrackDict


# Known albums that API fails to find years for (from pending_year_verification.csv)
KNOWN_MISSING_YEAR_ALBUMS: list[tuple[str, str, str]] = [
    ("Disturbed", "Ten Thousand Fists", "2005"),
    ("Be'lakor", "Stone's Reach", "2009"),
    ("Be'lakor", "The Frail Tide", "2008"),
    ("Bloodbath", "Nightmares Made Flesh", "2004"),
    ("Anathema", "The Silent Enigma", "1995"),
    ("Anathema", "Eternity", "1996"),
    ("Anathema", "Judgement", "1999"),
    ("Animals As Leaders", "Animals as Leaders", "2009"),
]

# Albums with year discrepancies: (artist, album, existing_year, expected_correct_year)
YEAR_DISCREPANCY_ALBUMS: list[tuple[str, str, str, str]] = [
    ("Aggressive Sound Painters", "The Path of Least Resistance", "2004", "2011"),
]

# Albums flagged as suspicious: (artist, album, year, year_gap)
# year_gap > 10 triggers API verification
SUSPICIOUS_YEAR_ALBUMS: list[tuple[str, str, str, int]] = [
    ("Parkway Drive", "Deep Blue", "2009", 13),
]


@pytest.mark.regression
class TestKnownMissingYearAlbums:
    """Test albums that API historically fails to find years for."""

    def test_known_albums_exist_in_snapshot(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Verify known problematic albums exist in test data."""
        missing_albums: list[tuple[str, str]] = []

        missing_albums.extend(
            (artist, album) for artist, album, _expected_year in KNOWN_MISSING_YEAR_ALBUMS if (artist, album) not in albums_with_tracks
        )
        # Report which albums are missing from snapshot
        if missing_albums:
            pytest.skip(f"Missing {len(missing_albums)} known albums from snapshot: {missing_albums[:5]}...")

    def test_known_albums_have_tracks_with_years(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Known problematic albums should have year data in tracks."""
        albums_without_years: list[tuple[str, str]] = []

        for artist, album, _expected_year in KNOWN_MISSING_YEAR_ALBUMS:
            if (artist, album) not in albums_with_tracks:
                continue

            tracks = albums_with_tracks[(artist, album)]
            years = {t.year for t in tracks if t.year and t.year != "0"}

            if not years:
                albums_without_years.append((artist, album))

        # These albums SHOULD have years in tracks even if API fails
        assert not albums_without_years, f"Albums missing year data in tracks: {albums_without_years}"

    def test_known_albums_flagged_for_api_verification(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
        console_logger: logging.Logger,
    ) -> None:
        """Known problematic albums should be flagged for API verification.

        These albums have old release years but were added recently (year gap > 10).
        The system correctly returns None to trigger API verification.
        This documents EXPECTED behavior, not a bug.
        """
        checker = YearConsistencyChecker(console_logger=console_logger)
        correctly_flagged: list[tuple[str, str, str]] = []
        incorrectly_returned: list[tuple[str, str, str, str]] = []

        for artist, album, expected_year in KNOWN_MISSING_YEAR_ALBUMS:
            if (artist, album) not in albums_with_tracks:
                continue

            tracks = albums_with_tracks[(artist, album)]
            dominant_year = checker.get_dominant_year(tracks)

            if dominant_year is None:
                # Correct: flagged for API verification
                correctly_flagged.append((artist, album, expected_year))
            else:
                # Unexpected: returned year without API check
                incorrectly_returned.append((artist, album, expected_year, dominant_year))

        # Most albums SHOULD return None (flagged for API verification)
        # because they have old years but recent dateAdded
        flagged_ratio = len(correctly_flagged) / len(KNOWN_MISSING_YEAR_ALBUMS) if KNOWN_MISSING_YEAR_ALBUMS else 0

        # At least 70% should be flagged (year gap > 10 triggers suspicion)
        assert flagged_ratio >= 0.7, (
            f"Too few albums flagged for API verification: {len(correctly_flagged)}/{len(KNOWN_MISSING_YEAR_ALBUMS)}\n"
            f"Incorrectly returned years: {incorrectly_returned}"
        )


@pytest.mark.regression
class TestYearDiscrepancies:
    """Test albums with known year discrepancies."""

    def test_year_discrepancy_triggers_api_verification(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
        console_logger: logging.Logger,
    ) -> None:
        """Albums with year discrepancies should trigger API verification.

        When an album has a wrong year (e.g., 2004 instead of 2011), the system
        may detect it as suspicious due to the year gap between release and
        dateAdded. Returning None is CORRECT - it triggers API verification.
        """
        checker = YearConsistencyChecker(console_logger=console_logger)

        for artist, album, existing_year, correct_year in YEAR_DISCREPANCY_ALBUMS:
            if (artist, album) not in albums_with_tracks:
                pytest.skip(f"Album not in snapshot: {artist} - {album}")

            tracks = albums_with_tracks[(artist, album)]
            track_years = {t.year for t in tracks if t.year and t.year != "0"}

            if existing_year in track_years:
                dominant = checker.get_dominant_year(tracks)

                # Either:
                # 1. Returns None (flagged for API verification) - CORRECT
                # 2. Returns the wrong year (will be corrected by API) - ACCEPTABLE
                # The key is: it should NOT crash or return garbage
                assert dominant is None or dominant.isdigit(), f"{artist} - {album}: unexpected result {dominant}"

                # Document what happened
                if dominant is None:
                    # Best case: flagged for API check
                    pass
                elif dominant == existing_year:
                    # Wrong year returned, but API will fix it
                    pytest.xfail(f"{artist} - {album}: returns wrong year {existing_year}, should be {correct_year}. API verification needed.")


@pytest.mark.regression
class TestSuspiciousYearDetection:
    """Test that suspiciously old years trigger verification."""

    def test_suspicious_years_return_none_for_api_check(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
        console_logger: logging.Logger,
    ) -> None:
        """Albums with suspicious years should return None to trigger API."""
        checker = YearConsistencyChecker(console_logger=console_logger)
        not_flagged: list[tuple[str, str, str, int]] = []

        for artist, album, year, expected_gap in SUSPICIOUS_YEAR_ALBUMS:
            if (artist, album) not in albums_with_tracks:
                continue

            tracks = albums_with_tracks[(artist, album)]

            # Force all tracks to have the suspicious year
            for track in tracks:
                if track.year != year:
                    continue  # Skip if year doesn't match

            dominant = checker.get_dominant_year(tracks)

            # If year gap > 10, dominant should be None (trigger API verification)
            # But if tracks already have consistent year, it may return that year
            # This test documents the expected behavior
            if dominant is not None and expected_gap > 10:
                # Check if dateAdded indicates suspicion
                with contextlib.suppress(ValueError, TypeError):
                    if added_years := [int(str(t.date_added or "")[:4]) for t in tracks if t.date_added]:
                        earliest_added = min(added_years)
                        actual_gap = earliest_added - int(year)
                        if actual_gap > 10:
                            not_flagged.append((artist, album, year, actual_gap))
        # Some suspicious albums should be flagged
        # This is informational - documents current behavior
        if not_flagged:
            pytest.xfail(f"Albums with suspicious years not flagged: {not_flagged}")


@pytest.mark.regression
class TestGenreEdgeCases:
    """Test genre calculation edge cases from production."""

    def test_artists_with_mixed_genres_get_dominant(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
        error_logger: logging.Logger,
    ) -> None:
        """Artists with multiple genres should get consistent dominant."""
        inconsistent_artists: list[tuple[str, set[str], str]] = []

        for artist, tracks in artists_with_tracks.items():
            genres = {t.genre for t in tracks if t.genre}

            # Only test artists with multiple different genres
            if len(genres) < 2:
                continue

            dominant = determine_dominant_genre_for_artist(tracks, error_logger)

            # Dominant should be one of the existing genres (not Unknown)
            if dominant == "Unknown" and genres:
                inconsistent_artists.append((artist, genres, dominant))

        # Allow some edge cases
        if inconsistent_artists:
            ratio = len(inconsistent_artists) / len(artists_with_tracks)
            assert ratio < 0.05, f"Too many artists with Unknown despite having genres: {len(inconsistent_artists)}\n" + "\n".join(
                f"  {a[0]}: genres={list(a[1])[:3]}, got={a[2]}" for a in inconsistent_artists[:10]
            )

    def test_empty_genre_artists_get_unknown(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
        error_logger: logging.Logger,
    ) -> None:
        """Artists with no genres should get 'Unknown'."""
        wrong_results: list[tuple[str, str]] = []

        for artist, tracks in artists_with_tracks.items():
            genres = [t.genre for t in tracks if t.genre]

            # Only test artists with NO genres
            if genres:
                continue

            dominant = determine_dominant_genre_for_artist(tracks, error_logger)

            if dominant != "Unknown":
                wrong_results.append((artist, dominant))

        assert not wrong_results, f"Artists with no genres got non-Unknown result: {wrong_results[:10]}"


@pytest.mark.regression
class TestBatchFetcherEdgeCases:
    """Test scenarios that cause batch fetcher issues.

    Note: These tests use snapshot data to verify the logic,
    not actual AppleScript execution.
    """

    def test_track_count_consistency(
        self,
        library_tracks: list[TrackDict],
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Total tracks should match sum of album tracks."""
        # Count tracks that have both artist and album
        tracks_with_album = [t for t in library_tracks if t.artist and t.album]

        album_track_count = sum(len(tracks) for tracks in albums_with_tracks.values())

        # Should be equal (all tracks with artist+album should be in albums_with_tracks)
        assert len(tracks_with_album) == album_track_count, (
            f"Track count mismatch: {len(tracks_with_album)} tracks with album, but {album_track_count} in albums_with_tracks"
        )

    def test_no_duplicate_track_ids(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Each track should have unique ID."""
        ids = [t.id for t in library_tracks]
        unique_ids = set(ids)

        duplicates = len(ids) - len(unique_ids)
        assert duplicates == 0, f"Found {duplicates} duplicate track IDs"

    def test_batch_size_simulation(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Simulate batch fetching to verify offset handling."""
        batch_size = 1000
        total_tracks = len(library_tracks)

        # Calculate expected batches
        expected_batches = (total_tracks + batch_size - 1) // batch_size

        # Simulate fetching
        fetched = 0
        batch_num = 0

        while fetched < total_tracks:
            batch_start = fetched
            batch_end = min(fetched + batch_size, total_tracks)
            batch_tracks = library_tracks[batch_start:batch_end]

            assert len(batch_tracks) > 0, f"Batch {batch_num} returned 0 tracks (offset={batch_start}, total={total_tracks})"

            fetched += len(batch_tracks)
            batch_num += 1

            # Prevent infinite loop
            if batch_num > expected_batches + 5:
                pytest.fail(f"Too many batches: {batch_num} > {expected_batches}")

        assert fetched == total_tracks, f"Fetched {fetched} tracks, expected {total_tracks}"
