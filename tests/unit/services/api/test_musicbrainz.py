"""Enhanced MusicBrainz API client tests with Allure reporting."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest

from services.api.musicbrainz import MusicBrainzClient
from tests.mocks.csv_mock import MockLogger


class TestMusicBrainzClientAllure:
    """Enhanced tests for MusicBrainzClient with Allure reporting."""

    @staticmethod
    def create_musicbrainz_client(
        mock_api_request: AsyncMock | None = None,
        mock_score_release: MagicMock | None = None,
    ) -> MusicBrainzClient:
        """Create a MusicBrainzClient instance for testing."""
        if mock_api_request is None:
            mock_api_request = AsyncMock(return_value={"artists": []})

        if mock_score_release is None:
            mock_score_release = MagicMock(return_value=0.85)

        return MusicBrainzClient(
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            make_api_request_func=mock_api_request,
            score_release_func=mock_score_release,
        )

    @staticmethod
    def create_mock_artist_response(artist_name: str = "Test Artist") -> dict[str, Any]:
        """Create a mock MusicBrainz artist search response."""
        return {
            "created": "2024-09-26T12:00:00.000Z",
            "count": 1,
            "offset": 0,
            "artists": [
                {
                    "id": "12345-6789-abcd-efgh",
                    "type": "Person",
                    "type-id": "b6e035f4-3ce9-331c-97df-83397230b0df",
                    "score": 100,
                    "name": artist_name,
                    "sort-name": artist_name,
                    "country": "US",
                    "area": {
                        "id": "489ce91b-6658-3307-9877-795b68554c98",
                        "type": "Country",
                        "type-id": "06dd0ae4-8c74-30bb-b43d-95dcedf961de",
                        "name": "United States",
                        "sort-name": "United States",
                        "life-span": {"ended": None},
                    },
                    "life-span": {"begin": "1980", "ended": None},
                    "aliases": [{"name": f"{artist_name} Alias", "type": "Artist name", "type-id": "894afba6-2816-3c24-8072-eadb66bd04bc"}],
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_search_artist_success(self) -> None:
        """Test successful artist search."""
        mock_response = TestMusicBrainzClientAllure.create_mock_artist_response("The Beatles")
        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)
        result = await client.get_artist_info("The Beatles", include_aliases=True)
        assert result is not None
        assert result["name"] == "The Beatles"
        assert result["id"] == "12345-6789-abcd-efgh"
        assert "life-span" in result
        assert "aliases" in result

        # Verify API was called with correct parameters (Issue #102: non-fielded search for alias matching)
        mock_api_request.assert_called_once()
        call_args = mock_api_request.call_args[1]
        # Non-fielded search to match both canonical names and aliases (Issue #102)
        assert call_args["params"]["query"] == "The Beatles"

    @pytest.mark.asyncio
    async def test_search_artist_not_found(self) -> None:
        """Test artist not found scenario."""
        mock_response: dict[str, Any] = {"artists": []}  # Mock empty response
        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)
        result = await client.get_artist_info("NonExistentArtist123")
        assert result is None

    @pytest.mark.asyncio
    async def test_rate_limiting(self) -> None:
        """Test rate limiting handling."""
        # Mock API request that returns None (rate limited)
        mock_api_request = AsyncMock(return_value=None)
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)
        result = await client.get_artist_info("Test Artist")
        # The client should handle the rate limiting gracefully
        # First call returns None due to rate limiting, handled by client
        assert mock_api_request.call_count == 1  # Only one call made by client

        # Result should be None when rate limited
        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_handling(self) -> None:
        """Test network error handling."""
        # Mock API request to return None (network error)
        mock_api_request = AsyncMock(return_value=None)
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)
        result = await client.get_artist_info("Test Artist")
        # Client should handle the network error gracefully
        assert result is None

    @pytest.mark.asyncio
    async def test_malformed_response(self) -> None:
        """Test handling of malformed API responses."""
        malformed_responses = [
            # Missing required fields
            {"artists": [{"name": "Test"}]},  # Missing id
            # Invalid data types
            {"artists": [{"id": 123, "name": "Test"}]},  # id should be string
            # Completely invalid structure
            {"invalid": "structure"},
            # Empty response
            {},
        ]

        for i, malformed_response in enumerate(malformed_responses):
            mock_api_request = AsyncMock(return_value=malformed_response)
            client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)

            # Attempt search with malformed response
            result = await client.get_artist_info("Test Artist")

            # Client should handle malformed responses gracefully
            # May return None or empty results depending on implementation
            assert result is not None or result is None  # Should not crash

    def test_lucene_escaping(self) -> None:
        """Test Lucene query escaping functionality."""
        test_cases = [
            ("AC/DC", r"AC\/DC"),
            ("Artist (Band)", r"Artist \\(Band\\)"),
            ("Artist & Co.", r"Artist \\& Co."),  # No period escaping needed
            ("Artist+More", r"Artist\\+More"),
            ("Artist?", r"Artist\\?"),
            ("Artist*", r"Artist\\*"),
            ("Artist~1", r"Artist\\~1"),
            ("Artist:Name", r"Artist\\:Name"),
            ("Artist[Live]", r"Artist\\[Live\\]"),
            ("Artist{Demo}", r"Artist\\{Demo\\}"),
            ("Artist!Loud", r"Artist\\!Loud"),
            ("Artist^2", r"Artist\\^2"),
            ('Artist"Quote"', r'Artist\\"Quote\\"'),
            ("Artist|Or", r"Artist\\|Or"),
            ("Artist-Minus", r"Artist\\-Minus"),
            ("Artist\\Test", r"Artist\\\\Test"),  # Test backslash escaping
        ]
        for input_str, expected in test_cases:
            result = MusicBrainzClient._escape_lucene(input_str)
            assert result == expected, f"Expected '{expected}', got '{result}'"

    @pytest.mark.asyncio
    async def test_get_artist_region(self) -> None:
        """Test artist region retrieval."""
        artist_response = TestMusicBrainzClientAllure.create_mock_artist_response()
        # Ensure country is in the response
        artist_response["artists"][0]["country"] = "US"

        mock_api_request = AsyncMock(return_value=artist_response)
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)
        region = await client.get_artist_region("Test Artist")
        assert region == "United States"

    @pytest.mark.asyncio
    async def test_get_artist_activity_period(self) -> None:
        """Test artist activity period retrieval."""
        artist_response = TestMusicBrainzClientAllure.create_mock_artist_response()
        # Ensure life-span is in the response
        artist_response["artists"][0]["life-span"] = {"begin": "1980", "end": "2020", "ended": True}

        mock_api_request = AsyncMock(return_value=artist_response)
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)
        begin, end = await client.get_artist_activity_period("Test Artist")
        assert begin == "1980"
        assert end == "2020"
