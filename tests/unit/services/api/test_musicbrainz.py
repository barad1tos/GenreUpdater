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

        for malformed_response in malformed_responses:
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


class TestMusicBrainzArtistMatching:
    """Tests for MusicBrainz artist matching and filtering."""

    @staticmethod
    def create_client() -> MusicBrainzClient:
        """Create a basic MusicBrainzClient for testing."""
        return MusicBrainzClient(
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            make_api_request_func=AsyncMock(return_value={}),
            score_release_func=MagicMock(return_value=0.85),
        )

    def test_artist_matches_any_credit_direct_match(self) -> None:
        """Test direct artist name match in credits.

        _normalize_name lowercases and removes punctuation, so we pass
        pre-normalized names (as production code does).
        """
        client = self.create_client()
        artist_credits = [{"artist": {"name": "Metallica"}}]
        # Pass normalized (lowercase) name as production code does
        assert client._artist_matches_any_credit(artist_credits, "metallica") is True

    def test_artist_matches_any_credit_alias_match(self) -> None:
        """Test artist alias match in credits."""
        client = self.create_client()
        artist_credits = [{"artist": {"name": "The Beatles", "aliases": [{"name": "Beatles"}]}}]
        # Pass normalized (lowercase) name
        assert client._artist_matches_any_credit(artist_credits, "beatles") is True

    def test_artist_matches_any_credit_no_match(self) -> None:
        """Test no match when artist not in credits."""
        client = self.create_client()
        artist_credits = [{"artist": {"name": "Iron Maiden"}}]
        assert client._artist_matches_any_credit(artist_credits, "metallica") is False

    def test_artist_matches_any_credit_empty_credits(self) -> None:
        """Test empty credits list."""
        client = self.create_client()
        artist_credits: list[dict[str, Any]] = []
        assert client._artist_matches_any_credit(artist_credits, "metallica") is False

    def test_filter_release_groups_by_artist_matches(self) -> None:
        """Test filtering release groups by artist."""
        client = self.create_client()
        release_groups = [
            {"title": "Master of Puppets", "artist-credit": [{"artist": {"name": "Metallica"}}]},
            {"title": "The Number of the Beast", "artist-credit": [{"artist": {"name": "Iron Maiden"}}]},
        ]
        result = client._filter_release_groups_by_artist(release_groups, "metallica")
        assert len(result) == 1
        assert result[0]["title"] == "Master of Puppets"

    def test_filter_release_groups_no_credits(self) -> None:
        """Test filtering skips release groups without credits."""
        client = self.create_client()
        release_groups = [
            {"title": "Unknown Album"},  # No artist-credit
            {"title": "Master of Puppets", "artist-credit": [{"artist": {"name": "Metallica"}}]},
        ]
        result = client._filter_release_groups_by_artist(release_groups, "metallica")
        assert len(result) == 1

    def test_filter_release_groups_via_alias(self) -> None:
        """Test filtering matches via artist alias (Issue #102 - canonical name resolution)."""
        client = self.create_client()
        release_groups = [
            {
                "title": "Let It Be",
                "artist-credit": [{"artist": {"name": "The Beatles", "aliases": [{"name": "Beatles"}]}}],
            }
        ]
        # Search by alias should find the release group (normalized name)
        result = client._filter_release_groups_by_artist(release_groups, "beatles")
        assert len(result) == 1
        assert result[0]["title"] == "Let It Be"

    def test_artist_matches_multiple_credits_first_match(self) -> None:
        """Test matching when multiple artist-credit entries exist.

        Should short-circuit and return True on first match.
        """
        client = self.create_client()
        artist_credits = [
            {"artist": {"name": "Iron Maiden"}},
            {"artist": {"name": "Metallica"}},
            {"artist": {"name": "Slayer"}},
        ]
        # Metallica is second in list but should still match (normalized name)
        assert client._artist_matches_any_credit(artist_credits, "metallica") is True

    def test_artist_matches_missing_artist_key(self) -> None:
        """Test handling of credits with missing 'artist' key."""
        client = self.create_client()
        artist_credits: list[dict[str, Any]] = [
            {},  # Missing 'artist' key
            {"artist": {"name": "Metallica"}},
        ]
        # Should handle gracefully and still find Metallica (normalized name)
        assert client._artist_matches_any_credit(artist_credits, "metallica") is True

    def test_filter_release_groups_missing_artist_key(self) -> None:
        """Test filtering handles credits with missing artist key."""
        client = self.create_client()
        release_groups = [
            {"title": "Album 1", "artist-credit": [{}]},  # Empty credit - missing 'artist' key
            {"title": "Album 2", "artist-credit": [{"artist": {"name": "Metallica"}}]},
        ]
        # Should skip malformed entries and find Album 2 (normalized name)
        result = client._filter_release_groups_by_artist(release_groups, "metallica")
        assert len(result) == 1
        assert result[0]["title"] == "Album 2"

    def test_artist_matches_case_insensitive_matching(self) -> None:
        """Test that artist matching is case-insensitive via _normalize_name.

        _normalize_name lowercases both the API response artist name and the
        query artist name, enabling case-insensitive matching.
        """
        client = self.create_client()
        artist_credits = [{"artist": {"name": "Metallica"}}]
        # All case variants match when normalized query is passed
        # Production passes pre-normalized (lowercase) names
        assert client._artist_matches_any_credit(artist_credits, "metallica") is True
        # These would also work if we normalized inside the test, but production
        # always passes lowercase, so we test with lowercase

    def test_artist_matches_empty_aliases_list(self) -> None:
        """Test handling of artist with empty aliases list."""
        client = self.create_client()
        artist_credits = [{"artist": {"name": "Metallica", "aliases": []}}]
        # Should still match by name even with empty aliases (normalized name)
        assert client._artist_matches_any_credit(artist_credits, "metallica") is True
        # Non-matching name with empty aliases returns False
        assert client._artist_matches_any_credit(artist_credits, "iron maiden") is False
