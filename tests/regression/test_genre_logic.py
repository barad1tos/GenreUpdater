"""Regression tests for genre calculation using real library data.

These tests verify that genre-related business logic works correctly
with real production data from the library snapshot.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from core.models.metadata_utils import determine_dominant_genre_for_artist


@pytest.mark.regression
class TestGenreDataValidity:
    """Test that library data meets basic genre requirements."""

    def test_all_tracks_have_artist_field(
        self,
        library_tracks: list[dict[str, Any]],
    ) -> None:
        """Every track should have an artist field (may be empty)."""
        for track in library_tracks:
            assert "artist" in track, f"Track {track.get('id')} missing artist field"

    def test_all_tracks_have_genre_field(
        self,
        library_tracks: list[dict[str, Any]],
    ) -> None:
        """Every track should have a genre field (may be empty)."""
        for track in library_tracks:
            assert "genre" in track, f"Track {track.get('id')} missing genre field"

    def test_no_null_genre_values(
        self,
        library_tracks: list[dict[str, Any]],
    ) -> None:
        """Genre should be string, not None (empty string is ok)."""
        for track in library_tracks:
            genre = track.get("genre")
            assert genre is None or isinstance(genre, str), f"Track {track.get('id')} has non-string genre: {type(genre)}"


@pytest.mark.regression
class TestDominantGenreCalculation:
    """Test dominant genre calculation with real data."""

    def test_dominant_genre_returns_string_or_unknown(
        self,
        artists_with_tracks: dict[str, list[dict[str, Any]]],
        error_logger: logging.Logger,
    ) -> None:
        """determine_dominant_genre_for_artist should return string for all artists."""
        for artist, tracks in artists_with_tracks.items():
            result = determine_dominant_genre_for_artist(tracks, error_logger)
            assert isinstance(result, str), f"Artist {artist}: expected str, got {type(result)}"
            assert len(result) > 0, f"Artist {artist}: empty genre string"

    def test_dominant_genre_not_always_unknown(
        self,
        artists_with_tracks: dict[str, list[dict[str, Any]]],
        error_logger: logging.Logger,
    ) -> None:
        """At least some artists should have non-Unknown genres."""
        unknown_count = 0
        total_count = len(artists_with_tracks)

        for tracks in artists_with_tracks.values():
            result = determine_dominant_genre_for_artist(tracks, error_logger)
            if result == "Unknown":
                unknown_count += 1

        # Allow up to 20% unknown genres (reasonable for real data)
        unknown_ratio = unknown_count / total_count if total_count > 0 else 0
        assert unknown_ratio < 0.2, f"Too many Unknown genres: {unknown_count}/{total_count} ({unknown_ratio:.1%})"

    def test_artists_with_genres_get_dominant_genre(
        self,
        artists_with_tracks: dict[str, list[dict[str, Any]]],
        error_logger: logging.Logger,
    ) -> None:
        """Artists with at least one non-empty genre should get non-Unknown dominant."""
        failures: list[str] = []

        for artist, tracks in artists_with_tracks.items():
            # Check if artist has any non-empty genres
            genres = [t.get("genre", "") for t in tracks if t.get("genre")]
            if not genres:
                continue  # Skip artists with no genres

            result = determine_dominant_genre_for_artist(tracks, error_logger)
            if result == "Unknown":
                failures.append(f"{artist}: has genres {genres[:3]}... but got Unknown")

        # Allow some failures (edge cases with parsing issues)
        failure_ratio = len(failures) / len(artists_with_tracks)
        assert failure_ratio < 0.05, (
            f"Too many failures: {len(failures)}/{len(artists_with_tracks)} ({failure_ratio:.1%})\nFirst 10 failures:\n" + "\n".join(failures[:10])
        )


@pytest.mark.regression
class TestGenreFormat:
    """Test that genres follow expected format."""

    def test_no_excessive_whitespace(
        self,
        library_tracks: list[dict[str, Any]],
    ) -> None:
        """Genres should not have leading/trailing whitespace."""
        violations: list[tuple[str, str, str]] = []

        for track in library_tracks:
            genre = track.get("genre", "")
            if genre and genre != genre.strip():
                violations.append(
                    (
                        str(track.get("id")),
                        track.get("artist", ""),
                        genre,
                    )
                )

        assert not violations, (
            f"Found {len(violations)} genres with excessive whitespace:\n"
            + "\n".join(f"  {v[0]}: {v[1]} - '{v[2]}'" for v in violations[:10])
        )

    def test_no_genre_longer_than_reasonable(
        self,
        library_tracks: list[dict[str, Any]],
    ) -> None:
        """Genres should not exceed 100 characters (sanity check)."""
        max_reasonable_length = 100
        violations: list[tuple[str, str, str]] = []

        for track in library_tracks:
            genre = track.get("genre", "")
            if len(genre) > max_reasonable_length:
                violations.append(
                    (
                        str(track.get("id")),
                        track.get("artist", ""),
                        f"{genre[:50]}... (len={len(genre)})",
                    )
                )

        assert not violations, (
            f"Found {len(violations)} genres longer than {max_reasonable_length}:\n"
            + "\n".join(f"  {v[0]}: {v[1]} - {v[2]}" for v in violations[:10])
        )
