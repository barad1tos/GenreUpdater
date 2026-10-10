"""Tests for year determination caching behavior.

Tests the MIN_CONFIDENCE_TO_CACHE threshold that prevents low-confidence
API results from being cached, avoiding bugs like BRITPOP→1987 (conf=19).
"""

from __future__ import annotations

import logging
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.track_models import TrackDict
from core.tracks.year_consistency import YearConsistencyChecker
from core.tracks.year_determination import YearDeterminator
from core.tracks.year_fallback import YearFallbackHandler
from tests.factories import create_test_app_config
from tests.mocks.protocol_mocks import MockPendingVerificationService


def create_test_track(
    artist: str = "Test Artist",
    album: str = "Test Album",
    year: str | None = None,
    release_year: str | None = None,
) -> TrackDict:
    """Create a test track with minimal required fields."""
    return TrackDict(
        id="test_id_1",
        name="Test Track",
        artist=artist,
        album=album,
        genre="Rock",
        year=year,
        date_added="2024-01-15 10:00:00",
        release_year=release_year,
    )


def create_cache_service() -> MagicMock:
    """Create a cache service that misses on read and records writes."""
    cache_service = MagicMock()
    cache_service.get_album_year_entry_from_cache = AsyncMock(return_value=None)
    cache_service.store_album_year_in_cache = AsyncMock()
    return cache_service


def create_external_api(answer: tuple[str | None, bool, int, dict[str, int]]) -> AsyncMock:
    """Create an external API whose single album lookup returns `answer`."""
    external_api = AsyncMock()
    external_api.get_album_year = AsyncMock(return_value=answer)
    return external_api


def create_year_determinator(
    mock_cache_service: MagicMock,
    mock_external_api: AsyncMock,
    mock_fallback_handler: AsyncMock | None = None,
) -> YearDeterminator:
    """Create a YearDeterminator with mocked dependencies."""
    console_logger = logging.getLogger("test.console")
    error_logger = logging.getLogger("test.error")

    mock_pending = MockPendingVerificationService()
    mock_consistency = MagicMock(spec=YearConsistencyChecker)
    mock_consistency.get_consensus_release_year = MagicMock(return_value=None)
    mock_consistency.get_most_common_year = MagicMock(return_value=None)
    mock_consistency.get_majority_year = MagicMock(return_value=None)

    if mock_fallback_handler is None:
        mock_fallback_handler = AsyncMock(spec=YearFallbackHandler)
        # Default: fallback handler returns the proposed year unchanged
        mock_fallback_handler.apply_year_fallback = AsyncMock(side_effect=lambda proposed_year, **_: proposed_year)

    config = create_test_app_config()

    return YearDeterminator(
        cache_service=cast(Any, mock_cache_service),
        external_api=cast(Any, mock_external_api),
        pending_verification=cast(Any, mock_pending),
        consistency_checker=cast(Any, mock_consistency),
        fallback_handler=cast(Any, mock_fallback_handler),
        console_logger=console_logger,
        error_logger=error_logger,
        config=config,
    )


class TestConfidenceThresholdCaching:
    """Tests for MIN_CONFIDENCE_TO_CACHE threshold behavior."""

    @pytest.mark.asyncio
    async def test_low_confidence_year_not_cached(self) -> None:
        """Year with confidence < 50 should NOT be cached.

        Regression test for BRITPOP bug where confidence=19 result was cached.
        """
        mock_cache_service = create_cache_service()
        mock_external_api = create_external_api(("1987", False, 19, {"1987": 19}))
        determinator = create_year_determinator(mock_cache_service, mock_external_api)
        tracks = [create_test_track(artist="Robbie Williams", album="BRITPOP")]

        result = await determinator.determine_album_year("Robbie Williams", "BRITPOP", tracks)

        # The API result is still applied, only its caching is refused
        assert result == "1987"
        mock_cache_service.store_album_year_in_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_confidence_year_is_cached(self) -> None:
        """Year with confidence >= 50 should be cached."""
        mock_cache_service = create_cache_service()
        mock_external_api = create_external_api(("2019", True, 77, {"2019": 77}))
        determinator = create_year_determinator(mock_cache_service, mock_external_api)
        tracks = [create_test_track(artist="Robbie Williams", album="The Christmas Present")]

        result = await determinator.determine_album_year("Robbie Williams", "The Christmas Present", tracks)

        assert result == "2019"
        mock_cache_service.store_album_year_in_cache.assert_called_once_with(
            "Robbie Williams",
            "The Christmas Present",
            "2019",
            confidence=77,
        )

    @pytest.mark.asyncio
    async def test_boundary_confidence_50_is_cached(self) -> None:
        """Year with confidence exactly 50 should be cached (boundary test)."""
        mock_cache_service = create_cache_service()
        mock_external_api = create_external_api(("2020", True, 50, {"2020": 50}))
        determinator = create_year_determinator(mock_cache_service, mock_external_api)
        tracks = [create_test_track()]

        result = await determinator.determine_album_year("Test Artist", "Test Album", tracks)

        assert result == "2020"
        mock_cache_service.store_album_year_in_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_boundary_confidence_49_not_cached(self) -> None:
        """Year with confidence 49 should NOT be cached (boundary test)."""
        mock_cache_service = create_cache_service()
        mock_external_api = create_external_api(("2020", True, 49, {"2020": 49}))
        determinator = create_year_determinator(mock_cache_service, mock_external_api)
        tracks = [create_test_track()]

        result = await determinator.determine_album_year("Test Artist", "Test Album", tracks)

        assert result == "2020"
        mock_cache_service.store_album_year_in_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_result_not_cached_regardless_of_confidence(self) -> None:
        """When fallback returns None, nothing should be cached."""
        mock_cache_service = create_cache_service()
        mock_external_api = create_external_api(("2020", True, 85, {"2020": 85}))
        mock_fallback = AsyncMock()
        mock_fallback.apply_year_fallback = AsyncMock(return_value=None)
        determinator = create_year_determinator(mock_cache_service, mock_external_api, mock_fallback)
        tracks = [create_test_track()]

        result = await determinator.determine_album_year("Test Artist", "Test Album", tracks)

        assert result is None
        mock_cache_service.store_album_year_in_cache.assert_not_called()
