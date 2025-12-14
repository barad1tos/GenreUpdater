"""Data quality baseline tests.

These tests fail if data quality degrades below established baselines.
Baselines are derived from production snapshot as of 2025-12-13.

Update baselines only when intentional changes improve data quality.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from core.models.metadata_utils import determine_dominant_genre_for_artist

if TYPE_CHECKING:
    from core.models.track_models import TrackDict


# =============================================================================
# BASELINES - Update these when data quality IMPROVES
# =============================================================================


class Baseline:
    """Data quality baselines from 2025-12-14 snapshot."""

    # Snapshot stats
    TOTAL_TRACKS: int = 36940
    TOTAL_ALBUMS: int = 1416  # TODO: verify and update
    TOTAL_ARTISTS: int = 358  # TODO: verify and update

    # Quality thresholds (as percentages)
    MAX_TRACKS_WITHOUT_YEAR_PCT: float = 0.1  # <0.1% tracks without year
    MAX_TRACKS_WITHOUT_GENRE_PCT: float = 0.1  # <0.1% tracks without genre
    MAX_ALBUMS_WITHOUT_YEAR_PCT: float = 1.0  # <1% albums without year
    MAX_ARTISTS_WITH_UNKNOWN_GENRE_PCT: float = 1.0  # <1% artists with Unknown

    # Absolute thresholds
    MAX_PENDING_VERIFICATION: int = 120  # Allow 10% growth from 105
    MAX_UNKNOWN_GENRE_TRACKS: int = 10  # Currently 0

    # Suspicious year is EXPECTED for old music collections
    # 50% is normal - don't alert on this
    SUSPICIOUS_YEAR_THRESHOLD: int = 10  # years gap


# =============================================================================
# FIXTURES
# =============================================================================

PENDING_VERIFICATION_PATH = Path("/Users/cloud/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs/csv/pending_year_verification.csv")


@pytest.fixture
def pending_verification_albums() -> list[dict[str, str]]:
    """Load albums pending API verification."""
    if not PENDING_VERIFICATION_PATH.exists():
        pytest.skip("pending_year_verification.csv not found")

    with PENDING_VERIFICATION_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


# =============================================================================
# DATA QUALITY TESTS
# =============================================================================


@pytest.mark.regression
class TestTrackDataQuality:
    """Test track-level data quality."""

    def test_tracks_with_year(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Most tracks should have year data."""
        without_year = sum(1 for t in library_tracks if not t.year or t.year == "0")
        total = len(library_tracks)
        pct = (without_year / total) * 100 if total > 0 else 0

        assert pct <= Baseline.MAX_TRACKS_WITHOUT_YEAR_PCT, (
            f"Too many tracks without year: {without_year}/{total} ({pct:.2f}%)\nBaseline: <{Baseline.MAX_TRACKS_WITHOUT_YEAR_PCT}%"
        )

    def test_tracks_with_genre(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Most tracks should have genre data."""
        without_genre = sum(1 for t in library_tracks if not t.genre)
        total = len(library_tracks)
        pct = (without_genre / total) * 100 if total > 0 else 0

        assert pct <= Baseline.MAX_TRACKS_WITHOUT_GENRE_PCT, (
            f"Too many tracks without genre: {without_genre}/{total} ({pct:.2f}%)\nBaseline: <{Baseline.MAX_TRACKS_WITHOUT_GENRE_PCT}%"
        )

    def test_no_unknown_genre_explosion(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """'Unknown' genre should not appear in tracks."""
        unknown_count = sum(1 for t in library_tracks if t.genre == "Unknown")

        assert unknown_count <= Baseline.MAX_UNKNOWN_GENRE_TRACKS, (
            f"Too many tracks with 'Unknown' genre: {unknown_count}\nBaseline: <={Baseline.MAX_UNKNOWN_GENRE_TRACKS}"
        )


@pytest.mark.regression
class TestAlbumDataQuality:
    """Test album-level data quality."""

    def test_albums_with_year(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Most albums should have year in at least one track."""
        without_year = 0

        for tracks in albums_with_tracks.values():
            years = {t.year for t in tracks if t.year and t.year != "0"}
            if not years:
                without_year += 1

        total = len(albums_with_tracks)
        pct = (without_year / total) * 100 if total > 0 else 0

        assert pct <= Baseline.MAX_ALBUMS_WITHOUT_YEAR_PCT, (
            f"Too many albums without year: {without_year}/{total} ({pct:.2f}%)\nBaseline: <{Baseline.MAX_ALBUMS_WITHOUT_YEAR_PCT}%"
        )

    def test_pending_verification_not_growing(
        self,
        pending_verification_albums: list[dict[str, str]],
    ) -> None:
        """Pending verification count should not grow significantly.

        If this fails, it means API is failing to find years for more albums.
        Investigate: API rate limits? New albums with obscure metadata?
        """
        unique_albums = {(r["artist"], r["album"]) for r in pending_verification_albums}
        count = len(unique_albums)

        assert count <= Baseline.MAX_PENDING_VERIFICATION, (
            f"Pending verification growing: {count} albums\n"
            f"Baseline: <={Baseline.MAX_PENDING_VERIFICATION}\n"
            f"Investigate API failures or add manual year overrides."
        )


@pytest.mark.regression
class TestArtistDataQuality:
    """Test artist-level data quality."""

    def test_artists_have_dominant_genre(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
        error_logger: logging.Logger,
    ) -> None:
        """Most artists should resolve to non-Unknown genre."""
        unknown_count = 0

        for tracks in artists_with_tracks.values():
            dominant = determine_dominant_genre_for_artist(tracks, error_logger)
            if dominant == "Unknown":
                unknown_count += 1

        total = len(artists_with_tracks)
        pct = (unknown_count / total) * 100 if total > 0 else 0

        assert pct <= Baseline.MAX_ARTISTS_WITH_UNKNOWN_GENRE_PCT, (
            f"Too many artists with Unknown genre: {unknown_count}/{total} ({pct:.2f}%)\nBaseline: <{Baseline.MAX_ARTISTS_WITH_UNKNOWN_GENRE_PCT}%"
        )


@pytest.mark.regression
class TestDataIntegrity:
    """Test data integrity constraints."""

    def test_snapshot_size_reasonable(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Snapshot should have reasonable track count.

        Fails if snapshot is suspiciously small (possible corruption)
        or suspiciously large (possible duplication).
        """
        count = len(library_tracks)
        min_expected = int(Baseline.TOTAL_TRACKS * 0.8)  # 80% of baseline
        max_expected = int(Baseline.TOTAL_TRACKS * 1.5)  # 150% of baseline

        assert min_expected <= count <= max_expected, (
            f"Unexpected track count: {count}\nExpected: {min_expected}-{max_expected} (based on baseline {Baseline.TOTAL_TRACKS})"
        )

    def test_artist_count_reasonable(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
    ) -> None:
        """Artist count should be reasonable."""
        count = len(artists_with_tracks)
        min_expected = int(Baseline.TOTAL_ARTISTS * 0.8)
        max_expected = int(Baseline.TOTAL_ARTISTS * 1.5)

        assert min_expected <= count <= max_expected, (
            f"Unexpected artist count: {count}\nExpected: {min_expected}-{max_expected} (based on baseline {Baseline.TOTAL_ARTISTS})"
        )

    def test_album_count_reasonable(
        self,
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Album count should be reasonable."""
        count = len(albums_with_tracks)
        min_expected = int(Baseline.TOTAL_ALBUMS * 0.8)
        max_expected = int(Baseline.TOTAL_ALBUMS * 1.5)

        assert min_expected <= count <= max_expected, (
            f"Unexpected album count: {count}\nExpected: {min_expected}-{max_expected} (based on baseline {Baseline.TOTAL_ALBUMS})"
        )

    def test_all_tracks_have_id(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Every track must have an ID."""
        without_id = [t for t in library_tracks if not t.id]

        assert not without_id, f"Found {len(without_id)} tracks without ID - data corruption!"

    def test_all_tracks_have_artist(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Every track should have an artist (allow some exceptions)."""
        without_artist = sum(1 for t in library_tracks if not t.artist)
        total = len(library_tracks)
        pct = (without_artist / total) * 100 if total > 0 else 0

        # Allow up to 1% without artist (compilations, etc.)
        assert pct <= 1.0, f"Too many tracks without artist: {without_artist}/{total} ({pct:.2f}%)"
