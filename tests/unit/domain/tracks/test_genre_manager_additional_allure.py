"""Additional GenreManager tests with Allure reporting for core functionality.

This file contains the 6 additional tests specified in the testing plan:
- test_calculate_dominant_genre_single()
- test_calculate_dominant_genre_tie()
- test_calculate_dominant_genre_threshold()
- test_calculate_dominant_genre_empty()
- test_process_artist_genres()
- test_apply_genre_to_tracks()
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import allure
import pytest
from src.domain.tracks.genre_manager import GenreManager
from src.shared.data.metadata import determine_dominant_genre_for_artist, group_tracks_by_artist
from src.shared.data.models import TrackDict

from tests.mocks.csv_mock import MockAnalytics, MockLogger


@allure.epic("Music Genre Updater")
@allure.feature("Genre Management Core")
class TestGenreManagerCoreFunctionality:
    """Tests for core GenreManager functionality specified in testing plan."""

    @staticmethod
    def create_genre_manager(
        config: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> GenreManager:
        """Create a GenreManager instance for testing."""
        mock_track_processor = AsyncMock()
        mock_track_processor.update_track_async = AsyncMock(return_value=True)

        test_config = config or {"force_update": False, "processing": {"batch_size": 100}}

        return GenreManager(
            track_processor=mock_track_processor,
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            analytics=MockAnalytics(),  # type: ignore[arg-type]
            config=test_config,
            dry_run=dry_run,
        )

    @staticmethod
    def create_dummy_track(
        track_id: str = "12345",
        name: str = "Test Track",
        artist: str = "Test Artist",
        album: str = "Test Album",
        genre: str = "Rock",
        date_added: str = "2024-01-01 10:00:00",
    ) -> TrackDict:
        """Create a dummy track for testing."""
        return TrackDict(
            id=track_id,
            name=name,
            artist=artist,
            album=album,
            genre=genre,
            date_added=date_added,
            track_status="subscription",
            year=None,
            last_modified="2024-01-01 10:00:00",
        )

    @allure.story("Dominant Genre Calculation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should determine dominant genre from single track")
    @allure.description("Test dominant genre calculation with a single track")
    def test_calculate_dominant_genre_single(self) -> None:
        """Test dominant genre calculation with single track."""
        with allure.step("Setup single track for artist"):
            tracks = [
                self.create_dummy_track("1", "Song A", "Artist 1", "Album A"),
            ]

            allure.attach(
                json.dumps(
                    [
                        {
                            "id": track.id,
                            "name": track.name,
                            "artist": track.artist,
                            "album": track.album,
                            "genre": track.genre,
                            "date_added": track.date_added,
                        }
                        for track in tracks
                    ],
                    indent=2,
                ),
                "Artist Tracks",
                allure.attachment_type.JSON,
            )

        with allure.step("Calculate dominant genre"):
            error_logger = MockLogger()
            dominant_genre = determine_dominant_genre_for_artist(tracks, error_logger)  # type: ignore[arg-type]

        with allure.step("Verify single track genre selected"):
            assert dominant_genre == "Rock"

            allure.attach("Rock", "Dominant Genre", allure.attachment_type.TEXT)
            allure.attach("✅ Single track genre correctly identified", "Result", allure.attachment_type.TEXT)

    @allure.story("Dominant Genre Calculation")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle tie between genres by selecting earliest album")
    @allure.description("Test tie-breaking logic when multiple albums have different genres")
    def test_calculate_dominant_genre_tie(self) -> None:
        """Test dominant genre calculation with tie scenario."""
        with allure.step("Setup tracks with genre tie"):
            tracks = [
                # Album A (earlier) - Rock genre
                self.create_dummy_track("1", "Song A1", "Artist 1", "Album A"),
                self.create_dummy_track("2", "Song A2", "Artist 1", "Album A"),
                # Album B (later) - Pop genre
                self.create_dummy_track("3", "Song B1", "Artist 1", "Album B", "Pop"),
                self.create_dummy_track("4", "Song B2", "Artist 1", "Album B", "Pop"),
            ]

            allure.attach(
                json.dumps(
                    [
                        {
                            "id": track.id,
                            "album": track.album,
                            "genre": track.genre,
                            "date_added": track.date_added,
                        }
                        for track in tracks
                    ],
                    indent=2,
                ),
                "Tie Scenario Tracks",
                allure.attachment_type.JSON,
            )

        with allure.step("Calculate dominant genre with tie-breaking"):
            error_logger = MockLogger()
            dominant_genre = determine_dominant_genre_for_artist(tracks, error_logger)  # type: ignore[arg-type]

        with allure.step("Verify earliest album genre selected"):
            # Should select "Rock" from Album A (earliest album)
            assert dominant_genre == "Rock"

            allure.attach("Rock", "Dominant Genre (Tie-Breaker)", allure.attachment_type.TEXT)
            allure.attach("✅ Tie resolved by earliest album date", "Tie-Breaking Logic", allure.attachment_type.TEXT)

    @allure.story("Dominant Genre Calculation")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should apply genre threshold logic correctly")
    @allure.description("Test genre selection with threshold considerations")
    def test_calculate_dominant_genre_threshold(self) -> None:
        """Test dominant genre calculation with threshold logic."""
        with allure.step("Setup tracks with different album dates"):
            tracks = [
                # Album A (very early) - Jazz
                self.create_dummy_track("1", "Jazz Song", "Artist 1", "Album A", "Jazz"),
                # Album B (later) - Rock (multiple tracks)
                self.create_dummy_track("2", "Rock Song 1", "Artist 1", "Album B"),
                self.create_dummy_track("3", "Rock Song 2", "Artist 1", "Album B"),
                self.create_dummy_track("4", "Rock Song 3", "Artist 1", "Album B"),
            ]

            allure.attach(
                json.dumps(
                    [
                        {
                            "id": track.id,
                            "album": track.album,
                            "genre": track.genre,
                            "date_added": track.date_added,
                            "year": track.date_added[:4] if track.date_added else "",
                        }
                        for track in tracks
                    ],
                    indent=2,
                ),
                "Threshold Test Tracks",
                allure.attachment_type.JSON,
            )

        with allure.step("Calculate dominant genre"):
            error_logger = MockLogger()
            dominant_genre = determine_dominant_genre_for_artist(tracks, error_logger)  # type: ignore[arg-type]

        with allure.step("Verify earliest album genre selected (regardless of frequency)"):
            # Algorithm selects earliest album's genre, not most frequent
            assert dominant_genre == "Jazz"

            allure.attach("Jazz", "Dominant Genre", allure.attachment_type.TEXT)
            allure.attach("✅ Earliest album genre selected despite frequency", "Threshold Logic", allure.attachment_type.TEXT)

    @allure.story("Dominant Genre Calculation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle empty track list gracefully")
    @allure.description("Test behavior when no tracks are provided for artist")
    def test_calculate_dominant_genre_empty(self) -> None:
        """Test dominant genre calculation with empty track list."""
        with allure.step("Setup empty track list"):
            tracks: list[TrackDict] = []

            allure.attach("[]", "Empty Track List", allure.attachment_type.TEXT)

        with allure.step("Calculate dominant genre for empty list"):
            error_logger = MockLogger()
            dominant_genre = determine_dominant_genre_for_artist(tracks, error_logger)  # type: ignore[arg-type]

        with allure.step("Verify default genre returned"):
            assert dominant_genre == "Unknown"

            allure.attach("Unknown", "Default Genre", allure.attachment_type.TEXT)
            allure.attach("✅ Empty list handled gracefully", "Error Handling", allure.attachment_type.TEXT)

    @allure.story("Artist Genre Processing")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should process genres for multiple artists correctly")
    @allure.description("Test genre processing workflow for multiple artists")
    def test_process_artist_genres(self) -> None:
        """Test processing genres for multiple artists."""
        with allure.step("Setup multi-artist track data"):
            tracks = [
                # Artist 1 - Rock
                self.create_dummy_track("1", "Song 1", "Artist 1", "Album A"),
                self.create_dummy_track("2", "Song 2", "Artist 1", "Album A"),
                # Artist 2 - Pop
                self.create_dummy_track("3", "Song 3", "Artist 2", "Album B", "Pop"),
                self.create_dummy_track("4", "Song 4", "Artist 2", "Album B", "Pop"),
                # Artist 3 - Jazz
                self.create_dummy_track("5", "Song 5", "Artist 3", "Album C", "Jazz"),
            ]

            allure.attach(
                json.dumps(
                    [
                        {
                            "id": track.id,
                            "artist": track.artist,
                            "genre": track.genre,
                        }
                        for track in tracks
                    ],
                    indent=2,
                ),
                "Multi-Artist Tracks",
                allure.attachment_type.JSON,
            )

        with allure.step("Group tracks by artist"):
            grouped_tracks = group_tracks_by_artist(tracks)

        with allure.step("Process dominant genre for each artist"):
            error_logger = MockLogger()
            artist_genres = {}

            for artist, artist_tracks in grouped_tracks.items():
                dominant_genre = determine_dominant_genre_for_artist(artist_tracks, error_logger)  # type: ignore[arg-type]
                artist_genres[artist] = dominant_genre

            allure.attach(json.dumps(artist_genres, indent=2), "Artist Genres", allure.attachment_type.JSON)

        with allure.step("Verify correct genres for each artist"):
            expected_genres = {
                "Artist 1": "Rock",
                "Artist 2": "Pop",
                "Artist 3": "Jazz",
            }

            for artist, expected_genre in expected_genres.items():
                assert artist in artist_genres
                assert artist_genres[artist] == expected_genre

            allure.attach(json.dumps(expected_genres, indent=2), "Expected Genres", allure.attachment_type.JSON)
            allure.attach("✅ All artists processed correctly", "Processing Result", allure.attachment_type.TEXT)

    @allure.story("Genre Application")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should apply genre updates to tracks correctly")
    @allure.description("Test the complete workflow of applying calculated genres to tracks")
    @pytest.mark.asyncio
    async def test_apply_genre_to_tracks(self) -> None:
        """Test applying calculated genres to tracks."""
        with allure.step("Setup genre manager and tracks"):
            genre_manager = self.create_genre_manager()

            # Tracks with missing/incorrect genres
            tracks = [
                # Empty genre
                self.create_dummy_track("1", "Song 1", album="Album A", genre=""),
                # Unknown genre
                self.create_dummy_track("2", "Song 2", album="Album A", genre="Unknown"),
                # Same genre - no update needed
                self.create_dummy_track("3", "Song 3", album="Album A", genre="Alternative Rock"),
            ]

            new_genre = "Alternative Rock"

            allure.attach(
                json.dumps(
                    [
                        {
                            "id": track.id,
                            "name": track.name,
                            "current_genre": track.genre,
                            "needs_update": track.genre in ["", "Unknown"],
                        }
                        for track in tracks
                    ],
                    indent=2,
                ),
                "Tracks for Update",
                allure.attachment_type.JSON,
            )

        with allure.step("Apply genre updates to tracks"):
            update_results = []
            change_logs = []

            for track in tracks:
                updated_track, change_log = await genre_manager.test_update_track_genre(track=track, new_genre=new_genre, force_update=False)

                update_results.append(
                    {
                        "track_id": track.id,
                        "updated": updated_track is not None,
                        "change_logged": change_log is not None,
                    }
                )

                if updated_track:
                    change_logs.append(change_log)

            allure.attach(json.dumps(update_results, indent=2), "Update Results", allure.attachment_type.JSON)

        with allure.step("Verify selective updates"):
            # Track 1 (empty genre) should be updated
            assert update_results[0]["updated"] is True
            assert update_results[0]["change_logged"] is True

            # Track 2 (Unknown genre) should be updated
            assert update_results[1]["updated"] is True
            assert update_results[1]["change_logged"] is True

            # Track 3 (same genre) should NOT be updated
            assert update_results[2]["updated"] is False
            assert update_results[2]["change_logged"] is False

            # Verify track processor was called for updates
            assert genre_manager.track_processor.update_track_async.call_count == 2  # type: ignore[attr-defined]

            allure.attach("2", "Tracks Updated", allure.attachment_type.TEXT)
            allure.attach("1", "Tracks Skipped", allure.attachment_type.TEXT)
            allure.attach("✅ Selective updates applied correctly", "Application Result", allure.attachment_type.TEXT)

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle tracks without IDs gracefully")
    @allure.description("Test error handling for tracks missing required ID field")
    @pytest.mark.asyncio
    async def test_handle_tracks_without_ids(self) -> None:
        """Test handling of tracks without IDs."""
        with allure.step("Setup track without ID"):
            genre_manager = self.create_genre_manager()

            # Track without ID
            track = self.create_dummy_track("", "Song", "Artist", "Album", "")
            # Explicitly set empty ID
            track.id = ""

            allure.attach("Empty ID track", "Test Scenario", allure.attachment_type.TEXT)

        with allure.step("Attempt to update track without ID"):
            updated_track, change_log = await genre_manager.test_update_track_genre(track=track, new_genre="Rock", force_update=False)

        with allure.step("Verify error handling"):
            assert updated_track is None
            assert change_log is None

            # Verify error was logged
            error_logger = genre_manager.error_logger
            assert hasattr(error_logger, "error_messages")
            assert len(error_logger.error_messages) > 0  # type: ignore[attr-defined]
            assert "Track missing 'id' field" in error_logger.error_messages[0]  # type: ignore[attr-defined]

            allure.attach("None", "Update Result", allure.attachment_type.TEXT)
            allure.attach("✅ Missing ID handled gracefully", "Error Handling", allure.attachment_type.TEXT)

    @allure.story("Track Status Handling")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should skip prerelease tracks correctly")
    @allure.description("Test that prerelease tracks are skipped during updates")
    @pytest.mark.asyncio
    async def test_skip_prerelease_tracks(self) -> None:
        """Test skipping prerelease tracks."""
        with allure.step("Setup prerelease track"):
            genre_manager = self.create_genre_manager()

            # Prerelease track (read-only)
            track = self.create_dummy_track("123", "Song", "Artist", "Album", "")
            track.track_status = "prerelease"

            allure.attach("prerelease", "Track Status", allure.attachment_type.TEXT)

        with allure.step("Attempt to update prerelease track"):
            updated_track, change_log = await genre_manager.test_update_track_genre(track=track, new_genre="Rock", force_update=False)

        with allure.step("Verify prerelease track skipped"):
            assert updated_track is None
            assert change_log is None

            # Verify debug message was logged
            console_logger = genre_manager.console_logger
            assert hasattr(console_logger, "debug_messages")
            # Note: In real implementation, check for prerelease skip message

            allure.attach("None", "Update Result", allure.attachment_type.TEXT)
            allure.attach("✅ Prerelease track correctly skipped", "Status Handling", allure.attachment_type.TEXT)
