"""Unit tests for track delta service.

This module tests the TrackDelta dataclass and compute_track_delta function
that are used for incremental track synchronization.
"""

from __future__ import annotations

import pytest
from src.core.tracks.delta import TrackDelta, compute_track_delta
from src.core.models.track import TrackDict


@pytest.fixture
def create_track() -> type[TrackDict]:
    """Factory fixture for creating TrackDict test instances."""
    return TrackDict


class TestTrackDelta:
    """Tests for TrackDelta dataclass methods."""

    def test_has_updates_with_new_tracks(self) -> None:
        """Test has_updates returns True when new tracks are present."""
        delta = TrackDelta(new_ids=["track1", "track2"], updated_ids=[], removed_ids=[])
        assert delta.has_updates() is True

    def test_has_updates_with_updated_tracks(self) -> None:
        """Test has_updates returns True when updated tracks are present."""
        delta = TrackDelta(new_ids=[], updated_ids=["track3"], removed_ids=[])
        assert delta.has_updates() is True

    def test_has_updates_with_both_new_and_updated(self) -> None:
        """Test has_updates returns True with both new and updated tracks."""
        delta = TrackDelta(new_ids=["track1"], updated_ids=["track2"], removed_ids=[])
        assert delta.has_updates() is True

    def test_has_updates_false_with_only_removals(self) -> None:
        """Test has_updates returns False when only removed tracks present."""
        delta = TrackDelta(new_ids=[], updated_ids=[], removed_ids=["track4"])
        assert delta.has_updates() is False

    def test_has_removals_true(self) -> None:
        """Test has_removals returns True when tracks were removed."""
        delta = TrackDelta(new_ids=[], updated_ids=[], removed_ids=["track5", "track6"])
        assert delta.has_removals() is True

    def test_has_removals_false(self) -> None:
        """Test has_removals returns False when no tracks removed."""
        delta = TrackDelta(new_ids=["track1"], updated_ids=["track2"], removed_ids=[])
        assert delta.has_removals() is False

    def test_is_empty_true(self) -> None:
        """Test is_empty returns True when no changes detected."""
        delta = TrackDelta(new_ids=[], updated_ids=[], removed_ids=[])
        assert delta.is_empty() is True

    def test_is_empty_false_with_new(self) -> None:
        """Test is_empty returns False when new tracks present."""
        delta = TrackDelta(new_ids=["track1"], updated_ids=[], removed_ids=[])
        assert delta.is_empty() is False

    def test_is_empty_false_with_updated(self) -> None:
        """Test is_empty returns False when updated tracks present."""
        delta = TrackDelta(new_ids=[], updated_ids=["track2"], removed_ids=[])
        assert delta.is_empty() is False

    def test_is_empty_false_with_removed(self) -> None:
        """Test is_empty returns False when removed tracks present."""
        delta = TrackDelta(new_ids=[], updated_ids=[], removed_ids=["track3"])
        assert delta.is_empty() is False


class TestComputeTrackDelta:
    """Tests for compute_track_delta function."""

    @staticmethod
    def _assert_single_track_updated(
        create_track: type[TrackDict],
        current_track_kwargs: dict,
        existing_track_kwargs: dict,
    ) -> None:
        """Helper method to test single track update scenarios.

        Args:
            create_track: Factory function for creating TrackDict instances
            current_track_kwargs: Keyword arguments for current track
            existing_track_kwargs: Keyword arguments for existing track
        """
        # Set common defaults if not provided
        defaults = {
            "id": "1",
            "name": "Track 1",
            "artist": "Artist A",
            "album": "Album 1",
        }

        current_kwargs = defaults | current_track_kwargs
        existing_kwargs = defaults | existing_track_kwargs

        current_tracks = [create_track(**current_kwargs)]
        existing_map = {"1": create_track(**existing_kwargs)}

        delta = compute_track_delta(current_tracks, existing_map)

        assert delta.new_ids == []
        assert delta.updated_ids == ["1"]
        assert delta.removed_ids == []

    def test_identifies_new_tracks(self, create_track: type[TrackDict]) -> None:
        """Test identifying tracks that are new to the library."""
        current_tracks = [
            create_track(id="1", name="Track 1", artist="Artist A", album="Album 1"),
            create_track(id="2", name="Track 2", artist="Artist B", album="Album 2"),
            create_track(id="3", name="Track 3", artist="Artist C", album="Album 3"),
        ]

        existing_map = {
            "1": create_track(id="1", name="Track 1", artist="Artist A", album="Album 1"),
        }

        delta = compute_track_delta(current_tracks, existing_map)

        assert set(delta.new_ids) == {"2", "3"}
        assert delta.updated_ids == []
        assert delta.removed_ids == []

    def test_identifies_removed_tracks(self, create_track: type[TrackDict]) -> None:
        """Test identifying tracks removed from the library."""
        current_tracks = [
            create_track(id="1", name="Track 1", artist="Artist A", album="Album 1"),
        ]

        existing_map = {
            "1": create_track(id="1", name="Track 1", artist="Artist A", album="Album 1"),
            "2": create_track(id="2", name="Track 2", artist="Artist B", album="Album 2"),
            "3": create_track(id="3", name="Track 3", artist="Artist C", album="Album 3"),
        }

        delta = compute_track_delta(current_tracks, existing_map)

        assert delta.new_ids == []
        assert delta.updated_ids == []
        assert set(delta.removed_ids) == {"2", "3"}

    def test_identifies_updated_tracks_by_last_modified(self, create_track: type[TrackDict]) -> None:
        """Test identifying tracks updated based on last_modified timestamp."""
        self._assert_single_track_updated(
            create_track,
            current_track_kwargs={"last_modified": "2024-01-02 12:00:00"},  # Newer
            existing_track_kwargs={"last_modified": "2024-01-01 12:00:00"},  # Older
        )

    def test_identifies_updated_tracks_by_date_added(self, create_track: type[TrackDict]) -> None:
        """Test identifying tracks with changed date_added."""
        self._assert_single_track_updated(
            create_track,
            current_track_kwargs={"date_added": "2024-01-15 10:00:00"},  # Changed
            existing_track_kwargs={"date_added": "2024-01-01 10:00:00"},  # Original
        )

    def test_identifies_status_changes(self, create_track: type[TrackDict]) -> None:
        """Test identifying tracks with status changes (prerelease -> subscription)."""
        self._assert_single_track_updated(
            create_track,
            current_track_kwargs={"track_status": "subscription"},  # Changed from prerelease
            existing_track_kwargs={"track_status": "prerelease"},  # Original status
        )

    def test_ignores_status_change_when_empty(self, create_track: type[TrackDict]) -> None:
        """Test that empty track_status values don't trigger updates."""
        # Test case 1: Both empty - no update
        current_tracks = [
            create_track(id="1", name="Track 1", artist="Artist", album="Album", track_status=""),
        ]
        existing_map = {
            "1": create_track(id="1", name="Track 1", artist="Artist", album="Album", track_status=""),
        }

        delta = compute_track_delta(current_tracks, existing_map)
        assert delta.updated_ids == []

        # Test case 2: Old empty, new has value - no update (prevents mass updates)
        current_tracks = [
            create_track(id="2", name="Track 2", artist="Artist", album="Album", track_status="subscription"),
        ]
        existing_map = {
            "2": create_track(id="2", name="Track 2", artist="Artist", album="Album", track_status=""),
        }

        delta = compute_track_delta(current_tracks, existing_map)
        assert delta.updated_ids == []

    def test_no_changes_detected(self, create_track: type[TrackDict]) -> None:
        """Test when tracks haven't changed."""
        tracks = [
            create_track(
                id="1",
                name="Track 1",
                artist="Artist A",
                album="Album 1",
                date_added="2024-01-01 12:00:00",
                last_modified="2024-01-01 12:00:00",
                track_status="subscription",
            ),
            create_track(
                id="2",
                name="Track 2",
                artist="Artist B",
                album="Album 2",
                date_added="2024-01-01 13:00:00",
                last_modified="2024-01-01 13:00:00",
                track_status="subscription",
            ),
        ]

        existing_map = {
            "1": create_track(
                id="1",
                name="Track 1",
                artist="Artist A",
                album="Album 1",
                date_added="2024-01-01 12:00:00",
                last_modified="2024-01-01 12:00:00",
                track_status="subscription",
            ),
            "2": create_track(
                id="2",
                name="Track 2",
                artist="Artist B",
                album="Album 2",
                date_added="2024-01-01 13:00:00",
                last_modified="2024-01-01 13:00:00",
                track_status="subscription",
            ),
        }

        delta = compute_track_delta(tracks, existing_map)

        assert delta.is_empty()
        assert delta.new_ids == []
        assert delta.updated_ids == []
        assert delta.removed_ids == []

    def test_handles_none_values(self, create_track: type[TrackDict]) -> None:
        """Test handling of None values in track fields."""
        current_tracks = [
            create_track(
                id="1",
                name="Track 1",
                artist="Artist",
                album="Album",
                last_modified=None,  # None should be treated as empty string
                date_added=None,
                track_status=None,
            ),
        ]

        existing_map = {
            "1": create_track(
                id="1",
                name="Track 1",
                artist="Artist",
                album="Album",
                last_modified="",
                date_added="",
                track_status="",
            ),
        }

        delta = compute_track_delta(current_tracks, existing_map)

        # None and empty string should be treated as equivalent
        assert delta.updated_ids == []
        assert delta.is_empty()

    def test_complex_scenario(self, create_track: type[TrackDict]) -> None:
        """Test a complex scenario with multiple types of changes."""
        current_tracks = [
            # Unchanged track
            create_track(id="1", name="Track 1", artist="Artist", album="Album", last_modified="2024-01-01 12:00:00"),
            # Updated track (modified date)
            create_track(id="2", name="Track 2", artist="Artist", album="Album", last_modified="2024-01-02 12:00:00"),
            # New track
            create_track(id="3", name="Track 3", artist="Artist", album="Album", last_modified="2024-01-01 12:00:00"),
            # Status change
            create_track(id="4", name="Track 4", artist="Artist", album="Album", track_status="subscription"),
            # Track 5 removed (not in current)
        ]

        existing_map = {
            "1": create_track(id="1", name="Track 1", artist="Artist", album="Album", last_modified="2024-01-01 12:00:00"),
            "2": create_track(id="2", name="Track 2", artist="Artist", album="Album", last_modified="2024-01-01 12:00:00"),
            "4": create_track(id="4", name="Track 4", artist="Artist", album="Album", track_status="prerelease"),
            "5": create_track(id="5", name="Track 5", artist="Artist", album="Album", last_modified="2024-01-01 12:00:00"),
        }

        delta = compute_track_delta(current_tracks, existing_map)

        assert delta.new_ids == ["3"]
        assert set(delta.updated_ids) == {"2", "4"}
        assert delta.removed_ids == ["5"]
        assert delta.has_updates() is True
        assert delta.has_removals() is True

    def test_empty_inputs(self, create_track: type[TrackDict]) -> None:
        """Test with empty inputs."""
        # No current tracks, some existing
        delta = compute_track_delta([], {"1": create_track(id="1", name="Track 1", artist="Artist", album="Album")})
        assert delta.new_ids == []
        assert delta.removed_ids == ["1"]

        # Some current tracks, no existing
        delta = compute_track_delta([create_track(id="1", name="Track 1", artist="Artist", album="Album")], {})
        assert delta.new_ids == ["1"]
        assert delta.removed_ids == []

        # Both empty
        delta = compute_track_delta([], {})
        assert delta.is_empty()
