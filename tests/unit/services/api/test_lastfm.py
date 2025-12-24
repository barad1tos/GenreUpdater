"""Enhanced Last.fm API client tests with Allure reporting."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest
from services.api.lastfm import LastFmClient

from tests.mocks.csv_mock import MockLogger


class TestLastFmClientAllure:
    """Enhanced tests for LastFmClient with Allure reporting."""

    @staticmethod
    def create_lastfm_client(
        mock_api_request: AsyncMock | None = None,
        mock_score_release: MagicMock | None = None,
        use_lastfm: bool = True,
    ) -> LastFmClient:
        """Create a LastFmClient instance for testing."""
        if mock_api_request is None:
            mock_api_request = AsyncMock(return_value={})

        if mock_score_release is None:
            mock_score_release = MagicMock(return_value=0.85)

        return LastFmClient(
            api_key="test_api_key",
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            make_api_request_func=mock_api_request,
            score_release_func=mock_score_release,
            use_lastfm=use_lastfm,
        )

    @staticmethod
    def create_mock_artist_response(artist_name: str = "Test Artist") -> dict[str, Any]:
        """Create a mock Last.fm artist.getInfo response."""
        return {
            "artist": {
                "name": artist_name,
                "mbid": "12345678-1234-1234-1234-123456789abc",
                "url": f"https://www.last.fm/music/{artist_name.replace(' ', '+')}",
                "image": [{"size": "medium", "#text": "https://lastfm.freetls.fastly.net/i/u/64s/artist.png"}],
                "streamable": "0",
                "ontour": "0",
                "stats": {"listeners": "1234567", "playcount": "9876543"},
                "similar": {"artist": [{"name": "Similar Artist 1", "url": "https://www.last.fm/music/Similar+Artist+1", "image": []}]},
                "tags": {
                    "tag": [
                        {"name": "rock", "url": "https://www.last.fm/tag/rock"},
                        {"name": "alternative rock", "url": "https://www.last.fm/tag/alternative+rock"},
                    ]
                },
                "bio": {
                    "links": {"link": {"rel": "original", "href": "https://last.fm/music/Test+Artist/+wiki"}},
                    "published": "01 Jan 2020, 10:00",
                    "summary": f"{artist_name} is a test artist for unit testing.",
                    "content": f"{artist_name} is a comprehensive test artist created for unit testing purposes. Originally formed in 2020.",
                },
            }
        }

    @staticmethod
    def create_mock_album_response(artist_name: str = "Test Artist", album_name: str = "Test Album") -> dict[str, Any]:
        """Create a mock Last.fm album.getInfo response."""
        return {
            "album": {
                "name": album_name,
                "artist": artist_name,
                "mbid": "12345678-1234-1234-1234-123456789abc",
                "url": f"https://www.last.fm/music/{artist_name.replace(' ', '+')}/" + album_name.replace(" ", "+"),
                "image": [{"size": "large", "#text": "https://lastfm.freetls.fastly.net/i/u/300x300/album.jpg"}],
                "listeners": "123456",
                "playcount": "654321",
                "releasedate": "15 Jan 2020",
                "tracks": {
                    "track": [
                        {
                            "name": "Track 1",
                            "duration": "180",
                            "url": f"https://www.last.fm/music/{artist_name.replace(' ', '+')}/{album_name.replace(' ', '+')}/Track+1",
                        },
                        {
                            "name": "Track 2",
                            "duration": "210",
                            "url": f"https://www.last.fm/music/{artist_name.replace(' ', '+')}/{album_name.replace(' ', '+')}/Track+2",
                        },
                    ]
                },
                "tags": {"tag": [{"name": "rock", "url": "https://www.last.fm/tag/rock"}, {"name": "2020", "url": "https://www.last.fm/tag/2020"}]},
                "wiki": {
                    "published": "01 Jan 2020, 10:00",
                    "summary": f"{album_name} is a test album by {artist_name}.",
                    "content": (
                        f"{album_name} is a comprehensive test album released in 2020 by {artist_name}. Originally released on January 15, 2020."
                    ),
                },
            }
        }

    @pytest.mark.asyncio
    async def test_get_artist_info(self) -> None:
        """Test successful artist information retrieval."""
        mock_response = TestLastFmClientAllure.create_mock_artist_response("The Beatles")
        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestLastFmClientAllure.create_lastfm_client(mock_api_request=mock_api_request)
        result = await client.get_artist_info("The Beatles")
        assert result is not None
        assert result["name"] == "The Beatles"
        assert "mbid" in result
        assert "bio" in result
        assert "tags" in result
        assert "stats" in result

        # Verify API was called with correct parameters
        mock_api_request.assert_called_once()
        call_args = mock_api_request.call_args
        assert "artist.getInfo" in call_args[1]["params"]["method"]
        assert "The Beatles" in call_args[1]["params"]["artist"]
        assert "test_api_key" in call_args[1]["params"]["api_key"]

    @pytest.mark.asyncio
    async def test_get_album_info(self) -> None:
        """Test successful album information retrieval."""
        mock_response = TestLastFmClientAllure.create_mock_album_response("The Beatles", "Abbey Road")
        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestLastFmClientAllure.create_lastfm_client(mock_api_request=mock_api_request)
        result = await client.get_album_info("The Beatles", "Abbey Road")
        assert result is not None
        assert result["name"] == "Abbey Road"
        assert result["artist"] == "The Beatles"
        assert "releasedate" in result
        assert "tags" in result
        assert "wiki" in result
        assert "tracks" in result

        # Verify API was called with correct parameters
        mock_api_request.assert_called_once()
        call_args = mock_api_request.call_args
        assert "album.getInfo" in call_args[1]["params"]["method"]
        assert "The Beatles" in call_args[1]["params"]["artist"]
        assert "Abbey Road" in call_args[1]["params"]["album"]

    @pytest.mark.asyncio
    async def test_get_top_tags(self) -> None:
        """Test top tags retrieval from artist and album responses."""
        artist_response = TestLastFmClientAllure.create_mock_artist_response()
        album_response = TestLastFmClientAllure.create_mock_album_response()

        mock_api_request = AsyncMock()
        mock_api_request.side_effect = [artist_response, album_response]
        client = TestLastFmClientAllure.create_lastfm_client(mock_api_request=mock_api_request)
        artist_info = await client.get_artist_info("Test Artist")
        album_info = await client.get_album_info("Test Artist", "Test Album")
        # Verify artist tags
        assert artist_info is not None
        assert "tags" in artist_info
        artist_tags = artist_info["tags"]["tag"]
        assert len(artist_tags) > 0
        assert any(tag["name"] == "rock" for tag in artist_tags)
        assert any(tag["name"] == "alternative rock" for tag in artist_tags)

        # Verify album tags
        assert album_info is not None
        assert "tags" in album_info
        album_tags = album_info["tags"]["tag"]
        assert len(album_tags) > 0
        assert any(tag["name"] == "rock" for tag in album_tags)
        assert any(tag["name"] == "2020" for tag in album_tags)  # Year tag

    @pytest.mark.asyncio
    async def test_api_key_validation(self) -> None:
        """Test API key validation."""
        # Mock invalid API key response
        invalid_key_response = {"error": 10, "message": "Invalid API key - You must be granted a valid key by last.fm"}

        mock_api_request = AsyncMock(return_value=invalid_key_response)
        client = TestLastFmClientAllure.create_lastfm_client(mock_api_request=mock_api_request)
        result = await client.get_artist_info("Test Artist")
        # Should return None when API key is invalid
        assert result is None

        # Verify API was called with the test key
        mock_api_request.assert_called_once()
        call_args = mock_api_request.call_args
        assert "test_api_key" in call_args[1]["params"]["api_key"]

    @pytest.mark.asyncio
    async def test_service_unavailable(self) -> None:
        """Test service unavailability handling."""
        # Test when API request returns None (network/service issue)
        mock_api_request = AsyncMock(return_value=None)
        client = TestLastFmClientAllure.create_lastfm_client(mock_api_request=mock_api_request)
        artist_result = await client.get_artist_info("Test Artist")
        album_result = await client.get_album_info("Test Artist", "Test Album")
        # Both should return None when service is unavailable
        assert artist_result is None
        assert album_result is None

        # Verify API was attempted
        assert mock_api_request.call_count == 2

    @pytest.mark.asyncio
    async def test_use_lastfm_disabled(self) -> None:
        """Test behavior when use_lastfm is disabled."""
        mock_api_request = AsyncMock()
        client = TestLastFmClientAllure.create_lastfm_client(mock_api_request=mock_api_request, use_lastfm=False)
        artist_result = await client.get_artist_info("Test Artist")
        album_result = await client.get_album_info("Test Artist", "Test Album")
        # Should return None without making API calls
        assert artist_result is None
        assert album_result is None

        # Verify no API calls were made
        mock_api_request.assert_not_called()
