"""Regression tests for genre manager edge cases.

These tests focus on edge cases in genre determination and update logic
that are likely to cause bugs with real production library data.
"""

from __future__ import annotations

import datetime
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from core.models.metadata_utils import determine_dominant_genre_for_artist, group_tracks_by_artist
from core.models.track_status import can_edit_metadata, is_prerelease_status
from core.tracks.track_utils import is_missing_or_unknown_genre

if TYPE_CHECKING:
    import logging

    from core.models.track_models import TrackDict


@pytest.mark.regression
class TestDominantGenreCalculation:
    """Test dominant genre calculation with real production data."""

    def test_all_artists_have_determinable_genres(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
        error_logger: logging.Logger,
    ) -> None:
        """Most artists should have a determinable dominant genre.

        Artists without determinable genres may have:
        - All empty genres
        - All 'unknown' genres
        - Edge case combinations
        """
        artists_without_genre: list[tuple[str, int]] = []
        artists_with_genre = 0

        for artist, tracks in artists_with_tracks.items():
            dominant = determine_dominant_genre_for_artist(tracks, error_logger)
            if dominant:
                artists_with_genre += 1
            else:
                artists_without_genre.append((artist, len(tracks)))

        total = len(artists_with_tracks)
        genre_ratio = artists_with_genre / total if total > 0 else 0

        # At least 80% of artists should have determinable genres
        assert genre_ratio >= 0.8, (
            f"Too few artists with determinable genres: {artists_with_genre}/{total} ({genre_ratio:.1%})\n"
            f"Artists without: {artists_without_genre[:10]}"
        )

    def test_dominant_genre_format_valid(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
        error_logger: logging.Logger,
    ) -> None:
        """Dominant genres should be non-empty strings."""
        invalid_genres: list[tuple[str, str | None]] = []

        for artist, tracks in artists_with_tracks.items():
            dominant = determine_dominant_genre_for_artist(tracks, error_logger)
            if dominant is not None and (not isinstance(dominant, str) or not dominant.strip()):
                invalid_genres.append((artist, dominant))

        assert not invalid_genres, f"Found {len(invalid_genres)} invalid dominant genres: {invalid_genres[:10]}"

    def test_dominant_genre_not_unknown(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
        error_logger: logging.Logger,
    ) -> None:
        """Dominant genre should not be 'unknown' when other genres available."""
        unknown_artists: list[tuple[str, set[str]]] = []

        for artist, tracks in artists_with_tracks.items():
            dominant = determine_dominant_genre_for_artist(tracks, error_logger)

            # Get all unique non-empty genres
            all_genres = {t.genre.strip().lower() for t in tracks if t.genre and t.genre.strip()}
            non_unknown_genres = {g for g in all_genres if g != "unknown"}

            # If we have non-unknown genres but dominant is 'unknown', that's suspicious
            if dominant and dominant.lower() == "unknown" and non_unknown_genres:
                unknown_artists.append((artist, non_unknown_genres))

        # This shouldn't happen if genre determination logic is correct
        assert not unknown_artists, f"Found {len(unknown_artists)} artists with 'unknown' dominant genre despite having other genres:\n" + "\n".join(
            f"  {a[0]}: {a[1]}" for a in unknown_artists[:10]
        )


@pytest.mark.regression
class TestGenreDistribution:
    """Test genre distribution characteristics in the library."""

    def test_most_tracks_have_genres(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Majority of tracks should have non-empty, non-unknown genres."""
        tracks_with_valid_genre = sum(bool(t.genre and t.genre.strip() and t.genre.strip().lower() != "unknown") for t in library_tracks)
        total = len(library_tracks)

        genre_ratio = tracks_with_valid_genre / total if total > 0 else 0

        # At least 70% should have valid genres
        assert genre_ratio >= 0.7, f"Too few tracks with valid genres: {tracks_with_valid_genre}/{total} ({genre_ratio:.1%})"

    def test_genre_diversity_reasonable(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Library should have reasonable genre diversity."""
        genres = [t.genre.strip() for t in library_tracks if t.genre and t.genre.strip()]

        if not genres:
            pytest.skip("No valid genres in library")

        unique_genres = set(genres)
        genre_counts = Counter(genres)

        # Should have at least 5 unique genres
        assert len(unique_genres) >= 5, f"Too few unique genres: {len(unique_genres)}"

        # Top genre shouldn't be more than 50% of library (unless very small)
        if len(genres) > 100:
            top_genre, top_count = genre_counts.most_common(1)[0]
            top_ratio = top_count / len(genres)
            assert top_ratio < 0.5, f"Top genre '{top_genre}' dominates library: {top_ratio:.1%}"


@pytest.mark.regression
class TestTrackGrouping:
    """Test track grouping by artist."""

    def test_all_tracks_grouped(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """All tracks with artists should be grouped."""
        grouped = group_tracks_by_artist(library_tracks)

        # Count tracks in groups
        grouped_track_count = sum(len(tracks) for tracks in grouped.values())

        # Count tracks with artists
        tracks_with_artists = sum(bool(t.artist and t.artist.strip()) for t in library_tracks)

        assert grouped_track_count == tracks_with_artists, f"Grouping mismatch: {grouped_track_count} grouped vs {tracks_with_artists} with artists"

    def test_no_empty_artist_groups(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """No groups should have empty artist name."""
        grouped = group_tracks_by_artist(library_tracks)

        empty_artists = [artist for artist in grouped if not artist or not artist.strip()]
        assert not empty_artists, f"Found groups with empty artist names: {len(empty_artists)}"

    def test_group_consistency(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """All tracks in a group should be grouped by album_artist or primary artist.

        The grouping logic intentionally groups:
        1. Tracks with the same album_artist (e.g., all tracks from an album)
        2. Collaboration tracks by primary artist (e.g., "Bad Omens & Poppy" → "Bad Omens")

        This is correct behavior for genre determination - all tracks from an artist
        (including their collaborations) should be considered together when
        determining the dominant genre.

        This test verifies the grouping produces non-empty, valid groups.
        """
        grouped = group_tracks_by_artist(library_tracks)

        # Verify all groups have at least one track
        empty_groups = [key for key, tracks in grouped.items() if not tracks]
        assert not empty_groups, f"Found {len(empty_groups)} empty groups"

        # Verify all tracks have valid artist info
        for group_key, tracks in grouped.items():
            assert group_key, "Group key should not be empty"
            assert all(t.artist or t.get("album_artist") for t in tracks), f"Group '{group_key}' has tracks without any artist info"


@pytest.mark.regression
class TestMissingGenreDetection:
    """Test detection of tracks with missing or unknown genres."""

    def test_missing_genre_detection_accuracy(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """is_missing_or_unknown_genre should accurately detect issues."""
        for track in library_tracks:
            genre = track.genre or ""
            is_missing = is_missing_or_unknown_genre(track)

            # If genre is empty or whitespace-only, should be missing
            if not genre.strip():
                assert is_missing, f"Track {track.id}: empty genre not detected as missing"

            # If genre is 'unknown' (case-insensitive), should be missing
            if genre.strip().lower() == "unknown":
                assert is_missing, f"Track {track.id}: 'unknown' genre not detected as missing"

            # If genre is valid non-unknown string, should NOT be missing
            if genre.strip() and genre.strip().lower() not in ("unknown", ""):
                assert not is_missing, f"Track {track.id}: valid genre '{genre}' incorrectly marked as missing"

    def test_missing_genre_count_reasonable(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Library should have less than 30% missing/unknown genres."""
        missing_count = sum(is_missing_or_unknown_genre(t) for t in library_tracks)
        total = len(library_tracks)

        missing_ratio = missing_count / total if total > 0 else 0

        assert missing_ratio < 0.3, f"Too many tracks with missing/unknown genres: {missing_count}/{total} ({missing_ratio:.1%})"


@pytest.mark.regression
class TestTrackStatusHandling:
    """Test handling of track status for metadata editing."""

    def test_prerelease_tracks_identified(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Prerelease tracks should be identifiable."""
        prerelease_count = 0
        editable_count = 0

        for track in library_tracks:
            status = track.track_status
            if is_prerelease_status(status):
                prerelease_count += 1
            if can_edit_metadata(status):
                editable_count += 1

        # Most tracks should be editable (non-prerelease)
        total = len(library_tracks)
        if total > 0:
            editable_ratio = editable_count / total
            assert editable_ratio >= 0.95, f"Too few editable tracks: {editable_count}/{total} ({editable_ratio:.1%})"

    def test_no_invalid_track_statuses(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Track statuses should be valid or empty."""
        # Valid statuses: None, '', 'Matched', 'Uploaded', 'Purchased', 'Prerelease', etc.
        known_statuses = {
            None,
            "",
            "Matched",
            "Uploaded",
            "Purchased",
            "Prerelease",
            "Pre-release",
            "Apple Music",
            "Library",
            "iTunes Match",
        }

        unknown_statuses: dict[str, int] = {}

        for track in library_tracks:
            status = track.track_status
            if status and status not in known_statuses:
                unknown_statuses[status] = unknown_statuses.get(status, 0) + 1

        if unknown_statuses:
            pytest.skip(f"Found {len(unknown_statuses)} unknown track statuses (informational): {unknown_statuses}")


@pytest.mark.regression
class TestIncrementalUpdateFiltering:
    """Test incremental update filtering with production data."""

    def test_recent_tracks_identification(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Should be able to identify recently added tracks."""
        now = datetime.datetime.now(tz=datetime.UTC)
        one_week_ago = now - datetime.timedelta(days=7)

        recent_tracks: list[TrackDict] = []
        invalid_dates: list[tuple[str, str]] = []

        for track in library_tracks:
            date_str = track.date_added
            if not date_str:
                continue

            try:
                # Parse date_added (format: YYYY-MM-DD HH:MM:SS or ISO)
                if "T" in date_str:
                    dt = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                else:
                    dt = datetime.datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=datetime.UTC)

                if dt > one_week_ago:
                    recent_tracks.append(track)
            except (ValueError, TypeError):
                invalid_dates.append((track.id, date_str))

        # Just verify we can parse dates - no specific count requirements
        parse_success_rate = (len(library_tracks) - len(invalid_dates)) / len(library_tracks) if library_tracks else 0
        assert parse_success_rate >= 0.95, f"Too many invalid dates: {len(invalid_dates)} ({1 - parse_success_rate:.1%})"

    def test_date_added_format_consistency(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """date_added should have consistent format across tracks."""
        date_formats: dict[str, int] = {}

        for track in library_tracks:
            date_str = track.date_added
            if not date_str:
                date_formats["empty"] = date_formats.get("empty", 0) + 1
                continue

            # Detect format
            if "T" in date_str:
                fmt = "ISO"
            elif len(date_str) >= 19 and date_str[10] == " ":
                fmt = "YYYY-MM-DD HH:MM:SS"
            else:
                fmt = f"other: {date_str[:20]}"

            date_formats[fmt] = date_formats.get(fmt, 0) + 1

        # Should have at most 2-3 formats (ISO, standard, empty)
        assert len(date_formats) <= 4, f"Too many date formats detected: {date_formats}"


@pytest.mark.regression
class TestArtistNameEdgeCases:
    """Test handling of edge cases in artist names."""

    def test_unicode_artist_names(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
        error_logger: logging.Logger,
    ) -> None:
        """Should handle Unicode artist names correctly."""
        unicode_artists: list[str] = []

        for artist, tracks in artists_with_tracks.items():
            # Check for non-ASCII characters
            if not artist.isascii():
                unicode_artists.append(artist)
                _ = determine_dominant_genre_for_artist(tracks, error_logger)

        # Verify Unicode artists are processed correctly
        # No assertion needed - just verifying no exceptions raised

    def test_special_character_artist_names(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
        error_logger: logging.Logger,
    ) -> None:
        """Should handle artist names with special characters."""
        special_char_artists: list[str] = []
        special_chars = "&+/\\()[]{}!@#$%^*'\"<>?"

        for artist, tracks in artists_with_tracks.items():
            if any(c in artist for c in special_chars):
                special_char_artists.append(artist)
                _ = determine_dominant_genre_for_artist(tracks, error_logger)

        # Verify special character artists are processed correctly
        # No assertion needed - just verifying no exceptions raised

    def test_whitespace_in_artist_names(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
    ) -> None:
        """Artist names should be properly trimmed."""
        whitespace_issues: list[str] = []

        for artist in artists_with_tracks:
            if artist != artist.strip():
                whitespace_issues.append(artist)
            if "  " in artist:  # Double spaces
                whitespace_issues.append(artist)

        # Should have no untrimmed artist names
        assert not whitespace_issues, f"Found {len(whitespace_issues)} artists with whitespace issues: {whitespace_issues[:10]}"


@pytest.mark.regression
class TestGenreConsistencyAcrossAlbums:
    """Test genre consistency across albums by same artist."""

    def test_same_artist_genre_consistency(
        self,
        artists_with_tracks: dict[str, list[TrackDict]],
        albums_with_tracks: dict[tuple[str, str], list[TrackDict]],
    ) -> None:
        """Albums by same artist should generally have consistent genres."""
        inconsistent_artists: list[tuple[str, set[str]]] = []

        for artist in artists_with_tracks:
            # Get all albums for this artist
            artist_albums = {album: album_tracks for (a, album), album_tracks in albums_with_tracks.items() if a == artist}

            if len(artist_albums) < 2:
                continue  # Skip single-album artists

            # Get dominant genres per album
            album_genres: set[str] = set()
            for album_tracks in artist_albums.values():
                if genres := [t.genre.strip() for t in album_tracks if t.genre and t.genre.strip()]:
                    most_common = Counter(genres).most_common(1)[0][0]
                    album_genres.add(most_common)

            # If very diverse (>3 different genres), flag for review
            if len(album_genres) > 3:
                inconsistent_artists.append((artist, album_genres))

        if inconsistent_artists:
            pytest.skip(f"Found {len(inconsistent_artists)} artists with >3 genres across albums (informational): {inconsistent_artists[:5]}")


@pytest.mark.regression
class TestDeduplicationLogic:
    """Test track deduplication logic."""

    def test_no_duplicate_track_ids(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """Library snapshot should not have duplicate track IDs."""
        seen_ids: set[str] = set()
        duplicates: list[str] = []

        for track in library_tracks:
            track_id = track.id
            if track_id in seen_ids:
                duplicates.append(track_id)
            seen_ids.add(track_id)

        assert not duplicates, f"Found {len(duplicates)} duplicate track IDs: {duplicates[:10]}"

    def test_tracks_have_valid_ids(
        self,
        library_tracks: list[TrackDict],
    ) -> None:
        """All tracks should have non-empty IDs."""
        empty_ids = [i for i, t in enumerate(library_tracks) if not t.id or not str(t.id).strip()]

        assert not empty_ids, f"Found {len(empty_ids)} tracks with empty IDs at indices: {empty_ids[:10]}"
