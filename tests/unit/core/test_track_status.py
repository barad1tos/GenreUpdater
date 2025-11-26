"""Tests for track status utility helpers."""

from __future__ import annotations

from src.core.models.status import (
    can_edit_metadata,
    filter_available_tracks,
    is_available_status,
    is_prerelease_status,
    is_subscription_status,
    normalize_track_status,
)
from tests.mocks.track_data import DummyTrackData


def test_normalize_track_status() -> None:
    """Normalization should trim whitespace and lowercase values."""

    assert normalize_track_status(" Subscription ") == "subscription"
    assert normalize_track_status(None) == ""
    assert normalize_track_status(" ") == ""


def test_status_classification_helpers() -> None:
    """Track status helpers should classify statuses consistently."""

    assert is_prerelease_status("prerelease") is True
    assert is_prerelease_status("Subscription") is False
    assert is_subscription_status("subscription") is True
    assert is_subscription_status("matched") is False
    assert can_edit_metadata("prerelease") is False
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
