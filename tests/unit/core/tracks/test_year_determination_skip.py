"""Tests for YearDeterminator skip logic with year_set_by_mgu tracking and pre-check pipeline.

Tests the skip re-processing optimization added in Issue #85.
When year_set_by_mgu matches current year, the album was already processed and should be skipped.

Also tests the pre-check pipeline added in Issue #75:
- Pre-check 1: Already processed (year_set_by_mgu tracking)
- Pre-check 2: Recently rejected by FALLBACK
- Pre-check 3: Year consistent across all tracks
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
from services.pending_verification import PendingAlbumEntry, VerificationReason


def _create_track(
    track_id: str = "1",
    *,
    name: str = "Track",
    artist: str = "Artist",
    album: str = "Album",
    year: str | None = None,
    year_before_mgu: str | None = None,
    year_set_by_mgu: str | None = None,
) -> TrackDict:
    """Create a test TrackDict with specified values."""
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        year=year,
        year_before_mgu=year_before_mgu,
        year_set_by_mgu=year_set_by_mgu,
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
    service.get_entry = AsyncMock(return_value=None)
    service.is_verification_needed = AsyncMock(return_value=False)
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
    # Cast through object to satisfy type checker for mock objects
    return YearDeterminator(
        cache_service=cast(CacheServiceProtocol, cast(object, cache_service or _create_mock_cache_service())),
        external_api=cast(ExternalApiServiceProtocol, cast(object, external_api or _create_mock_external_api())),
        pending_verification=cast(PendingVerificationServiceProtocol, cast(object, pending_verification or _create_mock_pending_verification())),
        consistency_checker=cast(YearConsistencyChecker, consistency_checker or _create_mock_consistency_checker()),
        fallback_handler=cast(YearFallbackHandler, fallback_handler or _create_mock_fallback_handler()),
        console_logger=logging.getLogger("test.console"),
        error_logger=logging.getLogger("test.error"),
        config=config or {},
    )


@pytest.mark.unit
class TestShouldSkipAlbumNewYearLogic:
    """Tests for should_skip_album year_set_by_mgu skip logic (Pre-check 1)."""

    @pytest.mark.asyncio
    async def test_skips_when_year_set_by_mgu_matches_current_year(self) -> None:
        """Should skip when year_set_by_mgu equals current year (already processed)."""
        determinator = _create_year_determinator()
        tracks = [_create_track(year="2020", year_set_by_mgu="2020")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is True
        assert reason == "already_processed"

    @pytest.mark.asyncio
    async def test_does_not_skip_when_year_set_by_mgu_differs_from_current(self) -> None:
        """Should NOT skip when year_set_by_mgu differs from current year (user changed)."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track(year="2018", year_set_by_mgu="2020")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is False
        assert reason == ""

    @pytest.mark.asyncio
    async def test_does_not_skip_when_year_set_by_mgu_not_set(self) -> None:
        """Should NOT skip when year_set_by_mgu is not set (not yet processed)."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track(year="2020")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is False
        assert reason == ""

    @pytest.mark.asyncio
    async def test_does_not_skip_when_year_set_by_mgu_empty_string(self) -> None:
        """Should NOT skip when year_set_by_mgu is empty string."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track(year="2020", year_set_by_mgu="")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is False
        assert reason == ""

    @pytest.mark.asyncio
    async def test_does_not_skip_when_current_year_empty(self) -> None:
        """Should NOT skip when current year is empty but year_set_by_mgu is set."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track(year="", year_set_by_mgu="2020")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is False
        assert reason == ""

    @pytest.mark.asyncio
    async def test_skips_when_year_set_by_mgu_matches_empty_current(self) -> None:
        """Should skip when both year_set_by_mgu and current year are empty strings."""
        determinator = _create_year_determinator()
        tracks = [_create_track(year="", year_set_by_mgu="")]

        should_skip, _reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Empty string year_set_by_mgu is falsy, so it won't enter the skip block
        # This tests that empty strings are handled correctly
        assert should_skip is False

    @pytest.mark.asyncio
    async def test_force_mode_bypasses_year_set_by_mgu_skip(self) -> None:
        """Should NOT skip when force=True even if year_set_by_mgu matches."""
        determinator = _create_year_determinator()
        tracks = [_create_track(year="2020", year_set_by_mgu="2020")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album", force=True)

        assert should_skip is False
        assert reason == ""


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

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is False
        assert reason == ""


@pytest.mark.unit
class TestShouldSkipAlbumCacheInteraction:
    """Tests for should_skip_album cache and year_set_by_mgu interaction."""

    @pytest.mark.asyncio
    async def test_year_set_by_mgu_check_runs_before_cache_check(self) -> None:
        """Should check year_set_by_mgu before querying cache."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value="2020")
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track(year="2020", year_set_by_mgu="2020")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should skip due to year_set_by_mgu match, cache should not be queried
        assert should_skip is True
        assert reason == "already_processed"
        cache_service.get_album_year_from_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_queried_when_year_set_by_mgu_not_matching(self) -> None:
        """Should query cache when year_set_by_mgu doesn't match current."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value="2020")
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track(year="2018", year_set_by_mgu="2020")]

        await determinator.should_skip_album(tracks, "Artist", "Album")

        cache_service.get_album_year_from_cache.assert_called_once()


@pytest.mark.unit
class TestShouldSkipAlbumMultipleTracks:
    """Tests for should_skip_album with multiple tracks."""

    @pytest.mark.asyncio
    async def test_uses_first_track_for_year_set_by_mgu_check(self) -> None:
        """Should use first track's year_set_by_mgu for skip check."""
        determinator = _create_year_determinator()
        tracks = [
            _create_track(year="2020", year_set_by_mgu="2020"),
            _create_track("2", year="2018", year_set_by_mgu="2019"),  # Different values
        ]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should skip based on first track's year_set_by_mgu matching
        assert should_skip is True
        assert reason == "already_processed"

    @pytest.mark.asyncio
    async def test_first_track_determines_skip_decision(self) -> None:
        """First track's state should determine skip decision."""
        cache_service = _create_mock_cache_service()
        cache_service.get_album_year_from_cache = AsyncMock(return_value=None)
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [
            _create_track(year="2018", year_set_by_mgu="2020"),  # Mismatch
            _create_track("2", year="2020", year_set_by_mgu="2020"),  # Match
        ]

        should_skip, _reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should NOT skip because first track has mismatch (user changed)
        assert should_skip is False


@pytest.mark.unit
class TestShouldSkipAlbumWithOldYear:
    """Tests for should_skip_album handling of year_before_mgu field."""

    @pytest.mark.asyncio
    async def test_year_before_mgu_does_not_affect_skip_logic(self) -> None:
        """year_before_mgu should not affect skip decision."""
        determinator = _create_year_determinator()
        tracks = [_create_track(year="2020", year_before_mgu="2015", year_set_by_mgu="2020")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should still skip - year_before_mgu is irrelevant to skip logic
        assert should_skip is True
        assert reason == "already_processed"

    @pytest.mark.asyncio
    async def test_year_before_mgu_preserved_when_skipping(self) -> None:
        """year_before_mgu should be preserved when album is skipped."""
        determinator = _create_year_determinator()
        track = _create_track(year="2020", year_before_mgu="2015", year_set_by_mgu="2020")
        tracks = [track]

        await determinator.should_skip_album(tracks, "Artist", "Album")

        # year_before_mgu should be unchanged
        assert track.year_before_mgu == "2015"


@pytest.mark.unit
class TestShouldSkipAlbumRecentRejection:
    """Tests for should_skip_album pre-check 2: recently rejected by FALLBACK.

    Issue #75: Albums rejected by FALLBACK within the verification period
    should be skipped to avoid wasted API calls.
    """

    @pytest.mark.asyncio
    async def test_skips_when_recently_rejected_suspicious_year_change(self) -> None:
        """Should skip when album was recently rejected due to suspicious year change."""
        from datetime import UTC, datetime

        pending_service = _create_mock_pending_verification()
        pending_entry = PendingAlbumEntry(
            timestamp=datetime.now(UTC),
            artist="Artist",
            album="Album",
            reason=VerificationReason.from_string("suspicious_year_change"),
            metadata="",
        )
        pending_service.get_entry = AsyncMock(return_value=pending_entry)
        pending_service.is_verification_needed = AsyncMock(return_value=False)  # Not yet time to re-verify

        determinator = _create_year_determinator(pending_verification=pending_service)
        tracks = [_create_track(year="2020")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is True
        assert reason == "recently_rejected:suspicious_year_change"

    @pytest.mark.asyncio
    async def test_does_not_skip_when_verification_period_elapsed(self) -> None:
        """Should NOT skip when verification period has elapsed."""
        from datetime import UTC, datetime

        pending_service = _create_mock_pending_verification()
        pending_entry = PendingAlbumEntry(
            timestamp=datetime.now(UTC),
            artist="Artist",
            album="Album",
            reason=VerificationReason.from_string("suspicious_year_change"),
            metadata="",
        )
        pending_service.get_entry = AsyncMock(return_value=pending_entry)
        pending_service.is_verification_needed = AsyncMock(return_value=True)  # Time to re-verify

        cache_service = _create_mock_cache_service()
        determinator = _create_year_determinator(
            pending_verification=pending_service,
            cache_service=cache_service,
        )
        tracks = [_create_track(year="2020")]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should proceed to next checks (cache, etc.) since verification period elapsed
        assert should_skip is False or reason != "recently_rejected:suspicious_year_change"

    @pytest.mark.asyncio
    async def test_does_not_skip_for_non_rejection_reasons(self) -> None:
        """Should NOT skip for non-rejection reasons like no_year_found."""
        from datetime import UTC, datetime

        pending_service = _create_mock_pending_verification()
        pending_entry = PendingAlbumEntry(
            timestamp=datetime.now(UTC),
            artist="Artist",
            album="Album",
            reason=VerificationReason.NO_YEAR_FOUND,  # Not a rejection reason
            metadata="",
        )
        pending_service.get_entry = AsyncMock(return_value=pending_entry)

        cache_service = _create_mock_cache_service()
        determinator = _create_year_determinator(
            pending_verification=pending_service,
            cache_service=cache_service,
        )
        tracks = [_create_track(year="2020")]

        _should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should NOT be skipped due to rejection (may be skipped for other reasons)
        assert not reason.startswith("recently_rejected:")


@pytest.mark.unit
class TestShouldSkipAlbumConsistentYear:
    """Tests for should_skip_album pre-check 3: year consistent across all tracks.

    Issue #75: Albums where all tracks have the same valid year
    should be skipped to avoid unnecessary API calls.
    """

    @pytest.mark.asyncio
    async def test_skips_when_all_tracks_have_same_year(self) -> None:
        """Should skip when all tracks have the same valid year."""
        determinator = _create_year_determinator()
        tracks = [
            _create_track(year="2020"),
            _create_track("2", year="2020"),
            _create_track("3", year="2020"),
        ]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is True
        assert reason == "year_consistent"

    @pytest.mark.asyncio
    async def test_does_not_skip_when_tracks_have_different_years(self) -> None:
        """Should NOT skip when tracks have different years."""
        cache_service = _create_mock_cache_service()
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [
            _create_track(year="2020"),
            _create_track("2", year="2019"),  # Different year
            _create_track("3", year="2020"),
        ]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is False or reason != "year_consistent"

    @pytest.mark.asyncio
    async def test_does_not_skip_when_some_tracks_missing_year(self) -> None:
        """Should NOT skip when some tracks have empty year."""
        cache_service = _create_mock_cache_service()
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [
            _create_track(year="2020"),
            _create_track("2", year=""),  # Missing year
            _create_track("3", year="2020"),
        ]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert should_skip is False or reason != "year_consistent"

    @pytest.mark.asyncio
    async def test_does_not_skip_when_all_years_empty(self) -> None:
        """Should NOT skip when all tracks have empty year."""
        cache_service = _create_mock_cache_service()
        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [
            _create_track(year=""),
            _create_track("2", year=""),
        ]

        _should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        assert reason != "year_consistent"


@pytest.mark.unit
class TestPreCheckPriority:
    """Tests for pre-check priority order.

    Pre-checks should run in order: already_processed → recently_rejected → year_consistent
    """

    @pytest.mark.asyncio
    async def test_already_processed_takes_priority_over_consistent_year(self) -> None:
        """Pre-check 1 (already_processed) should run before pre-check 3 (year_consistent)."""
        determinator = _create_year_determinator()
        # All tracks have same year AND year_set_by_mgu matches current
        tracks = [
            _create_track(year="2020", year_set_by_mgu="2020"),
            _create_track("2", year="2020"),
            _create_track("3", year="2020"),
        ]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should skip with "already_processed", not "year_consistent"
        assert should_skip is True
        assert reason == "already_processed"

    @pytest.mark.asyncio
    async def test_recently_rejected_takes_priority_over_consistent_year(self) -> None:
        """Pre-check 2 (recently_rejected) should run before pre-check 3 (year_consistent)."""
        from datetime import UTC, datetime

        pending_service = _create_mock_pending_verification()
        pending_entry = PendingAlbumEntry(
            timestamp=datetime.now(UTC),
            artist="Artist",
            album="Album",
            reason=VerificationReason.from_string("suspicious_year_change"),
            metadata="",
        )
        pending_service.get_entry = AsyncMock(return_value=pending_entry)
        pending_service.is_verification_needed = AsyncMock(return_value=False)

        determinator = _create_year_determinator(pending_verification=pending_service)
        # All tracks have same year
        tracks = [
            _create_track(year="2020"),
            _create_track("2", year="2020"),
        ]

        should_skip, reason = await determinator.should_skip_album(tracks, "Artist", "Album")

        # Should skip with "recently_rejected:...", not "year_consistent"
        assert should_skip is True
        assert reason.startswith("recently_rejected:")
