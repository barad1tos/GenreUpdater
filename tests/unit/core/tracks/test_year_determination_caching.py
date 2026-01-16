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

    if mock_fallback_handler is None:
        mock_fallback_handler = AsyncMock(spec=YearFallbackHandler)
        # Default: fallback handler returns the proposed year unchanged
        mock_fallback_handler.apply_year_fallback = AsyncMock(side_effect=lambda proposed_year, **_: proposed_year)

    config: dict[str, Any] = {
        "year_retrieval": {
            "processing": {
                "skip_prerelease": True,
                "future_year_threshold": 1,
                "prerelease_recheck_days": 30,
            }
        }
    }

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
        # Arrange
        mock_cache_service = MagicMock()
        mock_cache_service.store_album_year_in_cache = AsyncMock()

        mock_external_api = AsyncMock()
        # Return year with LOW confidence (19 < 50)
        mock_external_api.get_album_year = AsyncMock(return_value=("1987", False, 19, {"1987": 19}))

        determinator = create_year_determinator(mock_cache_service, mock_external_api)
        tracks = [create_test_track(artist="Robbie Williams", album="BRITPOP")]

        # Act
        result = await determinator._fetch_from_api(
            artist="Robbie Williams",
            album="BRITPOP",
            album_tracks=tracks,
            dominant_year=None,
        )

        # Assert: Result is returned but NOT cached
        assert result == "1987"  # API result is still returned
        mock_cache_service.store_album_year_in_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_confidence_year_is_cached(self) -> None:
        """Year with confidence >= 50 should be cached."""
        # Arrange
        mock_cache_service = MagicMock()
        mock_cache_service.store_album_year_in_cache = AsyncMock()

        mock_external_api = AsyncMock()
        # Return year with HIGH confidence (77 >= 50)
        mock_external_api.get_album_year = AsyncMock(return_value=("2019", True, 77, {"2019": 77}))

        determinator = create_year_determinator(mock_cache_service, mock_external_api)
        tracks = [create_test_track(artist="Robbie Williams", album="The Christmas Present")]

        # Act
        result = await determinator._fetch_from_api(
            artist="Robbie Williams",
            album="The Christmas Present",
            album_tracks=tracks,
            dominant_year=None,
        )

        # Assert: Result is returned AND cached
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
        # Arrange
        mock_cache_service = MagicMock()
        mock_cache_service.store_album_year_in_cache = AsyncMock()

        mock_external_api = AsyncMock()
        # Return year with BOUNDARY confidence (50 == 50)
        mock_external_api.get_album_year = AsyncMock(return_value=("2020", True, 50, {"2020": 50}))

        determinator = create_year_determinator(mock_cache_service, mock_external_api)
        tracks = [create_test_track()]

        # Act
        result = await determinator._fetch_from_api(
            artist="Test Artist",
            album="Test Album",
            album_tracks=tracks,
            dominant_year=None,
        )

        # Assert: Result is cached at boundary confidence
        assert result == "2020"
        mock_cache_service.store_album_year_in_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_boundary_confidence_49_not_cached(self) -> None:
        """Year with confidence 49 should NOT be cached (boundary test)."""
        # Arrange
        mock_cache_service = MagicMock()
        mock_cache_service.store_album_year_in_cache = AsyncMock()

        mock_external_api = AsyncMock()
        # Return year with confidence just below threshold (49 < 50)
        mock_external_api.get_album_year = AsyncMock(return_value=("2020", True, 49, {"2020": 49}))

        determinator = create_year_determinator(mock_cache_service, mock_external_api)
        tracks = [create_test_track()]

        # Act
        result = await determinator._fetch_from_api(
            artist="Test Artist",
            album="Test Album",
            album_tracks=tracks,
            dominant_year=None,
        )

        # Assert: Result returned but NOT cached (49 < 50)
        assert result == "2020"
        mock_cache_service.store_album_year_in_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_result_not_cached_regardless_of_confidence(self) -> None:
        """When fallback returns None, nothing should be cached."""
        # Arrange
        mock_cache_service = MagicMock()
        mock_cache_service.store_album_year_in_cache = AsyncMock()

        mock_external_api = AsyncMock()
        mock_external_api.get_album_year = AsyncMock(return_value=("2020", True, 85, {"2020": 85}))

        # Fallback handler rejects the year
        mock_fallback = AsyncMock()
        mock_fallback.apply_year_fallback = AsyncMock(return_value=None)

        determinator = create_year_determinator(mock_cache_service, mock_external_api, mock_fallback)
        tracks = [create_test_track()]

        # Act
        result = await determinator._fetch_from_api(
            artist="Test Artist",
            album="Test Album",
            album_tracks=tracks,
            dominant_year=None,
        )

        # Assert: No result, nothing cached
        assert result is None
        mock_cache_service.store_album_year_in_cache.assert_not_called()
