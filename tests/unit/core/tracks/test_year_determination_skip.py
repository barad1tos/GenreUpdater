"""Tests for YearDeterminator skip logic with new_year tracking.

Tests the skip re-processing optimization added in Issue #85.
When new_year matches current year, the album was already processed and should be skipped.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.protocols import (
    CacheServiceProtocol,
    ExternalApiServiceProtocol,
    PendingVerificationServiceProtocol,
)
from core.models.types import TrackDict
from core.tracks.year_consistency import YearConsistencyChecker
from core.tracks.year_determination import YearDeterminator
from core.tracks.year_fallback import YearFallbackHandler


def _create_track(
    track_id: str = "1",
    *,
    name: str = "Track",
    artist: str = "Artist",
    album: str = "Album",
    year: str | None = None,
    old_year: str | None = None,
    new_year: str | None = None,
) -> TrackDict:
    """Create a test TrackDict with specified values."""
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        year=year,
        old_year=old_year,
        new_year=new_year,
    )


def _create_mock_cache_service() -> MagicMock:
    """Create a mock cache service."""
    service = MagicMock()
    service.get_album_year_from_cache = AsyncMock(return_value=None)
    service.get_album_year_entry_from_cache = AsyncMock(return_value=None)
    service.store_album_year_in_cache = AsyncMock()
    return service


def _create_mock_external_api() -> MagicMock:
    """Create a mock external API service."""
    api = MagicMock()
    api.get_album_year = AsyncMock(return_value=(None, False, 0))
    return api


def _create_mock_pending_verification() -> MagicMock:
    """Create a mock pending verification service."""
    service = MagicMock()
    service.mark_for_verification = AsyncMock()
    return service


def _create_mock_consistency_checker() -> MagicMock:
    """Create a mock consistency checker."""
    checker = MagicMock()
    checker.get_dominant_year = MagicMock(return_value=None)
    checker.get_consensus_release_year = MagicMock(return_value=None)
    return checker


def _create_mock_fallback_handler() -> MagicMock:
    """Create a mock fallback handler."""
    handler = MagicMock()
    handler.apply_year_fallback = AsyncMock(return_value=None)
    return handler


def _create_year_determinator(
    cache_service: MagicMock | None = None,
    external_api: MagicMock | None = None,
    pending_verification: MagicMock | None = None,
    consistency_checker: MagicMock | None = None,
    fallback_handler: MagicMock | None = None,
    config: dict[str, Any] | None = None,
) -> YearDeterminator:
    """Create YearDeterminator with mock dependencies."""
    return YearDeterminator(
        cache_service=cast(CacheServiceProtocol, cache_service or _create_mock_cache_service()),
        external_api=cast(ExternalApiServiceProtocol, external_api or _create_mock_external_api()),
        pending_verification=cast(PendingVerificationServiceProtocol, pending_verification or _create_mock_pending_verification()),
        consistency_checker=cast(YearConsistencyChecker, consistency_checker or _create_mock_consistency_checker()),
        fallback_handler=cast(YearFallbackHandler, fallback_handler or _create_mock_fallback_handler()),
        console_logger=logging.getLogger("test.console"),
        error_logger=logging.getLogger("test.error"),
        config=config or {},
    )


@pytest.mark.unit
class TestShouldSkipAlbumNewYearLogic:
    """Tests for should_skip_album new_year skip logic."""

    @pytest.mark.asyncio
    async def test_skips_when_new_year_matches_current_year(self) -> None:
        """Should skip when new_year equals current year (already processed)."""
        determinator = _create_year_determinator()
        tracks = [_create_track("1", year="2020", new_year="2020")]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert result is True

    @pytest.mark.asyncio
    async def test_does_not_skip_when_new_year_differs_from_current(self) -> None:
        """Should NOT skip when new_year differs from current year (user changed)."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track("1", year="2018", new_year="2020")]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_skip_when_new_year_not_set(self) -> None:
        """Should NOT skip when new_year is not set (not yet processed)."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track("1", year="2020", new_year=None)]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_skip_when_new_year_empty_string(self) -> None:
        """Should NOT skip when new_year is empty string."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track("1", year="2020", new_year="")]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_skip_when_current_year_empty(self) -> None:
        """Should NOT skip when current year is empty but new_year is set."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track("1", year="", new_year="2020")]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert result is False

    @pytest.mark.asyncio
    async def test_skips_when_new_year_matches_empty_current(self) -> None:
        """Should skip when both new_year and current year are empty strings."""
        determinator = _create_year_determinator()
        tracks = [_create_track("1", year="", new_year="")]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Empty string new_year is falsy, so it won't enter the skip block
        # This tests that empty strings are handled correctly
        assert result is False

    @pytest.mark.asyncio
    async def test_force_mode_bypasses_new_year_skip(self) -> None:
        """Should NOT skip when force=True even if new_year matches."""
        determinator = _create_year_determinator()
        tracks = [_create_track("1", year="2020", new_year="2020")]

        result = await determinator.should_skip_album(tracks, "Artist", "Album", force=True)

        assert result is False


@pytest.mark.unit
class TestShouldSkipAlbumEmptyTracks:
    """Tests for should_skip_album with empty track list."""

    @pytest.mark.asyncio
    async def test_does_not_skip_when_no_tracks(self) -> None:
        """Should NOT skip when album has no tracks."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks: list[TrackDict] = []

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert result is False


@pytest.mark.unit
class TestShouldSkipAlbumCacheInteraction:
    """Tests for should_skip_album cache and new_year interaction."""

    @pytest.mark.asyncio
    async def test_new_year_check_runs_before_cache_check(self) -> None:
        """Should check new_year before querying cache."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value="2020")
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track("1", year="2020", new_year="2020")]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should skip due to new_year match, cache should not be queried
        assert result is True
        cache_service.get_album_year_from_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_queried_when_new_year_not_matching(self) -> None:
        """Should query cache when new_year doesn't match current."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value="2020")
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track("1", year="2018", new_year="2020")]

        await determinator.should_skip_album(tracks, "Artist", "Album")

        cache_service.get_album_year_from_cache.assert_called_once()


@pytest.mark.unit
class TestShouldSkipAlbumMultipleTracks:
    """Tests for should_skip_album with multiple tracks."""

    @pytest.mark.asyncio
    async def test_uses_first_track_for_new_year_check(self) -> None:
        """Should use first track's new_year for skip check."""
        determinator = _create_year_determinator()
        tracks = [
            _create_track("1", year="2020", new_year="2020"),
            _create_track("2", year="2018", new_year="2019"),  # Different values
        ]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should skip based on first track's new_year matching
        assert result is True

    @pytest.mark.asyncio
    async def test_first_track_determines_skip_decision(self) -> None:
        """First track's state should determine skip decision."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [
            _create_track("1", year="2018", new_year="2020"),  # Mismatch
            _create_track("2", year="2020", new_year="2020"),  # Match
        ]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should NOT skip because first track has mismatch (user changed)
        assert result is False


@pytest.mark.unit
class TestShouldSkipAlbumWithOldYear:
    """Tests for should_skip_album handling of old_year field."""

    @pytest.mark.asyncio
    async def test_old_year_does_not_affect_skip_logic(self) -> None:
        """old_year should not affect skip decision."""
        determinator = _create_year_determinator()
        tracks = [_create_track("1", year="2020", old_year="2015", new_year="2020")]

        result = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should still skip - old_year is irrelevant to skip logic
        assert result is True

    @pytest.mark.asyncio
    async def test_old_year_preserved_when_skipping(self) -> None:
        """old_year should be preserved when album is skipped."""
        determinator = _create_year_determinator()
        track = _create_track("1", year="2020", old_year="2015", new_year="2020")
        tracks = [track]

        await determinator.should_skip_album(tracks, "Artist", "Album")

        # old_year should be unchanged
        assert track.old_year == "2015"
