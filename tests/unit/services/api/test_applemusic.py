"""Tests for AppleMusicClient - iTunes Search API client."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.api.applemusic import AppleMusicClient, VALID_YEAR_LENGTH


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create a test console logger."""
    return logging.getLogger("test.applemusic.console")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create a test error logger."""
    return logging.getLogger("test.applemusic.error")


@pytest.fixture
def mock_api_request_func() -> AsyncMock:
    """Create mock API request function."""
    return AsyncMock(return_value=None)


@pytest.fixture
def mock_score_func() -> MagicMock:
    """Create mock score release function."""
    return MagicMock(return_value=85.0)


@pytest.fixture
def client(
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    mock_api_request_func: AsyncMock,
    mock_score_func: MagicMock,
) -> AppleMusicClient:
    """Create an AppleMusicClient instance."""
    return AppleMusicClient(
        console_logger=console_logger,
        error_logger=error_logger,
        make_api_request_func=mock_api_request_func,
        score_release_func=mock_score_func,
    )


@pytest.fixture
def sample_itunes_result() -> dict[str, Any]:
    """Create a sample iTunes API result."""
    return {
        "artistName": "Pink Floyd",
        "collectionName": "The Dark Side of the Moon",
        "releaseDate": "1973-03-01T12:00:00Z",
        "collectionType": "Album",
        "primaryGenreName": "Rock",
        "copyright": "℗ 1973 Pink Floyd Records",
        "collectionCensoredName": "The Dark Side of the Moon",
    }


class TestInitialization:
    """Tests for AppleMusicClient initialization."""

    def test_init_with_defaults(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Test initialization with default values."""
        client = AppleMusicClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=mock_api_request_func,
            score_release_func=mock_score_func,
        )

        assert client.country_code == "US"
        assert client.entity == "album"
        assert client.limit == 50
        assert client.base_url == "https://itunes.apple.com/search"

    def test_init_with_custom_values(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Test initialization with custom values."""
        client = AppleMusicClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=mock_api_request_func,
            score_release_func=mock_score_func,
            country_code="GB",
            entity="song",
            limit=100,
        )

        assert client.country_code == "GB"
        assert client.entity == "song"
        assert client.limit == 100

    def test_limit_capped_at_200(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Test limit is capped at 200 (iTunes API maximum)."""
        client = AppleMusicClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=mock_api_request_func,
            score_release_func=mock_score_func,
            limit=500,
        )

        assert client.limit == 200

    def test_limit_minimum_is_1(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Test limit minimum is 1."""
        client = AppleMusicClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=mock_api_request_func,
            score_release_func=mock_score_func,
            limit=0,
        )

        assert client.limit == 1

    def test_negative_limit_becomes_1(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Test negative limit becomes 1."""
        client = AppleMusicClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=mock_api_request_func,
            score_release_func=mock_score_func,
            limit=-10,
        )

        assert client.limit == 1


class TestGetScoredReleases:
    """Tests for get_scored_releases method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_response(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test returns empty list when API returns None."""
        mock_api_request_func.return_value = None

        result = await client.get_scored_releases("pink floyd", "dark side")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_results(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test returns empty list when API returns empty results."""
        mock_api_request_func.return_value = {"results": []}

        result = await client.get_scored_releases("unknown artist", "unknown album")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_scored_releases(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
        sample_itunes_result: dict[str, Any],
    ) -> None:
        """Test returns scored releases from API response."""
        mock_api_request_func.return_value = {"results": [sample_itunes_result]}
        mock_score_func.return_value = 90.0

        result = await client.get_scored_releases("pink floyd", "dark side")

        assert len(result) == 1
        assert result[0]["title"] == "The Dark Side of the Moon"
        assert result[0]["artist"] == "Pink Floyd"
        assert result[0]["year"] == "1973"
        assert result[0]["score"] == 90.0
        assert result[0]["source"] == "itunes"

    @pytest.mark.asyncio
    async def test_makes_correct_api_call(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test makes correct API call with parameters."""
        mock_api_request_func.return_value = {"results": []}

        await client.get_scored_releases("pink floyd", "dark side")

        mock_api_request_func.assert_called_once()
        call_kwargs = mock_api_request_func.call_args[1]
        assert call_kwargs["api_name"] == "itunes"
        assert call_kwargs["url"] == "https://itunes.apple.com/search"
        assert "pink floyd dark side" in call_kwargs["params"]["term"]
        assert call_kwargs["params"]["country"] == "US"
        assert call_kwargs["params"]["entity"] == "album"

    @pytest.mark.asyncio
    async def test_handles_api_error(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test handles API error gracefully."""
        mock_api_request_func.side_effect = OSError("Connection error")

        result = await client.get_scored_releases("pink floyd", "dark side")

        assert result == []

    @pytest.mark.asyncio
    async def test_skips_results_without_year(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Test skips results without valid release year."""
        result_without_year = {
            "artistName": "Pink Floyd",
            "collectionName": "The Dark Side",
            "releaseDate": "",
        }
        mock_api_request_func.return_value = {"results": [result_without_year]}

        result = await client.get_scored_releases("pink floyd", "dark side")

        assert result == []
        mock_score_func.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_multiple_results(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Test processes multiple results."""
        results = [
            {
                "artistName": "Pink Floyd",
                "collectionName": "The Dark Side of the Moon",
                "releaseDate": "1973-03-01T12:00:00Z",
            },
            {
                "artistName": "Pink Floyd",
                "collectionName": "Wish You Were Here",
                "releaseDate": "1975-09-12T12:00:00Z",
            },
        ]
        mock_api_request_func.return_value = {"results": results}
        mock_score_func.return_value = 85.0

        result = await client.get_scored_releases("pink floyd", "albums")

        assert len(result) == 2


class TestProcessItunesResult:
    """Tests for _process_itunes_result method."""

    def test_process_valid_result(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
        sample_itunes_result: dict[str, Any],
    ) -> None:
        """Test processing a valid iTunes result."""
        mock_score_func.return_value = 90.0

        result = client._process_itunes_result(
            sample_itunes_result,
            "pink floyd",
            "dark side",
        )

        assert result is not None
        assert result["title"] == "The Dark Side of the Moon"
        assert result["artist"] == "Pink Floyd"
        assert result["year"] == "1973"
        assert result["score"] == 90.0
        assert result["format"] == "Digital"
        assert result["status"] == "official"

    def test_returns_none_for_missing_artist(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test returns None when artist is missing."""
        result_data = {
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = client._process_itunes_result(result_data, "artist", "album")

        assert result is None

    def test_returns_none_for_missing_collection(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test returns None when collection name is missing."""
        result_data = {
            "artistName": "Artist",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = client._process_itunes_result(result_data, "artist", "album")

        assert result is None

    def test_returns_none_for_empty_artist(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test returns None when artist is empty string."""
        result_data = {
            "artistName": "",
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = client._process_itunes_result(result_data, "artist", "album")

        assert result is None

    def test_returns_none_for_invalid_year(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test returns None when year is invalid."""
        result_data = {
            "artistName": "Artist",
            "collectionName": "Album",
            "releaseDate": "invalid-date",
        }

        result = client._process_itunes_result(result_data, "artist", "album")

        assert result is None

    def test_returns_none_for_short_year(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test returns None when year has wrong length."""
        result_data = {
            "artistName": "Artist",
            "collectionName": "Album",
            "releaseDate": "20-01-01T00:00:00Z",  # Year too short
        }

        result = client._process_itunes_result(result_data, "artist", "album")

        assert result is None

    def test_handles_scoring_error(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
    ) -> None:
        """Test handles scoring function errors."""
        mock_score_func.side_effect = ValueError("Scoring error")
        result_data = {
            "artistName": "Artist",
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = client._process_itunes_result(result_data, "artist", "album")

        assert result is None

    def test_includes_optional_fields(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
        sample_itunes_result: dict[str, Any],
    ) -> None:
        """Test includes optional fields when present."""
        mock_score_func.return_value = 85.0

        result = client._process_itunes_result(
            sample_itunes_result,
            "pink floyd",
            "dark side",
        )

        assert result is not None
        assert result["label"] == "℗ 1973 Pink Floyd Records"
        assert result["album_type"] == "Album"
        assert result["disambiguation"] == "The Dark Side of the Moon"

    def test_handles_missing_optional_fields(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
    ) -> None:
        """Test handles missing optional fields."""
        mock_score_func.return_value = 80.0
        result_data = {
            "artistName": "Artist",
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = client._process_itunes_result(result_data, "artist", "album")

        assert result is not None
        assert result["catalog_number"] is None
        assert result["barcode"] is None


class TestConstants:
    """Tests for module constants."""

    def test_valid_year_length(self) -> None:
        """Test VALID_YEAR_LENGTH constant."""
        assert VALID_YEAR_LENGTH == 4


class TestScoredReleaseStructure:
    """Tests for ScoredRelease structure returned by the client."""

    @pytest.mark.asyncio
    async def test_scored_release_has_all_required_fields(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
        sample_itunes_result: dict[str, Any],
    ) -> None:
        """Test ScoredRelease has all required fields."""
        mock_api_request_func.return_value = {"results": [sample_itunes_result]}
        mock_score_func.return_value = 85.0

        results = await client.get_scored_releases("pink floyd", "dark side")

        assert len(results) == 1
        release = results[0]

        # Check all required fields exist
        assert "title" in release
        assert "year" in release
        assert "score" in release
        assert "artist" in release
        assert "source" in release
        assert "album_type" in release
        assert "country" in release
        assert "status" in release
        assert "format" in release
        assert "label" in release
        assert "catalog_number" in release
        assert "barcode" in release
        assert "disambiguation" in release


class TestEdgeCases:
    """Tests for edge cases."""

    def test_strips_whitespace_from_names(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
    ) -> None:
        """Test whitespace is stripped from artist and collection names."""
        mock_score_func.return_value = 80.0
        result_data = {
            "artistName": "  Artist  ",
            "collectionName": "  Album  ",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = client._process_itunes_result(result_data, "artist", "album")

        assert result is not None
        assert result["artist"] == "Artist"
        assert result["title"] == "Album"

    def test_handles_whitespace_only_artist(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test returns None for whitespace-only artist."""
        result_data = {
            "artistName": "   ",
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = client._process_itunes_result(result_data, "artist", "album")

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_empty_search_terms(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test handles empty search terms."""
        mock_api_request_func.return_value = {"results": []}

        result = await client.get_scored_releases("", "")

        # Should make request with just space-stripped term
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_runtime_error(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test handles RuntimeError."""
        mock_api_request_func.side_effect = RuntimeError("Runtime error")

        result = await client.get_scored_releases("artist", "album")

        assert result == []

    @pytest.mark.asyncio
    async def test_handles_value_error(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test handles ValueError."""
        mock_api_request_func.side_effect = ValueError("Value error")

        result = await client.get_scored_releases("artist", "album")

        assert result == []
