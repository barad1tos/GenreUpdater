"""Enhanced Discogs API client tests with Allure reporting."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import pytest

from services.api.discogs import DiscogsClient
from tests.factories import create_test_app_config
from tests.mocks.csv_mock import MockLogger


class TestDiscogsClientAllure:
    """Enhanced tests for DiscogsClient with Allure reporting."""

    @staticmethod
    def create_discogs_client(
        mock_api_request: AsyncMock | None = None,
        mock_score_release: MagicMock | None = None,
        mock_cache_service: MagicMock | None = None,
    ) -> DiscogsClient:
        """Create a DiscogsClient instance for testing."""
        if mock_api_request is None:
            mock_api_request = AsyncMock(return_value={"results": []})

        if mock_score_release is None:
            mock_score_release = MagicMock(return_value=0.85)

        if mock_cache_service is None:
            mock_cache_service = MagicMock()
            mock_cache_service.get_cached_data = AsyncMock(return_value=None)
            mock_cache_service.cache_data = AsyncMock()
            mock_cache_service.get_async = AsyncMock(return_value=None)
            mock_cache_service.set_async = AsyncMock()

        # Create a mock analytics that bypasses the decorator
        class MockAnalytics:
            """Mock analytics service that bypasses tracking."""

            @staticmethod
            async def execute_async_wrapped_call(func: Any, _event_type: str, *args: Any, **kwargs: Any) -> Any:
                """Execute async function without tracking."""
                return await func(*args, **kwargs)

        mock_analytics = MockAnalytics()

        test_api_token = "test_token"  # noqa: S105
        app_config = create_test_app_config()
        return DiscogsClient(
            token=test_api_token,
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            analytics=mock_analytics,  # type: ignore[arg-type]
            make_api_request_func=mock_api_request,
            score_release_func=mock_score_release,
            cache_service=mock_cache_service,
            scoring_config=app_config.year_retrieval,
            config=app_config,
        )

    @staticmethod
    def create_mock_discogs_response(artist_name: str = "Test Artist", album_name: str = "Test Album") -> dict[str, Any]:
        """Create a mock Discogs search response."""
        return {
            "pagination": {"pages": 1, "page": 1, "per_page": 10, "items": 1, "urls": {}},
            "results": [
                {
                    "id": 123456,
                    "type": "release",
                    "title": f"{artist_name} - {album_name}",
                    "year": 2020,
                    "released": "2020-01-15",
                    "country": "US",
                    "genre": ["Rock", "Alternative Rock"],
                    "style": ["Indie Rock"],
                    "label": ["Test Records"],
                    "formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"]}],
                    "thumb": "https://i.discogs.com/thumb.jpg",
                    "cover_image": "https://i.discogs.com/cover.jpg",
                    "resource_url": "https://api.discogs.com/releases/123456",
                    "uri": "/Test-Artist-Test-Album/release/123456",
                    "master_id": 654321,
                    "master_url": "https://api.discogs.com/masters/654321",
                }
            ],
        }

    @staticmethod
    def create_mock_release_details() -> dict[str, Any]:
        """Create a mock Discogs release details response."""
        return {
            "id": 123456,
            "title": "Test Artist - Test Album",
            "year": 2020,
            "released": "2020-01-15",
            "released_formatted": "15 Jan 2020",
            "country": "US",
            "genres": ["Rock", "Alternative Rock"],
            "styles": ["Indie Rock"],
            "labels": [
                {
                    "name": "Test Records",
                    "catno": "TR001",
                    "entity_type": "1",
                    "entity_type_name": "Label",
                    "id": 78910,
                    "resource_url": "https://api.discogs.com/labels/78910",
                }
            ],
            "formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"], "text": ""}],
            "artists": [
                {
                    "name": "Test Artist",
                    "anv": "",
                    "join": "",
                    "role": "",
                    "tracks": "",
                    "id": 112233,
                    "resource_url": "https://api.discogs.com/artists/112233",
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_search_release_success(self) -> None:
        """Test successful release search."""
        mock_response = TestDiscogsClientAllure.create_mock_discogs_response("The Beatles", "Abbey Road")
        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        result = await client.get_scored_releases("The Beatles", "Abbey Road", "US")
        assert result is not None
        assert len(result) > 0

        # Verify first release
        release = result[0]
        assert release["title"] == "Abbey Road"  # Extracted album part only
        assert release["year"] == "2020"
        assert release["source"] == "discogs"
        assert "score" in release

        # Verify API was called
        mock_api_request.assert_called()

    @pytest.mark.asyncio
    async def test_search_release_not_found(self) -> None:
        """Test release not found scenario."""
        mock_response = {"results": [], "pagination": {"pages": 0, "items": 0}}
        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        result = await client.get_scored_releases("NonExistentArtist123", "NonExistentAlbum456", None)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_release_year(self) -> None:
        """Test release year retrieval."""
        mock_response = TestDiscogsClientAllure.create_mock_discogs_response()
        mock_response["results"][0]["year"] = 1969  # Set specific year
        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        year = await client.get_year_from_discogs("Test Artist", "Test Album")
        assert year == "1969"

    @pytest.mark.asyncio
    async def test_authentication_handling(self) -> None:
        """Test authentication handling with fallback search strategies.

        The client now uses multiple search strategies:
        1. Primary: fielded search (artist= + release_title=)
        2. Fallback 1: generic query (q=artist album)
        3. Fallback 2: album-only search (release_title=)

        When results are empty, all strategies are tried.
        """
        mock_api_request = AsyncMock(return_value={"results": []})
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        await client.get_scored_releases("Test Artist", "Test Album", None)

        # Verify all 3 search strategies were attempted (primary + 2 fallbacks)
        expected_call_count = 3
        assert mock_api_request.call_count == expected_call_count

        # Check that proper Discogs API URL was used for all calls
        for call in mock_api_request.call_args_list:
            url = call[0][1]  # URL argument
            host = urlparse(url).hostname
            assert host is not None
            assert host == "api.discogs.com" or host.endswith(".discogs.com")

    @pytest.mark.asyncio
    async def test_api_quota_exceeded(self) -> None:
        """Test API quota exceeded handling."""
        # Mock API request that returns None (quota exceeded)
        mock_api_request = AsyncMock(return_value=None)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        result = await client.get_scored_releases("Test Artist", "Test Album", None)
        # Client should handle quota exceeded gracefully
        assert result == []

    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """Test timeout handling."""
        # Mock API request that returns None (timeout)
        mock_api_request = AsyncMock(return_value=None)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        result = await client.get_scored_releases("Test Artist", "Test Album", None)
        # Client should handle timeouts gracefully
        assert result == []

    @pytest.mark.asyncio
    async def test_caching_behavior(self) -> None:
        """Test caching behavior."""
        mock_response = TestDiscogsClientAllure.create_mock_discogs_response()

        # Setup cache service mock
        mock_cache_service = MagicMock()
        cached_releases = [
            {
                "title": "Cached Album",  # Cached data should contain just album name
                "year": "2019",
                "score": 0.95,
                "source": "discogs",
            }
        ]
        mock_cache_service.get_cached_data = AsyncMock(return_value=cached_releases)
        mock_cache_service.cache_data = AsyncMock()
        mock_cache_service.get_async = AsyncMock(return_value=cached_releases)
        mock_cache_service.set_async = AsyncMock()

        # API should not be called if cache hit
        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request, mock_cache_service=mock_cache_service)
        result = await client.get_scored_releases("Test Artist", "Test Album", None)
        # Should get cached results
        assert result == cached_releases

        # Verify cache was checked
        mock_cache_service.get_async.assert_called()

    @pytest.mark.asyncio
    async def test_primary_search_success_no_fallback(self) -> None:
        """Test that fallback is not called when primary search succeeds."""
        # Create response without master_id to avoid additional API calls
        mock_response = TestDiscogsClientAllure.create_mock_discogs_response()
        mock_response["results"][0]["master_id"] = None  # No master release lookup
        mock_response["results"][0]["master_url"] = None

        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        result = await client.get_scored_releases("Test Artist", "Test Album", None)

        # Primary search should succeed, no fallbacks needed
        assert len(result) > 0

        # Verify first call was the fielded search (primary)
        first_call = mock_api_request.call_args_list[0]
        params = first_call[1]["params"]
        assert "artist" in params
        assert "release_title" in params

    @pytest.mark.asyncio
    async def test_fallback_to_generic_search(self) -> None:
        """Test fallback to generic search when primary fails."""
        # Create response without master_id to simplify test
        mock_response = TestDiscogsClientAllure.create_mock_discogs_response()
        mock_response["results"][0]["master_id"] = None
        mock_response["results"][0]["master_url"] = None

        # First call (primary) returns empty, second call (fallback) returns results
        mock_api_request = AsyncMock(side_effect=[{"results": []}, mock_response])
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        result = await client.get_scored_releases("Test Artist", "Test Album", None)

        # Should get results from fallback
        assert len(result) > 0

        # Verify first call was primary search, second was generic
        first_call = mock_api_request.call_args_list[0]
        assert "artist" in first_call[1]["params"]

        second_call = mock_api_request.call_args_list[1]
        params = second_call[1]["params"]
        assert "q" in params
        assert params["q"] == "Test Artist Test Album"

    @pytest.mark.asyncio
    async def test_fallback_to_album_only_search(self) -> None:
        """Test fallback to album-only search when both primary and generic fail."""
        # Create response without master_id to simplify test
        mock_response = TestDiscogsClientAllure.create_mock_discogs_response()
        mock_response["results"][0]["master_id"] = None
        mock_response["results"][0]["master_url"] = None

        # First two calls return empty, third returns results
        mock_api_request = AsyncMock(side_effect=[{"results": []}, {"results": []}, mock_response])
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        result = await client.get_scored_releases("Test Artist", "Test Album", None)

        # Should get results from album-only fallback
        assert len(result) > 0

        # Verify search strategy progression
        first_call = mock_api_request.call_args_list[0]
        assert "artist" in first_call[1]["params"]  # Primary

        second_call = mock_api_request.call_args_list[1]
        assert "q" in second_call[1]["params"]  # Fallback 1 (generic)

        third_call = mock_api_request.call_args_list[2]
        params = third_call[1]["params"]
        assert "release_title" in params
        assert "artist" not in params
        assert "q" not in params  # Fallback 2 (album-only)

    def test_artist_matching_the_prefix(self) -> None:
        """Test artist matching handles 'The' prefix variations."""
        from services.api.discogs import DiscogsClient

        # Test "The Beatles" vs "Beatles, The" normalization
        assert DiscogsClient._normalize_artist_for_matching("The Beatles") == "the beatles"
        assert DiscogsClient._normalize_artist_for_matching("Beatles, The") == "the beatles"

        # Test numbered suffix removal
        assert DiscogsClient._normalize_artist_for_matching("Artist (2)") == "artist"
        assert DiscogsClient._normalize_artist_for_matching("The Band (3)") == "the band"

    def test_artist_matching_empty_and_whitespace(self) -> None:
        """Test artist matching handles edge cases."""
        from services.api.discogs import DiscogsClient

        assert DiscogsClient._normalize_artist_for_matching("") == ""
        assert DiscogsClient._normalize_artist_for_matching("  Artist  ") == "artist"
        assert DiscogsClient._normalize_artist_for_matching("UPPERCASE") == "uppercase"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("artist", "album"),
        [
            ("Diary of Dreams", "The Anatomy of Silence"),
            ("Fallujah", "Dreamless"),
            ("Fear Factory", "Aggression Continuum"),
        ],
    )
    async def test_search_strategies_for_known_failures(self, artist: str, album: str) -> None:
        """Test that search strategies are applied for previously failing cases from issue #107."""
        mock_api_request = AsyncMock(return_value={"results": []})
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        await client.get_scored_releases(artist, album, None)

        # All 3 strategies should be tried when no results found
        assert mock_api_request.call_count == 3

        # Verify search parameters for each strategy
        calls = mock_api_request.call_args_list

        # Call 1: Primary fielded search
        assert "artist" in calls[0][1]["params"]
        assert "release_title" in calls[0][1]["params"]

        # Call 2: Generic query
        assert "q" in calls[1][1]["params"]

        # Call 3: Album-only
        assert "release_title" in calls[2][1]["params"]
        assert "artist" not in calls[2][1]["params"]
