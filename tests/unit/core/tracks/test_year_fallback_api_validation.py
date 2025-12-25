"""Tests for Issue #93: FALLBACK validation against API results."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.tracks.year_fallback import YearFallbackHandler


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test")


@pytest.fixture
def pending_verification() -> MagicMock:
    """Create a mock pending verification service."""
    mock = MagicMock()
    mock.mark_for_verification = AsyncMock()
    return mock


@pytest.fixture
def api_orchestrator() -> MagicMock:
    """Create a mock API orchestrator."""
    mock = MagicMock()
    mock.get_artist_start_year = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def fallback_handler(
    logger: logging.Logger,
    pending_verification: MagicMock,
    api_orchestrator: MagicMock,
) -> YearFallbackHandler:
    """Create a YearFallbackHandler for testing."""
    return YearFallbackHandler(
        console_logger=logger,
        pending_verification=pending_verification,
        fallback_enabled=True,
        absurd_year_threshold=1970,
        year_difference_threshold=5,
        min_confidence_for_new_year=30,
        api_orchestrator=api_orchestrator,
    )


class TestCheckExistingYearInApiResults:
    """Tests for _check_existing_year_in_api_results validation."""

    def test_returns_none_when_no_year_scores(self, fallback_handler: YearFallbackHandler) -> None:
        """Test returns None when year_scores is None."""
        result = fallback_handler._check_existing_year_in_api_results(
            existing_year="1998",
            year_scores=None,
            artist="Test",
            album="Album",
        )
        assert result is None

    def test_returns_none_when_empty_year_scores(self, fallback_handler: YearFallbackHandler) -> None:
        """Test returns None when year_scores is empty."""
        result = fallback_handler._check_existing_year_in_api_results(
            existing_year="1998",
            year_scores={},
            artist="Test",
            album="Album",
        )
        assert result is None

    def test_returns_true_when_existing_year_found(self, fallback_handler: YearFallbackHandler) -> None:
        """Test returns True when existing year is in API results."""
        result = fallback_handler._check_existing_year_in_api_results(
            existing_year="2018",
            year_scores={"2018": 85, "2020": 70},
            artist="Test",
            album="Album",
        )
        assert result is True

    def test_returns_false_when_existing_year_not_found(self, fallback_handler: YearFallbackHandler) -> None:
        """Test returns False when existing year NOT in API results."""
        result = fallback_handler._check_existing_year_in_api_results(
            existing_year="1998",
            year_scores={"2018": 85, "2019": 70},
            artist="Abney Park",
            album="Scallywag",
        )
        assert result is False


class TestIssue93FalsePositives:
    """Regression tests for Issue #93 false positives."""

    @pytest.mark.asyncio
    async def test_abney_park_scallywag_trusts_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Abney Park - Scallywag: existing=1998, API=2018, should return 2018."""
        # Mock artist period (formed 1997)
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1997)

        tracks: list[dict[str, Any]] = [{"artist": "Abney Park", "album": "Scallywag", "year": "1998"}]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2018",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=60,
            artist="Abney Park",
            album="Scallywag",
            year_scores={"2018": 85},  # 1998 NOT in API results
        )

        assert result == "2018"

    @pytest.mark.asyncio
    async def test_korn_korn_trusts_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Korn - Korn: existing=2003, API=1994, should return 1994."""
        # Mock artist period (formed 1993)
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1993)

        tracks: list[dict[str, Any]] = [{"artist": "Korn", "album": "Korn", "year": "2003"}]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="1994",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=60,
            artist="Korn",
            album="Korn",
            year_scores={"1994": 90},  # 2003 NOT in API results
        )

        assert result == "1994"

    @pytest.mark.asyncio
    async def test_abney_park_taxidermy_trusts_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Abney Park - Taxidermy: existing=1998, API=2019, should return 2019."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1997)

        tracks: list[dict[str, Any]] = [{"artist": "Abney Park", "album": "Taxidermy", "year": "1998"}]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2019",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=55,
            artist="Abney Park",
            album="Taxidermy",
            year_scores={"2019": 80},  # 1998 NOT in API results
        )

        assert result == "2019"

    @pytest.mark.asyncio
    async def test_korn_life_is_peachy_trusts_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Korn - Life is Peachy: existing=2003, API=1996, should return 1996."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1993)

        tracks: list[dict[str, Any]] = [{"artist": "Korn", "album": "Life is Peachy", "year": "2003"}]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="1996",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=65,
            artist="Korn",
            album="Life is Peachy",
            year_scores={"1996": 88},  # 2003 NOT in API results
        )

        assert result == "1996"


class TestPreservesValidExistingYear:
    """Tests for correct behavior when existing year IS in API results."""

    @pytest.mark.asyncio
    async def test_preserves_year_when_in_api_results(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Should preserve existing year if it appears in API results with dramatic change."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1990)

        tracks: list[dict[str, Any]] = [{"artist": "Test", "album": "Album", "year": "2000"}]

        # Both years in API results - existing has lower score but is still valid
        # The method should NOT automatically reject 2000 just because 2020 has higher score
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Test",
            album="Album",
            year_scores={"2000": 70, "2020": 85},  # 2000 IS in API results
        )

        # With dramatic change + low confidence + both plausible + existing in API results
        # → should preserve existing (2000) per existing fallback logic
        assert result == "2000"

    @pytest.mark.asyncio
    async def test_applies_year_when_no_dramatic_change(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Should apply new year when change is not dramatic."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1990)

        tracks: list[dict[str, Any]] = [{"artist": "Test", "album": "Album", "year": "2018"}]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Test",
            album="Album",
            year_scores={"2020": 85},
        )

        # No dramatic change (2 years difference <= 5 year threshold) → apply proposed
        assert result == "2020"


class TestBackwardCompatibility:
    """Tests for backward compatibility when year_scores is not provided."""

    @pytest.mark.asyncio
    async def test_works_without_year_scores(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Should work correctly when year_scores is not provided (backward compatibility)."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1990)

        tracks: list[dict[str, Any]] = [{"artist": "Test", "album": "Album", "year": "2000"}]

        # No year_scores provided - should fall through to existing logic
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Test",
            album="Album",
            # year_scores not provided - defaults to None
        )

        # Dramatic change + low confidence + both plausible + no year_scores
        # → existing logic preserves existing year
        assert result == "2000"
