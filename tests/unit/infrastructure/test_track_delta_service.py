"""Unit tests for track delta service."""

from __future__ import annotations

import pytest
from typing import TYPE_CHECKING

from src.infrastructure.track_delta_service import (
    EXPECTED_FIELD_COUNT,
    FIELD_SEPARATOR,
    LINE_SEPARATOR,
    TrackDelta,
    TrackSummary,
    apply_track_delta_to_map,
    compute_track_delta,
    parse_track_summaries,
)
from tests.mocks.track_data import DummyTrackData, DummyTrackSummary, FakeTrackMap

if TYPE_CHECKING:
    from src.shared.data.models import TrackDict


class TestTrackSummary:
    """Tests for TrackSummary dataclass."""

    def test_track_summary_creation(self) -> None:
        """Test TrackSummary can be created with all fields."""
        summary = DummyTrackSummary.create(
            track_id="123",
            date_added="2024-01-01 12:00:00",
            last_modified="2024-01-02 12:00:00",
            track_status="subscription",
        )

        assert summary.track_id == "123"
        assert summary.date_added == "2024-01-01 12:00:00"
        assert summary.last_modified == "2024-01-02 12:00:00"
        assert summary.track_status == "subscription"

    def test_track_summary_empty_status(self) -> None:
        """Test TrackSummary with empty status."""
        summary = DummyTrackSummary.create(track_status="")

        assert summary.track_status == ""
        assert summary.track_id == "12345"  # Default from factory


class TestTrackDelta:
    """Tests for TrackDelta dataclass."""

    def test_track_delta_creation(self) -> None:
        """Test TrackDelta creation with all fields."""
        delta = TrackDelta(
            new_ids=["1", "2"],
            updated_ids=["3", "4"],
            removed_ids=["5", "6"],
        )

        assert delta.new_ids == ["1", "2"]
        assert delta.updated_ids == ["3", "4"]
        assert delta.removed_ids == ["5", "6"]

    def test_has_updates_true(self) -> None:
        """Test has_updates returns True when new or updated tracks exist."""
        delta_with_new = TrackDelta(new_ids=["1"], updated_ids=[], removed_ids=[])
        delta_with_updated = TrackDelta(new_ids=[], updated_ids=["2"], removed_ids=[])
        delta_with_both = TrackDelta(new_ids=["1"], updated_ids=["2"], removed_ids=[])

        assert delta_with_new.has_updates() is True
        assert delta_with_updated.has_updates() is True
        assert delta_with_both.has_updates() is True

    def test_has_updates_false(self) -> None:
        """Test has_updates returns False when no new or updated tracks."""
        delta = TrackDelta(new_ids=[], updated_ids=[], removed_ids=["5"])

        assert delta.has_updates() is False

    def test_has_removals_true(self) -> None:
        """Test has_removals returns True when tracks removed."""
        delta = TrackDelta(new_ids=[], updated_ids=[], removed_ids=["5"])

        assert delta.has_removals() is True

    def test_has_removals_false(self) -> None:
        """Test has_removals returns False when no tracks removed."""
        delta = TrackDelta(new_ids=["1"], updated_ids=["2"], removed_ids=[])

        assert delta.has_removals() is False

    def test_is_empty_true(self) -> None:
        """Test is_empty returns True when no changes detected."""
        delta = TrackDelta(new_ids=[], updated_ids=[], removed_ids=[])

        assert delta.is_empty() is True

    def test_is_empty_false(self) -> None:
        """Test is_empty returns False when changes exist."""
        delta_new = TrackDelta(new_ids=["1"], updated_ids=[], removed_ids=[])
        delta_updated = TrackDelta(new_ids=[], updated_ids=["2"], removed_ids=[])
        delta_removed = TrackDelta(new_ids=[], updated_ids=[], removed_ids=["3"])

        assert delta_new.is_empty() is False
        assert delta_updated.is_empty() is False
        assert delta_removed.is_empty() is False


class TestParseTrackSummaries:
    """Tests for parse_track_summaries function."""

    def test_parse_track_summaries_valid(self) -> None:
        """Test parsing valid AppleScript output."""
        raw_output = (
            f"123{FIELD_SEPARATOR}2024-01-01 12:00:00{FIELD_SEPARATOR}2024-01-01 13:00:00{FIELD_SEPARATOR}subscription{LINE_SEPARATOR}"
            f"456{FIELD_SEPARATOR}2024-01-02 12:00:00{FIELD_SEPARATOR}2024-01-02 13:00:00{FIELD_SEPARATOR}prerelease"
        )

        summaries = parse_track_summaries(raw_output)

        assert len(summaries) == 2

        assert summaries[0].track_id == "123"
        assert summaries[0].date_added == "2024-01-01 12:00:00"
        assert summaries[0].last_modified == "2024-01-01 13:00:00"
        assert summaries[0].track_status == "subscription"

        assert summaries[1].track_id == "456"
        assert summaries[1].date_added == "2024-01-02 12:00:00"
        assert summaries[1].last_modified == "2024-01-02 13:00:00"
        assert summaries[1].track_status == "prerelease"

    def test_parse_track_summaries_empty(self) -> None:
        """Test parsing empty AppleScript output."""
        summaries = parse_track_summaries("")

        assert len(summaries) == 0

    def test_parse_track_summaries_malformed(self) -> None:
        """Test parsing malformed AppleScript output with missing fields."""
        # Missing track_status field (only 3 fields instead of 4)
        raw_output = f"123{FIELD_SEPARATOR}2024-01-01 12:00:00{FIELD_SEPARATOR}2024-01-01 13:00:00"

        summaries = parse_track_summaries(raw_output)

        assert len(summaries) == 1
        assert summaries[0].track_id == "123"
        assert summaries[0].date_added == "2024-01-01 12:00:00"
        assert summaries[0].last_modified == "2024-01-01 13:00:00"
        assert summaries[0].track_status == ""  # Empty due to missing field

    def test_parse_track_summaries_empty_track_id(self) -> None:
        """Test parsing output with empty track ID (should be skipped)."""
        raw_output = f"{FIELD_SEPARATOR}2024-01-01 12:00:00{FIELD_SEPARATOR}2024-01-01 13:00:00{FIELD_SEPARATOR}subscription"

        summaries = parse_track_summaries(raw_output)

        assert len(summaries) == 0  # Empty track_id should be skipped

    def test_parse_track_summaries_whitespace_handling(self) -> None:
        """Test parsing handles whitespace in fields correctly."""
        raw_output = f"  123  {FIELD_SEPARATOR}  2024-01-01 12:00:00  {FIELD_SEPARATOR}  2024-01-01 13:00:00  {FIELD_SEPARATOR}  subscription  "

        summaries = parse_track_summaries(raw_output)

        assert len(summaries) == 1
        assert summaries[0].track_id == "123"  # Stripped
        assert summaries[0].date_added == "2024-01-01 12:00:00"  # Stripped
        assert summaries[0].last_modified == "2024-01-01 13:00:00"  # Stripped
        assert summaries[0].track_status == "subscription"  # Stripped

    def test_parse_track_summaries_mixed_valid_invalid(self) -> None:
        """Test parsing with mix of valid and invalid lines."""
        raw_output = (
            f"123{FIELD_SEPARATOR}2024-01-01 12:00:00{FIELD_SEPARATOR}2024-01-01 13:00:00{FIELD_SEPARATOR}subscription{LINE_SEPARATOR}"
            f"{FIELD_SEPARATOR}{FIELD_SEPARATOR}{FIELD_SEPARATOR}{LINE_SEPARATOR}"  # Empty track_id
            f"456{FIELD_SEPARATOR}2024-01-02 12:00:00{FIELD_SEPARATOR}2024-01-02 13:00:00{FIELD_SEPARATOR}prerelease"
        )

        summaries = parse_track_summaries(raw_output)

        assert len(summaries) == 2  # Only valid entries
        assert summaries[0].track_id == "123"
        assert summaries[1].track_id == "456"


class TestComputeTrackDelta:
    """Tests for compute_track_delta function."""

    def test_compute_track_delta_new_tracks(self) -> None:
        """Test detection of new tracks."""
        summaries = [
            DummyTrackSummary.create(track_id="1"),
            DummyTrackSummary.create(track_id="2"),
        ]
        existing_map: dict[str, TrackDict] = {}  # No existing tracks

        delta = compute_track_delta(summaries, existing_map)

        assert delta.new_ids == ["1", "2"]
        assert delta.updated_ids == []
        assert delta.removed_ids == []

    def test_compute_track_delta_updated_tracks(self) -> None:
        """Test detection of updated tracks based on last_modified."""
        summaries = [
            DummyTrackSummary.create(
                track_id="1",
                last_modified="2024-01-02 12:00:00"  # Updated
            ),
        ]

        # Create existing track with older last_modified
        existing_track = DummyTrackData.create(track_id="1", last_modified="2024-01-01 12:00:00")
        existing_map = {"1": existing_track}

        delta = compute_track_delta(summaries, existing_map)

        assert delta.new_ids == []
        assert delta.updated_ids == ["1"]
        assert delta.removed_ids == []

    def test_compute_track_delta_removed_tracks(self) -> None:
        """Test detection of removed tracks."""
        summaries: list[TrackSummary] = []  # No current tracks

        existing_map = {
            "1": DummyTrackData.create(track_id="1"),
            "2": DummyTrackData.create(track_id="2"),
        }

        delta = compute_track_delta(summaries, existing_map)

        assert delta.new_ids == []
        assert delta.updated_ids == []
        assert delta.removed_ids == ["1", "2"]

    def test_compute_track_delta_status_changes(self) -> None:
        """Test detection of track_status changes (prerelease → subscription)."""
        old_summary, new_summary = DummyTrackSummary.prerelease_to_subscription_pair()

        summaries = [new_summary]

        # Create existing track with old status
        existing_track = DummyTrackData.create(track_id="54321", track_status="prerelease")
        existing_map = {"54321": existing_track}

        delta = compute_track_delta(summaries, existing_map)

        assert delta.new_ids == []
        assert delta.updated_ids == ["54321"]
        assert delta.removed_ids == []

    def test_compute_track_delta_first_run_optimization(self) -> None:
        """Test that first run doesn't mark all tracks as updated due to missing track_status."""
        summaries = [
            DummyTrackSummary.create(
                track_id="1",
                track_status="subscription"  # New field value
            ),
        ]

        # Existing track without track_status (simulating old CSV)
        existing_track = DummyTrackData.create(track_id="1")
        # Don't set track_status at all, simulating old format
        existing_map = {"1": existing_track}

        delta = compute_track_delta(summaries, existing_map)

        # Should not be marked as updated due to empty stored_track_status
        assert delta.new_ids == []
        assert delta.updated_ids == []  # Optimization prevents this
        assert delta.removed_ids == []

    def test_compute_track_delta_date_added_changes(self) -> None:
        """Test detection of date_added changes."""
        summaries = [
            DummyTrackSummary.create(
                track_id="1",
                date_added="2024-01-02 12:00:00"  # Updated
            ),
        ]

        existing_track = DummyTrackData.create(track_id="1", date_added="2024-01-01 12:00:00")
        existing_map = {"1": existing_track}

        delta = compute_track_delta(summaries, existing_map)

        assert delta.new_ids == []
        assert delta.updated_ids == ["1"]
        assert delta.removed_ids == []

    def test_compute_track_delta_no_changes(self) -> None:
        """Test when no changes detected."""
        summaries = [
            DummyTrackSummary.create(track_id="1"),
        ]

        # Identical existing track
        existing_track = DummyTrackData.create(
            track_id="1",
            last_modified="2024-01-01 12:00:00",
            date_added="2024-01-01 12:00:00",
            track_status="subscription"
        )
        existing_map = {"1": existing_track}

        delta = compute_track_delta(summaries, existing_map)

        assert delta.new_ids == []
        assert delta.updated_ids == []
        assert delta.removed_ids == []
        assert delta.is_empty() is True

    def test_compute_track_delta_mixed_changes(self) -> None:
        """Test complex scenario with new, updated, and removed tracks."""
        summaries = [
            DummyTrackSummary.create(track_id="1"),  # Unchanged
            DummyTrackSummary.create(track_id="2", last_modified="2024-01-02 12:00:00"),  # Updated
            DummyTrackSummary.create(track_id="3"),  # New
        ]

        # Existing tracks: 1 (unchanged), 2 (will be updated), 4 (will be removed)
        existing_map = {
            "1": DummyTrackData.create(track_id="1"),
            "2": DummyTrackData.create(track_id="2"),
            "4": DummyTrackData.create(track_id="4"),
        }
        # Set up track 2 to be detected as updated - create new instance with older modification time
        existing_map["2"] = DummyTrackData.create(track_id="2", last_modified="2024-01-01 12:00:00")

        delta = compute_track_delta(summaries, existing_map)

        assert delta.new_ids == ["3"]
        assert delta.updated_ids == ["2"]
        assert delta.removed_ids == ["4"]


class TestApplyTrackDeltaToMap:
    """Tests for apply_track_delta_to_map function."""

    def test_apply_track_delta_to_map_remove_tracks(self) -> None:
        """Test removing tracks from map."""
        track_map = FakeTrackMap([
            DummyTrackData.create(track_id="1"),
            DummyTrackData.create(track_id="2"),
            DummyTrackData.create(track_id="3"),
        ]).to_dict()

        # Remove tracks 2 and 3
        removed_ids = ["2", "3"]
        updated_tracks: list[TrackDict] = []
        summary_lookup: dict[str, TrackSummary] = {}

        apply_track_delta_to_map(track_map, updated_tracks, summary_lookup, removed_ids)

        assert "1" in track_map
        assert "2" not in track_map
        assert "3" not in track_map
        assert len(track_map) == 1

    def test_apply_track_delta_to_map_update_tracks(self) -> None:
        """Test updating tracks with summary data."""
        track_map = FakeTrackMap([
            DummyTrackData.create(track_id="1"),
        ]).to_dict()

        # Update track 1 with new summary data
        updated_track = DummyTrackData.create(track_id="1", name="Updated Track")
        updated_tracks = [updated_track]

        summary = DummyTrackSummary.create(
            track_id="1",
            last_modified="2024-01-02 12:00:00",
            date_added="2024-01-02 11:00:00",
            track_status="subscription",
        )
        summary_lookup = {"1": summary}

        apply_track_delta_to_map(track_map, updated_tracks, summary_lookup, [])

        updated_map_track = track_map["1"]
        assert updated_map_track.name == "Updated Track"
        assert updated_map_track.last_modified == "2024-01-02 12:00:00"
        assert updated_map_track.date_added == "2024-01-02 11:00:00"
        assert updated_map_track.track_status == "subscription"

    def test_apply_track_delta_to_map_add_new_tracks(self) -> None:
        """Test adding new tracks to map."""
        track_map: dict[str, TrackDict] = {}

        new_track = DummyTrackData.create(track_id="1", name="New Track")
        updated_tracks = [new_track]

        summary = DummyTrackSummary.create(track_id="1")
        summary_lookup = {"1": summary}

        apply_track_delta_to_map(track_map, updated_tracks, summary_lookup, [])

        assert "1" in track_map
        assert track_map["1"].name == "New Track"
        assert len(track_map) == 1

    def test_apply_track_delta_to_map_missing_summary(self) -> None:
        """Test updating track without corresponding summary."""
        track_map: dict[str, TrackDict] = {}

        updated_track = DummyTrackData.create(track_id="1", name="Track Without Summary")
        updated_tracks = [updated_track]

        summary_lookup: dict[str, TrackSummary] = {}  # No summary for track 1

        apply_track_delta_to_map(track_map, updated_tracks, summary_lookup, [])

        # Track should still be added, but without summary metadata
        assert "1" in track_map
        assert track_map["1"].name == "Track Without Summary"

    def test_apply_track_delta_to_map_empty_track_id(self) -> None:
        """Test handling tracks with empty/missing ID."""
        track_map: dict[str, TrackDict] = {}

        # Track with empty ID
        invalid_track = DummyTrackData.create(track_id="", name="Invalid Track")
        updated_tracks = [invalid_track]

        summary_lookup: dict[str, TrackSummary] = {}

        apply_track_delta_to_map(track_map, updated_tracks, summary_lookup, [])

        # Track with empty ID should be skipped
        assert len(track_map) == 0