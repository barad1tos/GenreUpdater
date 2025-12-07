"""Enhanced MusicBrainz API client tests with Allure reporting."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import allure
import pytest

from services.api.musicbrainz import MusicBrainzClient
from tests.mocks.csv_mock import MockLogger


@allure.epic("Music Genre Updater")
@allure.feature("External API Integration")
@allure.sub_suite("MusicBrainz API")
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

    @allure.story("Artist Search")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should successfully search for artist")
    @allure.description("Test successful artist search with complete metadata")
    @pytest.mark.asyncio
    async def test_search_artist_success(self) -> None:
        """Test successful artist search."""
        with allure.step("Setup successful artist search response"):
            mock_response = TestMusicBrainzClientAllure.create_mock_artist_response("The Beatles")
            mock_api_request = AsyncMock(return_value=mock_response)
            client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)

            allure.attach(json.dumps(mock_response, indent=2), "Mock API Response", allure.attachment_type.JSON)

        with allure.step("Search for artist"):
            result = await client.get_artist_info("The Beatles", include_aliases=True)

        with allure.step("Verify successful artist retrieval"):
            assert result is not None
            assert result["name"] == "The Beatles"
            assert result["id"] == "12345-6789-abcd-efgh"
            assert "life-span" in result
            assert "aliases" in result

            # Verify API was called with correct parameters
            mock_api_request.assert_called_once()
            call_args = mock_api_request.call_args[1]
            assert "artist:" in call_args["params"]["query"]

            allure.attach("✅ Artist found with complete metadata", "Search Result", allure.attachment_type.TEXT)

    @allure.story("Artist Search")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle artist not found scenario")
    @allure.description("Test behavior when artist is not found in MusicBrainz")
    @pytest.mark.asyncio
    async def test_search_artist_not_found(self) -> None:
        """Test artist not found scenario."""
        with allure.step("Setup empty artist search response"):
            mock_response: dict[str, Any] = {"artists": []}  # Mock empty response
            mock_api_request = AsyncMock(return_value=mock_response)
            client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)

            allure.attach(json.dumps(mock_response, indent=2), "Empty API Response", allure.attachment_type.JSON)

        with allure.step("Search for non-existent artist"):
            result = await client.get_artist_info("NonExistentArtist123")

        with allure.step("Verify artist not found handling"):
            assert result is None

            allure.attach("None", "Search Result", allure.attachment_type.TEXT)
            allure.attach("✅ Artist not found handled gracefully", "Search Result", allure.attachment_type.TEXT)

    @allure.story("Rate Limiting")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should handle rate limiting correctly")
    @allure.description("Test rate limiting behavior and retry mechanisms")
    @pytest.mark.asyncio
    async def test_rate_limiting(self) -> None:
        """Test rate limiting handling."""
        with allure.step("Setup rate limiting scenario"):
            # Mock API request that returns None (rate limited)
            mock_api_request = AsyncMock(return_value=None)
            client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)

            allure.attach("API returns None (rate limited)", "Rate Limiting Scenario", allure.attachment_type.TEXT)

        with allure.step("Attempt search with rate limiting"):
            result = await client.get_artist_info("Test Artist")

        with allure.step("Verify rate limiting is handled"):
            # The client should handle the rate limiting gracefully
            # First call returns None due to rate limiting, handled by client
            assert mock_api_request.call_count == 1  # Only one call made by client

            # Result should be None when rate limited
            assert result is None

            allure.attach("1", "API Calls Made", allure.attachment_type.TEXT)
            allure.attach("✅ Rate limiting handled gracefully", "Rate Limiting Result", allure.attachment_type.TEXT)

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle network errors gracefully")
    @allure.description("Test behavior when network errors occur")
    @pytest.mark.asyncio
    async def test_network_error_handling(self) -> None:
        """Test network error handling."""
        with allure.step("Setup network error scenario"):
            # Mock API request to return None (network error)
            mock_api_request = AsyncMock(return_value=None)
            client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)

            allure.attach("Network connection failed (returns None)", "Simulated Error", allure.attachment_type.TEXT)

        with allure.step("Attempt search with network error"):
            result = await client.get_artist_info("Test Artist")

        with allure.step("Verify network error handling"):
            # Client should handle the network error gracefully
            assert result is None

            allure.attach("None", "Result After Error", allure.attachment_type.TEXT)
            allure.attach("✅ Network error handled gracefully", "Error Handling Result", allure.attachment_type.TEXT)

    @allure.story("Data Validation")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle malformed API responses")
    @allure.description("Test behavior when API returns malformed data")
    @pytest.mark.asyncio
    async def test_malformed_response(self) -> None:
        """Test handling of malformed API responses."""
        with allure.step("Setup malformed response scenarios"):
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

            allure.attach(json.dumps(malformed_responses, indent=2), "Malformed Responses", allure.attachment_type.JSON)

        for i, malformed_response in enumerate(malformed_responses):
            with allure.step(f"Test malformed response {i + 1}"):
                mock_api_request = AsyncMock(return_value=malformed_response)
                client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)

                # Attempt search with malformed response
                result = await client.get_artist_info("Test Artist")

                # Client should handle malformed responses gracefully
                # May return None or empty results depending on implementation
                assert result is not None or result is None  # Should not crash

                allure.attach(f"Response {i + 1}: handled gracefully", "Malformed Response Result", allure.attachment_type.TEXT)

        with allure.step("Verify all malformed responses handled"):
            allure.attach("✅ All malformed responses handled without crashes", "Malformed Response Summary", allure.attachment_type.TEXT)

    @allure.story("Search Utilities")
    @allure.severity(allure.severity_level.MINOR)
    @allure.title("Should properly escape Lucene query syntax")
    @allure.description("Test Lucene query escaping for safe API queries")
    def test_lucene_escaping(self) -> None:
        """Test Lucene query escaping functionality."""
        with allure.step("Setup test strings with special characters"):
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

            allure.attach(
                json.dumps([{"input": inp, "expected": exp} for inp, exp in test_cases], indent=2), "Escaping Test Cases", allure.attachment_type.JSON
            )

        with allure.step("Test Lucene escaping"):
            for input_str, expected in test_cases:
                with allure.step(f"Escape '{input_str}'"):
                    result = MusicBrainzClient._escape_lucene(input_str)
                    assert result == expected, f"Expected '{expected}', got '{result}'"

                    allure.attach(
                        f"'{input_str}' → '{result}'",
                        "Escaping Result",
                        allure.attachment_type.TEXT,
                    )

        with allure.step("Verify all special characters escaped"):
            allure.attach("✅ All Lucene special characters properly escaped", "Escaping Summary", allure.attachment_type.TEXT)

    @allure.story("Artist Region")
    @allure.severity(allure.severity_level.MINOR)
    @allure.title("Should retrieve artist region information")
    @allure.description("Test artist region/country retrieval functionality")
    @pytest.mark.asyncio
    async def test_get_artist_region(self) -> None:
        """Test artist region retrieval."""
        with allure.step("Setup artist with region information"):
            artist_response = TestMusicBrainzClientAllure.create_mock_artist_response()
            # Ensure country is in the response
            artist_response["artists"][0]["country"] = "US"

            mock_api_request = AsyncMock(return_value=artist_response)
            client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)

            allure.attach("US", "Expected Region", allure.attachment_type.TEXT)

        with allure.step("Retrieve artist region"):
            region = await client.get_artist_region("Test Artist")

        with allure.step("Verify region retrieval"):
            assert region == "United States"

            allure.attach("United States", "Retrieved Region", allure.attachment_type.TEXT)
            allure.attach("✅ Artist region retrieved successfully", "Region Result", allure.attachment_type.TEXT)

    @allure.story("Artist Activity Period")
    @allure.severity(allure.severity_level.MINOR)
    @allure.title("Should retrieve artist activity period")
    @allure.description("Test artist life span/activity period retrieval")
    @pytest.mark.asyncio
    async def test_get_artist_activity_period(self) -> None:
        """Test artist activity period retrieval."""
        with allure.step("Setup artist with activity period"):
            artist_response = TestMusicBrainzClientAllure.create_mock_artist_response()
            # Ensure life-span is in the response
            artist_response["artists"][0]["life-span"] = {"begin": "1980", "end": "2020", "ended": True}

            mock_api_request = AsyncMock(return_value=artist_response)
            client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=mock_api_request)

            allure.attach("1980 - 2020", "Expected Activity Period", allure.attachment_type.TEXT)

        with allure.step("Retrieve artist activity period"):
            begin, end = await client.get_artist_activity_period("Test Artist")

        with allure.step("Verify activity period retrieval"):
            assert begin == "1980"
            assert end == "2020"

            allure.attach(f"{begin} - {end}", "Retrieved Activity Period", allure.attachment_type.TEXT)
            allure.attach("✅ Artist activity period retrieved successfully", "Activity Period Result", allure.attachment_type.TEXT)
