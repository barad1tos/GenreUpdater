"""Tests for track status utility helpers."""

from __future__ import annotations

from core.models.track_status import (
    can_edit_metadata,
    filter_available_tracks,
    is_available_status,
    is_prerelease_status,
    normalize_track_status,
)
from tests.mocks.track_data import DummyTrackData


def test_normalize_track_status() -> None:
    """Normalization should trim whitespace and lowercase values."""

    assert normalize_track_status(" Subscription ") == "subscription"
    assert normalize_track_status(None) == ""
    assert normalize_track_status(" ") == ""


def test_normalize_applescript_raw_constants() -> None:
    """Normalization should handle AppleScript raw enum constants."""

    # AppleScript sometimes returns raw constants like «constant ****kSub»
    assert normalize_track_status("«constant ****kSub»") == "subscription"
    assert normalize_track_status("«constant ****kPre»") == "prerelease"
    assert normalize_track_status("«constant ****kLoc»") == "local only"
    assert normalize_track_status("«constant ****kPur»") == "purchased"
    assert normalize_track_status("«constant ****kMat»") == "matched"
    assert normalize_track_status("«constant ****kUpl»") == "uploaded"
    assert normalize_track_status("«constant ****kDwn»") == "downloaded"


def test_status_classification_helpers() -> None:
    """Track status helpers should classify statuses consistently."""

    assert is_prerelease_status("prerelease") is True
    assert is_prerelease_status("Subscription") is False
    assert can_edit_metadata("prerelease") is False
    assert can_edit_metadata("subscription") is True
    assert can_edit_metadata(None) is True


def test_filter_available_tracks() -> None:
    """Filtering should include only tracks with supported statuses."""

    available_track = DummyTrackData.create(track_id="1", track_status="subscription")
    unavailable_track = DummyTrackData.create(track_id="2", track_status="prerelease")
    unknown_track = DummyTrackData.create(track_id="3", track_status="unsupported")

    filtered = filter_available_tracks([available_track, unavailable_track, unknown_track])

    assert filtered == [available_track]
    assert is_available_status("subscription") is True
    assert is_available_status("unsupported") is False
