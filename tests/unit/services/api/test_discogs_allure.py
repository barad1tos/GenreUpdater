"""Enhanced Discogs API client tests with Allure reporting."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import allure
import pytest
from services.api.discogs import DiscogsClient

from tests.mocks.csv_mock import MockLogger


@allure.epic("Music Genre Updater")
@allure.feature("External API Integration")
@allure.sub_suite("Discogs API")
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
            async def execute_async_wrapped_call(func: Any, *args: Any, **kwargs: Any) -> Any:
                """Execute async function without tracking."""
                # Simply call the function without tracking
                return await func(*args, **kwargs)

        mock_analytics = MockAnalytics()

        test_api_token = "test_token"  # noqa: S105
        return DiscogsClient(
            token=test_api_token,
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            analytics=mock_analytics,  # type: ignore[arg-type]
            make_api_request_func=mock_api_request,
            score_release_func=mock_score_release,
            cache_service=mock_cache_service,
            scoring_config={"weight_match_year": 0.3, "weight_match_artist": 0.4},
            config={"discogs": {"search_limit": 10}},
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

    @allure.story("Release Search")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should successfully search for release")
    @allure.description("Test successful release search with complete metadata")
    @pytest.mark.asyncio
    async def test_search_release_success(self) -> None:
        """Test successful release search."""
        with allure.step("Setup successful release search response"):
            mock_response = TestDiscogsClientAllure.create_mock_discogs_response("The Beatles", "Abbey Road")
            mock_api_request = AsyncMock(return_value=mock_response)
            client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)

            allure.attach(json.dumps(mock_response, indent=2), "Mock API Response", allure.attachment_type.JSON)

        with allure.step("Search for release"):
            result = await client.get_scored_releases("The Beatles", "Abbey Road", "US")

        with allure.step("Verify successful release retrieval"):
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

            allure.attach(str(len(result)), "Releases Found", allure.attachment_type.TEXT)
            allure.attach(json.dumps(result[0], indent=2), "First Release", allure.attachment_type.JSON)
            allure.attach("✅ Release found with complete metadata", "Search Result", allure.attachment_type.TEXT)

    @allure.story("Release Search")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle release not found scenario")
    @allure.description("Test behavior when release is not found in Discogs")
    @pytest.mark.asyncio
    async def test_search_release_not_found(self) -> None:
        """Test release not found scenario."""
        with allure.step("Setup empty release search response"):
            mock_response = {"results": [], "pagination": {"pages": 0, "items": 0}}
            mock_api_request = AsyncMock(return_value=mock_response)
            client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)

            allure.attach(json.dumps(mock_response, indent=2), "Empty API Response", allure.attachment_type.JSON)

        with allure.step("Search for non-existent release"):
            result = await client.get_scored_releases("NonExistentArtist123", "NonExistentAlbum456", None)

        with allure.step("Verify release not found handling"):
            assert result == []

            allure.attach("[]", "Empty Results", allure.attachment_type.TEXT)
            allure.attach("✅ Release not found handled gracefully", "Search Result", allure.attachment_type.TEXT)

    @allure.story("Year Retrieval")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should retrieve release year correctly")
    @allure.description("Test year retrieval from Discogs release data")
    @pytest.mark.asyncio
    async def test_get_release_year(self) -> None:
        """Test release year retrieval."""
        with allure.step("Setup release with year information"):
            mock_response = TestDiscogsClientAllure.create_mock_discogs_response()
            mock_response["results"][0]["year"] = 1969  # Set specific year
            mock_api_request = AsyncMock(return_value=mock_response)
            client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)

            allure.attach("1969", "Expected Year", allure.attachment_type.TEXT)

        with allure.step("Retrieve release year using backward compatibility method"):
            year = await client.get_year_from_discogs("Test Artist", "Test Album")

        with allure.step("Verify year retrieval"):
            assert year == "1969"

            allure.attach("1969", "Retrieved Year", allure.attachment_type.TEXT)
            allure.attach("✅ Release year retrieved successfully", "Year Result", allure.attachment_type.TEXT)

    @allure.story("Authentication")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle authentication correctly")
    @allure.description("Test API token authentication handling")
    @pytest.mark.asyncio
    async def test_authentication_handling(self) -> None:
        """Test authentication handling."""
        with allure.step("Setup authenticated client"):
            mock_api_request = AsyncMock(return_value={"results": []})
            client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)

            allure.attach("test_token", "API Token", allure.attachment_type.TEXT)

        with allure.step("Make authenticated request"):
            await client.get_scored_releases("Test Artist", "Test Album", None)

        with allure.step("Verify authentication headers"):
            # Verify API request was called (authentication is handled by make_api_request_func)
            mock_api_request.assert_called_once()

            # Check that proper Discogs API URL was used
            call_args = mock_api_request.call_args
            url = call_args[0][1]  # URL argument
            host = urlparse(url).hostname
            assert host is not None
            assert host == "api.discogs.com" or host.endswith(".discogs.com")

            allure.attach("✅ Authentication handled correctly", "Auth Result", allure.attachment_type.TEXT)

    @allure.story("Rate Limiting")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle API quota exceeded")
    @allure.description("Test behavior when API quota is exceeded")
    @pytest.mark.asyncio
    async def test_api_quota_exceeded(self) -> None:
        """Test API quota exceeded handling."""
        with allure.step("Setup quota exceeded scenario"):
            # Mock API request that returns None (quota exceeded)
            mock_api_request = AsyncMock(return_value=None)
            client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)

            allure.attach("API returns None (quota exceeded)", "Quota Scenario", allure.attachment_type.TEXT)

        with allure.step("Attempt search with quota exceeded"):
            result = await client.get_scored_releases("Test Artist", "Test Album", None)

        with allure.step("Verify quota exceeded handling"):
            # Client should handle quota exceeded gracefully
            assert result == []

            allure.attach("[]", "Empty Results", allure.attachment_type.TEXT)
            allure.attach("✅ API quota exceeded handled gracefully", "Quota Result", allure.attachment_type.TEXT)

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle request timeouts")
    @allure.description("Test behavior when API requests timeout")
    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """Test timeout handling."""
        with allure.step("Setup timeout scenario"):
            # Mock API request that returns None (timeout)
            mock_api_request = AsyncMock(return_value=None)
            client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)

            allure.attach("API request timeout (returns None)", "Timeout Scenario", allure.attachment_type.TEXT)

        with allure.step("Attempt search with timeout"):
            result = await client.get_scored_releases("Test Artist", "Test Album", None)

        with allure.step("Verify timeout handling"):
            # Client should handle timeouts gracefully
            assert result == []

            allure.attach("[]", "Empty Results", allure.attachment_type.TEXT)
            allure.attach("✅ Request timeout handled gracefully", "Timeout Result", allure.attachment_type.TEXT)

    @allure.story("Caching")
    @allure.severity(allure.severity_level.MINOR)
    @allure.title("Should utilize caching effectively")
    @allure.description("Test caching behavior for repeated requests")
    @pytest.mark.asyncio
    async def test_caching_behavior(self) -> None:
        """Test caching behavior."""
        with allure.step("Setup caching scenario"):
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

            allure.attach(json.dumps(cached_releases, indent=2), "Cached Data", allure.attachment_type.JSON)

        with allure.step("Request cached data"):
            result = await client.get_scored_releases("Test Artist", "Test Album", None)

        with allure.step("Verify cache utilization"):
            # Should get cached results
            assert result == cached_releases

            # Verify cache was checked
            mock_cache_service.get_async.assert_called()

            allure.attach(json.dumps(result, indent=2), "Returned Results", allure.attachment_type.JSON)
            allure.attach("✅ Cache utilized effectively", "Cache Result", allure.attachment_type.TEXT)
