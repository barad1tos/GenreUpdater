"""Regression tests for known issues from production logs.

These tests verify that specific problematic cases from production
are handled correctly. Based on analysis of:
- pending_year_verification.csv
- main.log errors
- library_snapshot.json
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from core.models.metadata_utils import determine_dominant_genre_for_artist

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
