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


class TestLastFmAlbumCleaning:
    """Tests for Last.fm album name cleaning for fallback search."""

    @staticmethod
    def create_client_with_keywords(keywords: list[str] | None = None) -> LastFmClient:
        """Create a LastFmClient with specified remaster keywords."""
        config: dict[str, Any] = {}
        if keywords is not None:
            config = {"cleaning": {"remaster_keywords": keywords}}

        return LastFmClient(
            api_key="test_key",
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            make_api_request_func=AsyncMock(return_value={}),
            score_release_func=MagicMock(return_value=0.85),
            config=config,
        )

    def test_clean_strips_trailing_whitespace(self) -> None:
        """Test that trailing whitespace is stripped."""
        client = self.create_client_with_keywords([])
        result = client._clean_album_for_search("Mother of Souls ")
        assert result == "Mother of Souls"

    def test_clean_strips_colon_suffix(self) -> None:
        """Test that content after colon is stripped."""
        client = self.create_client_with_keywords([])
        result = client._clean_album_for_search("Delta Machine: The 12 Singles")
        assert result == "Delta Machine"

    def test_clean_strips_keyword_suffix(self) -> None:
        """Test that trailing keywords are stripped."""
        client = self.create_client_with_keywords(["The 12 Singles", "Remastered"])
        result = client._clean_album_for_search("A Broken Frame The 12 Singles")
        assert result == "A Broken Frame"

    def test_clean_returns_none_when_unchanged(self) -> None:
        """Test that None is returned when no cleaning is needed."""
        client = self.create_client_with_keywords([])
        result = client._clean_album_for_search("Thriller")
        assert result is None

    def test_clean_returns_none_for_empty_string(self) -> None:
        """Test that None is returned for empty input."""
        client = self.create_client_with_keywords([])
        result = client._clean_album_for_search("")
        assert result is None

    def test_clean_handles_multiple_keywords(self) -> None:
        """Test cleaning with multiple potential keywords."""
        client = self.create_client_with_keywords(["Deluxe", "Edition", "Deluxe Edition"])
        result = client._clean_album_for_search("Album Deluxe Edition")
        assert result == "Album"

    @pytest.mark.parametrize(
        ("album", "expected"),
        [
            ("A Broken Frame The 12 Singles", "A Broken Frame"),
            ("Delta Machine: The 12 Singles", "Delta Machine"),
            ("Mother of Souls ", "Mother of Souls"),
            ("Thriller", None),
        ],
    )
    def test_issue_106_album_patterns(self, album: str, expected: str | None) -> None:
        """Test patterns from Issue #106."""
        client = self.create_client_with_keywords(["The 12 Singles", 'The 12" Singles'])
        result = client._clean_album_for_search(album)
        assert result == expected


class TestLastFmArtistMatching:
    """Tests for Last.fm artist name normalization and matching."""

    def test_normalize_basic(self) -> None:
        """Test basic normalization (strip + lowercase)."""
        result = LastFmClient._normalize_artist_for_matching("  Depeche Mode  ")
        assert result == "depeche mode"

    def test_normalize_handles_the_suffix(self) -> None:
        """Test 'X, The' -> 'the x' conversion."""
        result = LastFmClient._normalize_artist_for_matching("Beatles, The")
        assert result == "the beatles"

    def test_normalize_removes_disambiguation_suffix(self) -> None:
        """Test removal of (2) disambiguation suffix."""
        result = LastFmClient._normalize_artist_for_matching("Genesis (2)")
        assert result == "genesis"

    def test_normalize_empty_string(self) -> None:
        """Test empty string handling."""
        result = LastFmClient._normalize_artist_for_matching("")
        assert result == ""

    def test_is_artist_match_exact(self) -> None:
        """Test exact match."""
        client = TestLastFmAlbumCleaning.create_client_with_keywords([])
        assert client._is_artist_match("Depeche Mode", "Depeche Mode")

    def test_is_artist_match_case_insensitive(self) -> None:
        """Test case-insensitive match."""
        client = TestLastFmAlbumCleaning.create_client_with_keywords([])
        assert client._is_artist_match("DEPECHE MODE", "depeche mode")

    def test_is_artist_match_the_variation(self) -> None:
        """Test 'The Beatles' vs 'Beatles, The' matching."""
        client = TestLastFmAlbumCleaning.create_client_with_keywords([])
        assert client._is_artist_match("The Beatles", "Beatles, The")
        assert client._is_artist_match("Beatles, The", "The Beatles")

    def test_is_artist_match_disambiguation(self) -> None:
        """Test matching with disambiguation suffix."""
        client = TestLastFmAlbumCleaning.create_client_with_keywords([])
        assert client._is_artist_match("Genesis (2)", "Genesis")
        assert client._is_artist_match("Genesis", "Genesis (2)")

    def test_is_artist_match_substring_fallback(self) -> None:
        """Test substring fallback matching."""
        client = TestLastFmAlbumCleaning.create_client_with_keywords([])
        # "air" should match "air supply" via substring
        assert client._is_artist_match("Air Supply", "Air")

    def test_is_artist_no_match(self) -> None:
        """Test non-matching artists."""
        client = TestLastFmAlbumCleaning.create_client_with_keywords([])
        assert not client._is_artist_match("Metallica", "Iron Maiden")


class TestLastFmFallbackChain:
    """Tests for Last.fm fallback search chain."""

    @pytest.mark.asyncio
    async def test_primary_search_success_no_fallback(self) -> None:
        """Test that fallbacks are not called when primary succeeds."""
        mock_api = AsyncMock(
            return_value={
                "album": {
                    "name": "Test Album",
                    "artist": "Test Artist",
                    "releasedate": "2020",
                }
            }
        )
        client = LastFmClient(
            api_key="test_key",
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            make_api_request_func=mock_api,
            score_release_func=MagicMock(return_value=50.0),
        )

        result = await client.get_scored_releases("test artist", "test album")

        # Should have result from primary search
        assert len(result) == 1
        assert result[0]["year"] == "2020"
        # Only one API call (primary)
        assert mock_api.call_count == 1

    @pytest.mark.asyncio
    async def test_fallback_to_cleaned_album(self) -> None:
        """Test fallback to cleaned album name when primary fails."""

        call_count = 0

        async def mock_api_func(_source: str, _url: str, params: dict[str, str]) -> dict[str, Any] | None:
            """Mock API function that simulates primary failure and cleaned success."""
            nonlocal call_count
            call_count += 1
            album_param = params.get("album", "")

            # Primary fails (full album name)
            if "The 12 Singles" in album_param:
                return None

            # Cleaned album succeeds
            if album_param == "A Broken Frame":
                return {
                    "album": {
                        "name": "A Broken Frame",
                        "artist": "Depeche Mode",
                        "releasedate": "1982",
                    }
                }
            return None

        client = LastFmClient(
            api_key="test_key",
            console_logger=MockLogger(),  # type: ignore[arg-type]
            error_logger=MockLogger(),  # type: ignore[arg-type]
            make_api_request_func=mock_api_func,  # type: ignore[arg-type]
            score_release_func=MagicMock(return_value=50.0),
            config={"cleaning": {"remaster_keywords": ["The 12 Singles"]}},
        )

        result = await client.get_scored_releases("depeche mode", "A Broken Frame The 12 Singles")

        # Should have result from fallback
        assert len(result) == 1
        assert result[0]["year"] == "1982"
        # Two API calls: primary + fallback 1
        assert call_count == 2
