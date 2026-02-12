"""Tests for YearDeterminator private helper methods.

Tests for _try_local_sources() and _fetch_from_api() methods
extracted during cognitive complexity refactoring.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.protocols import (
    CacheServiceProtocol,
    ExternalApiServiceProtocol,
    PendingVerificationServiceProtocol,
)
from core.models.types import TrackDict
from core.tracks.year_consistency import YearConsistencyChecker
from core.tracks.year_determination import (
    CACHE_TRUST_THRESHOLD,
    CONSENSUS_YEAR_CONFIDENCE,
    YearDeterminator,
)
from core.tracks.year_fallback import YearFallbackHandler
from core.models.cache_types import AlbumCacheEntry
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


def _create_track(
    track_id: str = "1",
    *,
    name: str = "Track",
    artist: str = "Artist",
    album: str = "Album",
    year: str | None = None,
    release_year: str | None = None,
    date_added: str | None = None,
) -> TrackDict:
    """Create a test TrackDict with specified values."""
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        year=year,
        release_year=release_year,
        date_added=date_added,
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
    api.get_album_year = AsyncMock(return_value=(None, False, 0, {}))
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
    config: AppConfig | None = None,
) -> YearDeterminator:
    """Create YearDeterminator with mock dependencies."""
    return YearDeterminator(
        cache_service=cast(CacheServiceProtocol, cast(object, cache_service or _create_mock_cache_service())),
        external_api=cast(ExternalApiServiceProtocol, cast(object, external_api or _create_mock_external_api())),
        pending_verification=cast(
            PendingVerificationServiceProtocol,
            cast(object, pending_verification or _create_mock_pending_verification()),
        ),
        consistency_checker=cast(YearConsistencyChecker, consistency_checker or _create_mock_consistency_checker()),
        fallback_handler=cast(YearFallbackHandler, fallback_handler or _create_mock_fallback_handler()),
        console_logger=logging.getLogger("test.console"),
        error_logger=logging.getLogger("test.error"),
        config=config or create_test_app_config(),
    )


@pytest.mark.unit
class TestTryLocalSources:
    """Tests for _try_local_sources() helper method."""

    @pytest.mark.asyncio
    async def test_returns_dominant_year_when_present(self) -> None:
        """Should return dominant year from tracks if available."""
        consistency_checker = _create_mock_consistency_checker()
        consistency_checker.get_dominant_year = MagicMock(return_value="2020")

        determinator = _create_year_determinator(consistency_checker=consistency_checker)
        tracks = [_create_track(year="2020")]

        result = await determinator._try_local_sources("Artist", "Album", tracks)

        assert result == "2020"
        consistency_checker.get_dominant_year.assert_called_once_with(tracks)

    @pytest.mark.asyncio
    async def test_returns_cached_year_with_high_confidence(self) -> None:
        """Should return cached year when confidence >= threshold."""
        cache_service = _create_mock_cache_service()
        cache_entry = AlbumCacheEntry(
            artist="Artist",
            album="Album",
            year="2019",
            timestamp=0.0,
            confidence=CACHE_TRUST_THRESHOLD,
        )
        cache_service.get_album_year_entry_from_cache = AsyncMock(return_value=cache_entry)

        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track()]

        result = await determinator._try_local_sources("Artist", "Album", tracks)

        assert result == "2019"
        cache_service.get_album_year_entry_from_cache.assert_called_once_with("Artist", "Album")

    @pytest.mark.asyncio
    async def test_ignores_cached_year_with_low_confidence(self) -> None:
        """Should ignore cached year when confidence < threshold."""
        cache_service = _create_mock_cache_service()
        cache_entry = AlbumCacheEntry(
            artist="Artist",
            album="Album",
            year="2019",
            timestamp=0.0,
            confidence=CACHE_TRUST_THRESHOLD - 1,
        )
        cache_service.get_album_year_entry_from_cache = AsyncMock(return_value=cache_entry)

        determinator = _create_year_determinator(cache_service=cache_service)
        tracks = [_create_track()]

        result = await determinator._try_local_sources("Artist", "Album", tracks)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_consensus_release_year(self) -> None:
        """Should return consensus release_year and cache it."""
        cache_service = _create_mock_cache_service()
        consistency_checker = _create_mock_consistency_checker()
        consistency_checker.get_consensus_release_year = MagicMock(return_value="2018")

        determinator = _create_year_determinator(
            cache_service=cache_service,
            consistency_checker=consistency_checker,
        )
        tracks = [_create_track(release_year="2018")]

        result = await determinator._try_local_sources("Artist", "Album", tracks)

        assert result == "2018"
        cache_service.store_album_year_in_cache.assert_called_once_with(
            "Artist",
            "Album",
            "2018",
            confidence=CONSENSUS_YEAR_CONFIDENCE,
        )

    @pytest.mark.asyncio
    async def test_returns_none_when_no_local_sources(self) -> None:
        """Should return None when no local sources have year data."""
        determinator = _create_year_determinator()
        tracks = [_create_track()]

        result = await determinator._try_local_sources("Artist", "Album", tracks)

        assert result is None

    @pytest.mark.asyncio
    async def test_priority_dominant_over_cache(self) -> None:
        """Dominant year should take priority over cached year."""
        cache_service = _create_mock_cache_service()
        cache_entry = AlbumCacheEntry(
            artist="Artist",
            album="Album",
            year="2019",
            timestamp=0.0,
            confidence=100,
        )
        cache_service.get_album_year_entry_from_cache = AsyncMock(return_value=cache_entry)

        consistency_checker = _create_mock_consistency_checker()
        consistency_checker.get_dominant_year = MagicMock(return_value="2020")

        determinator = _create_year_determinator(
            cache_service=cache_service,
            consistency_checker=consistency_checker,
        )
        tracks = [_create_track(year="2020")]

        result = await determinator._try_local_sources("Artist", "Album", tracks)

        assert result == "2020"
        # Cache should NOT be checked if dominant year exists
        cache_service.get_album_year_entry_from_cache.assert_not_called()


@pytest.mark.unit
class TestFetchFromApi:
    """Tests for _fetch_from_api() helper method."""

    @pytest.mark.asyncio
    async def test_returns_validated_year_from_api(self) -> None:
        """Should return validated year from API and cache it."""
        cache_service = _create_mock_cache_service()
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("2021", True, 95, {"2021": 95}))

        fallback_handler = _create_mock_fallback_handler()
        fallback_handler.apply_year_fallback = AsyncMock(return_value="2021")

        determinator = _create_year_determinator(
            cache_service=cache_service,
            external_api=external_api,
            fallback_handler=fallback_handler,
        )
        tracks = [_create_track()]

        result = await determinator._fetch_from_api("Artist", "Album", tracks, None)

        assert result == "2021"
        cache_service.store_album_year_in_cache.assert_called_once_with(
            "Artist",
            "Album",
            "2021",
            confidence=95,
        )

    @pytest.mark.asyncio
    async def test_returns_none_when_api_returns_no_year(self) -> None:
        """Should return None when API returns no year."""
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=(None, False, 0, {}))

        determinator = _create_year_determinator(external_api=external_api)
        tracks = [_create_track()]

        result = await determinator._fetch_from_api("Artist", "Album", tracks, None)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_fallback_rejects(self) -> None:
        """Should return None when fallback handler rejects the year."""
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("2021", False, 50, {"2021": 50}))

        fallback_handler = _create_mock_fallback_handler()
        fallback_handler.apply_year_fallback = AsyncMock(return_value=None)

        cache_service = _create_mock_cache_service()

        determinator = _create_year_determinator(
            cache_service=cache_service,
            external_api=external_api,
            fallback_handler=fallback_handler,
        )
        tracks = [_create_track()]

        result = await determinator._fetch_from_api("Artist", "Album", tracks, None)

        assert result is None
        # Should NOT cache rejected year
        cache_service.store_album_year_in_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_api_exception_gracefully(self) -> None:
        """Should return None when API raises exception."""
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(side_effect=RuntimeError("API error"))

        determinator = _create_year_determinator(external_api=external_api)
        tracks = [_create_track()]

        result = await determinator._fetch_from_api("Artist", "Album", tracks, None)

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_os_error_gracefully(self) -> None:
        """Should return None when API raises OSError."""
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(side_effect=OSError("Network error"))

        determinator = _create_year_determinator(external_api=external_api)
        tracks = [_create_track()]

        result = await determinator._fetch_from_api("Artist", "Album", tracks, None)

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_value_error_gracefully(self) -> None:
        """Should return None when API raises ValueError."""
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(side_effect=ValueError("Invalid data"))

        determinator = _create_year_determinator(external_api=external_api)
        tracks = [_create_track()]

        result = await determinator._fetch_from_api("Artist", "Album", tracks, None)

        assert result is None

    @pytest.mark.asyncio
    async def test_passes_dominant_year_to_api(self) -> None:
        """Should pass dominant_year to API for contamination detection."""
        external_api = _create_mock_external_api()

        determinator = _create_year_determinator(external_api=external_api)
        tracks = [_create_track(date_added="2020-01-01")]

        await determinator._fetch_from_api("Artist", "Album", tracks, "2019")

        external_api.get_album_year.assert_called_once()
        call_kwargs = external_api.get_album_year.call_args
        assert call_kwargs[1]["current_library_year"] == "2019"

    @pytest.mark.asyncio
    async def test_passes_fallback_params_correctly(self) -> None:
        """Should pass all parameters to fallback handler."""
        external_api = _create_mock_external_api()
        external_api.get_album_year = AsyncMock(return_value=("2021", True, 95, {"2021": 95, "2020": 80}))

        fallback_handler = _create_mock_fallback_handler()

        determinator = _create_year_determinator(
            external_api=external_api,
            fallback_handler=fallback_handler,
        )
        tracks = [_create_track()]

        await determinator._fetch_from_api("Artist", "Album", tracks, "2020")

        fallback_handler.apply_year_fallback.assert_called_once()
        call_kwargs = fallback_handler.apply_year_fallback.call_args[1]
        assert call_kwargs["proposed_year"] == "2021"
        assert call_kwargs["is_definitive"] is True
        assert call_kwargs["confidence_score"] == 95
        assert call_kwargs["artist"] == "Artist"
        assert call_kwargs["album"] == "Album"
        assert call_kwargs["year_scores"] == {"2021": 95, "2020": 80}
