"""Integration tests for alternative search fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.api.year_search_coordinator import YearSearchCoordinator


class TestAlternativeSearchFallback:
    """Tests for alternative search fallback mechanism."""

    @pytest.fixture
    def mock_coordinator(self) -> YearSearchCoordinator:
        """Create coordinator with mocked dependencies."""
        mock_logger = MagicMock()
        config = {
            "year_retrieval": {
                "preferred_api": "musicbrainz",
            },
            "album_type_detection": {
                "soundtrack_patterns": ["soundtrack", "OST"],
                "various_artists_names": ["Various Artists"],
            },
        }

        return YearSearchCoordinator(
            console_logger=mock_logger,
            error_logger=mock_logger,
            config=config,
            preferred_api="musicbrainz",
            musicbrainz_client=AsyncMock(),
            discogs_client=AsyncMock(),
            applemusic_client=AsyncMock(),
            release_scorer=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_fallback_not_triggered_when_results_exist(self, mock_coordinator: YearSearchCoordinator) -> None:
        """No fallback when standard search returns results."""
        mock_coordinator._execute_standard_api_search = AsyncMock(return_value=[{"year": "2020", "score": 90}])

        results = await mock_coordinator.fetch_all_api_results(
            artist_norm="ghost",
            album_norm="prequelle",
            artist_region=None,
            log_artist="Ghost",
            log_album="Prequelle",
        )

        assert len(results) == 1
        mock_coordinator._execute_standard_api_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_triggered_for_soundtrack(self, mock_coordinator: YearSearchCoordinator) -> None:
        """Fallback triggered for soundtrack albums."""
        call_count = 0

        async def mock_search(*args: Any, **kwargs: Any) -> list[dict[str, str | int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            return [{"year": "2010", "score": 85}]

        mock_coordinator._execute_standard_api_search = AsyncMock(side_effect=mock_search)

        results = await mock_coordinator.fetch_all_api_results(
            artist_norm="hans zimmer",
            album_norm="inception original soundtrack",
            artist_region=None,
            log_artist="Hans Zimmer",
            log_album="Inception (Original Soundtrack)",
        )

        assert len(results) == 1
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_fallback_not_triggered_for_normal_album(self, mock_coordinator: YearSearchCoordinator) -> None:
        """Fallback not triggered for normal albums without special patterns."""
        mock_coordinator._execute_standard_api_search = AsyncMock(return_value=[])

        results = await mock_coordinator.fetch_all_api_results(
            artist_norm="metallica",
            album_norm="master of puppets",
            artist_region=None,
            log_artist="Metallica",
            log_album="Master of Puppets",
        )

        assert len(results) == 0
        # Should only be called once (no alternative strategy detected)
        mock_coordinator._execute_standard_api_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_triggered_for_various_artists(self, mock_coordinator: YearSearchCoordinator) -> None:
        """Fallback triggered for Various Artists albums."""
        call_count = 0

        async def mock_search(*args: Any, **kwargs: Any) -> list[dict[str, str | int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            return [{"year": "2015", "score": 80}]

        mock_coordinator._execute_standard_api_search = AsyncMock(side_effect=mock_search)

        results = await mock_coordinator.fetch_all_api_results(
            artist_norm="various artists",
            album_norm="now thats what i call music 50",
            artist_region=None,
            log_artist="Various Artists",
            log_album="Now That's What I Call Music 50",
        )

        assert len(results) == 1
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_fallback_triggered_for_unusual_brackets(self, mock_coordinator: YearSearchCoordinator) -> None:
        """Fallback triggered for albums with unusual bracket content."""
        call_count = 0

        async def mock_search(*args: Any, **kwargs: Any) -> list[dict[str, str | int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            return [{"year": "2018", "score": 90}]

        mock_coordinator._execute_standard_api_search = AsyncMock(side_effect=mock_search)

        results = await mock_coordinator.fetch_all_api_results(
            artist_norm="ghost",
            album_norm="prequelle [message from the clergy]",
            artist_region=None,
            log_artist="Ghost",
            log_album="Prequelle [MESSAGE FROM THE CLERGY]",
        )

        assert len(results) == 1
        assert call_count == 2
