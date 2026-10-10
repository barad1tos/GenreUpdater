"""Tests for AppleMusicClient - iTunes Search API client."""

from __future__ import annotations

from tests.factories import fetch_and_score

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from metrics.analytics import Analytics
from services.api.applemusic import AppleMusicClient
from services.api.request_executor import ApiRequestError
from services.api.year_scoring import ArtistContext
from tests.mocks.csv_mock import MockAnalytics

from core.models.release_record import ReleaseRecord

if TYPE_CHECKING:
    from services.api.api_base import ScoredRelease


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
        analytics=MagicMock(),
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
            analytics=MagicMock(),
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
            analytics=MagicMock(),
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
            analytics=MagicMock(),
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
            analytics=MagicMock(),
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
            analytics=MagicMock(),
        )

        assert client.limit == 1


class TestFetchAndScore:
    """Fetching and scoring iTunes releases."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_response(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test returns empty list when API returns None."""
        mock_api_request_func.return_value = None

        result = await fetch_and_score(client, "pink floyd", "dark side", artist_context=ArtistContext())

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_results(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test returns empty list when API returns empty results."""
        mock_api_request_func.return_value = {"results": []}

        result = await fetch_and_score(client, "unknown artist", "unknown album", artist_context=ArtistContext())

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

        result = await fetch_and_score(client, "pink floyd", "dark side", artist_context=ArtistContext())

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
        sample_itunes_result: dict[str, Any],
    ) -> None:
        """Test makes correct API call with parameters."""
        # Return valid results to avoid triggering the lookup fallback
        mock_api_request_func.return_value = {"results": [sample_itunes_result]}

        await fetch_and_score(client, "pink floyd", "dark side", artist_context=ArtistContext())

        mock_api_request_func.assert_called_once()
        call_kwargs = mock_api_request_func.call_args[1]
        assert call_kwargs["api_name"] == "itunes"
        assert call_kwargs["url"] == "https://itunes.apple.com/search"
        assert "pink floyd dark side" in call_kwargs["params"]["term"]
        assert call_kwargs["params"]["country"] == "US"
        assert call_kwargs["params"]["entity"] == "album"

    @pytest.mark.asyncio
    async def test_lookup_fallback_when_search_empty(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Test fallback to artist lookup when search returns no results."""
        # First call: search returns empty
        # Second call: artist search returns artist ID
        # Third call: lookup returns albums
        album_result = {
            "wrapperType": "collection",
            "artistName": "Korn",
            "collectionName": "Issues",
            "releaseDate": "1999-11-16T08:00:00Z",
        }
        mock_api_request_func.side_effect = [
            {"results": []},  # Search: no results
            {"results": [{"artistId": 466532, "artistName": "Korn"}]},  # Artist search
            {"results": [{"wrapperType": "artist"}, album_result]},  # Lookup: artist + album
        ]
        mock_score_func.return_value = 85.0

        result = await fetch_and_score(client, "korn", "issues", artist_context=ArtistContext())

        # Should have made 3 API calls
        assert mock_api_request_func.call_count == 3
        # Should return the album from lookup
        assert len(result) == 1
        assert result[0]["title"] == "Issues"
        assert result[0]["year"] == "1999"

    @pytest.mark.asyncio
    async def test_lookup_fallback_no_artist_found(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test returns empty when search and artist lookup both fail."""
        mock_api_request_func.side_effect = [
            {"results": []},  # Search: no results
            {"results": []},  # Artist search: no matching artist
        ]

        result = await fetch_and_score(client, "unknown artist", "unknown album", artist_context=ArtistContext())

        assert mock_api_request_func.call_count == 2
        assert result == []

    @pytest.mark.asyncio
    async def test_fallback_when_api_returns_none(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Test fallback triggers when API returns None instead of empty results.

        Covers the full expression: `response_data.get(...) if response_data else [] or
        await self._try_lookup_fallback(...)`.
        When make_api_request_func returns None (network issue, timeout, etc.),
        the empty list result from the conditional expression should trigger the
        fallback lookup.

        Context for debugging:
        - Who: iTunes Search API client
        - What: Primary search returned None, fallback to artist lookup
        - Why: Network issues or API returning null response
        """
        album_result = {
            "wrapperType": "collection",
            "artistName": "Tool",
            "collectionName": "Lateralus",
            "releaseDate": "2001-05-15T07:00:00Z",
        }
        mock_api_request_func.side_effect = [
            None,  # Primary search: None response (network issue/timeout)
            {"results": [{"artistId": 54619, "artistName": "Tool"}]},  # Artist search
            {"results": [{"wrapperType": "artist"}, album_result]},  # Lookup: albums
        ]
        mock_score_func.return_value = 90.0

        result = await fetch_and_score(client, "tool", "lateralus", artist_context=ArtistContext())

        # Should have made 3 API calls: primary search (None) + artist search + lookup
        assert mock_api_request_func.call_count == 3
        # Should return the album from fallback lookup
        assert len(result) == 1
        assert result[0]["title"] == "Lateralus"
        assert result[0]["year"] == "2001"

    @pytest.mark.asyncio
    async def test_handles_api_error(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """A broken request fails the lookup instead of reading as "nothing found"."""
        mock_api_request_func.side_effect = OSError("Connection error")

        with pytest.raises(OSError, match="Connection error"):
            await fetch_and_score(client, "pink floyd", "dark side", artist_context=ArtistContext())

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

        result = await fetch_and_score(client, "pink floyd", "dark side", artist_context=ArtistContext())

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

        result = await fetch_and_score(client, "pink floyd", "albums", artist_context=ArtistContext())

        assert len(result) == 2


class TestProcessItunesResult:
    """Tests for turning one iTunes result into a scored release."""

    @staticmethod
    def score_one(
        client: AppleMusicClient,
        result: dict[str, Any],
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
    ) -> ScoredRelease | None:
        """Build one record from an iTunes result and score it, the way a lookup does."""
        record = client._build_release_record(result)
        if record is None:
            return None
        scored = client.score_records([record], artist_norm, album_norm, artist_context)
        return scored[0] if scored else None

    def test_process_valid_result(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
        sample_itunes_result: dict[str, Any],
    ) -> None:
        """Test processing a valid iTunes result."""
        mock_score_func.return_value = 90.0

        result = TestProcessItunesResult.score_one(
            client,
            sample_itunes_result,
            "pink floyd",
            "dark side",
            artist_context=ArtistContext(),
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

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

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

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

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

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

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

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

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

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

        assert result is None

    def test_handles_scoring_error(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
    ) -> None:
        """A scoring error fails the lookup instead of dropping the release."""
        mock_score_func.side_effect = ValueError("Scoring error")
        result_data = {
            "artistName": "Artist",
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        with pytest.raises(ValueError, match="Scoring error"):
            TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

    def test_includes_optional_fields(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
        sample_itunes_result: dict[str, Any],
    ) -> None:
        """Test includes optional fields when present."""
        mock_score_func.return_value = 85.0

        result = TestProcessItunesResult.score_one(
            client,
            sample_itunes_result,
            "pink floyd",
            "dark side",
            artist_context=ArtistContext(),
        )

        assert result is not None
        assert result["label"] == "℗ 1973 Pink Floyd Records"
        assert result["album_type"] == "Album"

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

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

        assert result is not None


class TestReissueDetectionEdgeCases:
    """Tests for reissue detection with edge-case year values."""


class TestScoredReleaseStructure:
    """Tests for ScoredRelease structure returned by the client."""

    @pytest.mark.asyncio
    async def test_releases_are_scored_with_the_search_artist_context(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
        sample_itunes_result: dict[str, Any],
    ) -> None:
        """The artist's region and period reach the scorer for iTunes releases, as they do for the other providers."""
        mock_api_request_func.return_value = {"results": [sample_itunes_result]}
        mock_score_func.return_value = 85.0
        artist_context = ArtistContext(region="gb", period={"start_year": 1965, "end_year": 2014})

        await fetch_and_score(client, "pink floyd", "dark side", artist_context)

        mock_score_func.assert_called_once()
        assert mock_score_func.call_args.kwargs["artist_context"] is artist_context

    @pytest.mark.asyncio
    async def test_storefront_is_not_the_release_country(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
        sample_itunes_result: dict[str, Any],
    ) -> None:
        """The searched storefront says nothing about where a release came from, so no country reaches the scorer."""
        mock_api_request_func.return_value = {"results": [sample_itunes_result]}
        mock_score_func.return_value = 85.0

        results = await fetch_and_score(client, "pink floyd", "dark side", ArtistContext(region="us"))

        assert mock_score_func.call_args.kwargs["release"]["country"] is None
        assert results[0]["country"] is None

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

        results = await fetch_and_score(client, "pink floyd", "dark side", artist_context=ArtistContext())

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

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

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

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_empty_search_terms(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test handles empty search terms."""
        mock_api_request_func.return_value = {"results": []}

        result = await fetch_and_score(client, "", "", artist_context=ArtistContext())

        # Should make request with just space-stripped term
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_runtime_error(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """A RuntimeError fails the lookup."""
        mock_api_request_func.side_effect = RuntimeError("Runtime error")

        with pytest.raises(RuntimeError, match="Runtime error"):
            await fetch_and_score(client, "artist", "album", artist_context=ArtistContext())

    @pytest.mark.asyncio
    async def test_handles_value_error(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """A ValueError fails the lookup."""
        mock_api_request_func.side_effect = ValueError("Value error")

        with pytest.raises(ValueError, match="Value error"):
            await fetch_and_score(client, "artist", "album", artist_context=ArtistContext())


class TestGetArtistStartYear:
    """Tests for get_artist_start_year method."""

    @pytest.mark.asyncio
    async def test_returns_earliest_year(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test returns earliest release year for artist."""
        mock_api_request_func.return_value = {
            "results": [
                {"artistName": "Metallica", "releaseDate": "1991-08-12T00:00:00Z"},
                {"artistName": "Metallica", "releaseDate": "1983-07-25T00:00:00Z"},
                {"artistName": "Metallica", "releaseDate": "1986-03-03T00:00:00Z"},
            ]
        }

        result = await client.get_artist_start_year("metallica")

        assert result == 1983

    @pytest.mark.asyncio
    async def test_returns_none_when_no_results(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test returns None when no albums found."""
        mock_api_request_func.return_value = {"results": []}

        result = await client.get_artist_start_year("unknown artist")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_api_fails(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test returns None when API request fails."""
        mock_api_request_func.return_value = None

        result = await client.get_artist_start_year("metallica")

        assert result is None

    @pytest.mark.asyncio
    async def test_filters_by_artist_name(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test filters results by matching artist name."""
        mock_api_request_func.return_value = {
            "results": [
                {"artistName": "Metallica", "releaseDate": "1991-08-12T00:00:00Z"},
                {"artistName": "Other Artist", "releaseDate": "1970-01-01T00:00:00Z"},
                {"artistName": "Metallica Tribute", "releaseDate": "2000-01-01T00:00:00Z"},
            ]
        }

        result = await client.get_artist_start_year("metallica")

        # Should only consider "Metallica" and "Metallica Tribute" (contains metallica)
        assert result == 1991  # Earliest from matching artists

    @pytest.mark.asyncio
    async def test_skips_invalid_release_dates(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test skips results with invalid release dates."""
        mock_api_request_func.return_value = {
            "results": [
                {"artistName": "Metallica", "releaseDate": ""},
                {"artistName": "Metallica", "releaseDate": "invalid"},
                {"artistName": "Metallica", "releaseDate": "1991-08-12T00:00:00Z"},
            ]
        }

        result = await client.get_artist_start_year("metallica")

        assert result == 1991

    @pytest.mark.asyncio
    async def test_returns_none_when_all_dates_invalid(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test returns None when all release dates are invalid."""
        mock_api_request_func.return_value = {
            "results": [
                {"artistName": "Metallica", "releaseDate": ""},
                {"artistName": "Metallica", "releaseDate": "not-a-date"},
            ]
        }

        result = await client.get_artist_start_year("metallica")

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_api_error(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test handles API errors gracefully."""
        mock_api_request_func.side_effect = OSError("Connection error")

        result = await client.get_artist_start_year("metallica")

        assert result is None

    @pytest.mark.asyncio
    async def test_case_insensitive_artist_matching(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test artist name matching is case insensitive."""
        mock_api_request_func.return_value = {
            "results": [
                {"artistName": "METALLICA", "releaseDate": "1983-07-25T00:00:00Z"},
                {"artistName": "metallica", "releaseDate": "1991-08-12T00:00:00Z"},
            ]
        }

        result = await client.get_artist_start_year("Metallica")

        assert result == 1983

    @pytest.mark.asyncio
    async def test_makes_correct_api_call(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
    ) -> None:
        """Test makes correct API call with parameters."""
        mock_api_request_func.return_value = {"results": []}

        await client.get_artist_start_year("metallica")

        mock_api_request_func.assert_called_once()
        call_kwargs = mock_api_request_func.call_args[1]
        assert call_kwargs["params"]["term"] == "metallica"
        assert call_kwargs["params"]["entity"] == "album"
        assert call_kwargs["params"]["limit"] == "200"


class TestScoreFiltering:
    """Tests for score filtering - releases with score <= 0 should be filtered out.

    This was a bug fix: AppleMusicClient was returning releases with score=0,
    unlike MusicBrainz, Discogs, and Last.fm which all filter score <= 0.
    This caused albums with only iTunes results (score=0) to appear with 0% confidence.
    """

    def test_filters_out_zero_score(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
    ) -> None:
        """Test that releases with score=0 are filtered out."""
        mock_score_func.return_value = 0.0
        result_data = {
            "artistName": "Artist",
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

        assert result is None

    def test_filters_out_negative_score(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
    ) -> None:
        """Test that releases with negative score are filtered out."""
        mock_score_func.return_value = -5.0
        result_data = {
            "artistName": "Artist",
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

        assert result is None

    def test_allows_positive_score(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
    ) -> None:
        """Test that releases with positive score are returned."""
        mock_score_func.return_value = 50.0
        result_data = {
            "artistName": "Artist",
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

        assert result is not None
        assert result["score"] == 50.0

    def test_allows_small_positive_score(
        self,
        client: AppleMusicClient,
        mock_score_func: MagicMock,
    ) -> None:
        """Test that even very small positive scores are allowed."""
        mock_score_func.return_value = 0.01
        result_data = {
            "artistName": "Artist",
            "collectionName": "Album",
            "releaseDate": "2020-01-01T00:00:00Z",
        }

        result = TestProcessItunesResult.score_one(client, result_data, "artist", "album", artist_context=ArtistContext())

        assert result is not None
        assert result["score"] == 0.01

    @pytest.mark.asyncio
    async def test_filters_zero_scores(
        self,
        client: AppleMusicClient,
        mock_api_request_func: AsyncMock,
        mock_score_func: MagicMock,
    ) -> None:
        """Zero-score results are filtered out."""
        # Two results: one with score 50, one with score 0
        results = [
            {
                "artistName": "Good Artist",
                "collectionName": "Good Album",
                "releaseDate": "2020-01-01T00:00:00Z",
            },
            {
                "artistName": "Bad Match",
                "collectionName": "Wrong Album",
                "releaseDate": "2020-01-01T00:00:00Z",
            },
        ]
        mock_api_request_func.return_value = {"results": results}

        # First call returns 50, second returns 0
        mock_score_func.side_effect = [50.0, 0.0]

        result = await fetch_and_score(client, "good artist", "good album", artist_context=ArtistContext())

        # Only the first result should be returned
        assert len(result) == 1
        assert result[0]["title"] == "Good Album"
        assert result[0]["score"] == 50.0


class TestExtractYearFromResult:
    """Tests for _extract_year_from_result helper method."""

    def test_extracts_valid_year(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test extracts year from valid result."""
        result = {"artistName": "Metallica", "releaseDate": "1991-08-12T00:00:00Z"}

        year = client._extract_year_from_result(result, "metallica")

        assert year == 1991

    def test_returns_none_for_non_matching_artist(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test returns None when artist doesn't match."""
        result = {"artistName": "Iron Maiden", "releaseDate": "1980-04-14T00:00:00Z"}

        year = client._extract_year_from_result(result, "metallica")

        assert year is None

    def test_returns_none_for_empty_release_date(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test returns None when release date is empty."""
        result = {"artistName": "Metallica", "releaseDate": ""}

        year = client._extract_year_from_result(result, "metallica")

        assert year is None

    def test_returns_none_for_invalid_year_format(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test returns None when year format is invalid."""
        result = {"artistName": "Metallica", "releaseDate": "91-08-12T00:00:00Z"}

        year = client._extract_year_from_result(result, "metallica")

        assert year is None

    def test_handles_partial_artist_match(
        self,
        client: AppleMusicClient,
    ) -> None:
        """Test handles partial artist name matches."""
        result = {"artistName": "Metallica & Friends", "releaseDate": "2000-01-01T00:00:00Z"}

        year = client._extract_year_from_result(result, "metallica")

        assert year == 2000


class TestUnreadableResults:
    """A result iTunes sends in an unexpected shape is skipped, and the other results still become records."""

    def test_unreadable_result_is_skipped_with_a_warning(self, client: AppleMusicClient, sample_itunes_result: dict[str, Any]) -> None:
        """A result whose artist name is not text is skipped and named in the error log."""
        mock_error_logger = MagicMock(spec=logging.Logger)
        client.error_logger = mock_error_logger

        records = client._build_release_records([{**sample_itunes_result, "artistName": 123}, sample_itunes_result], "pink floyd dark side")

        assert [record["title"] for record in records] == [sample_itunes_result["collectionName"]]
        mock_error_logger.warning.assert_called_once()
        assert "Skipping unreadable result" in mock_error_logger.warning.call_args[0][0]


class TestRequestFailurePropagation:
    """A failed request fails the Apple Music lookup."""

    @pytest.mark.asyncio
    async def test_failed_search_fails_the_lookup(self, client: AppleMusicClient, mock_api_request_func: AsyncMock) -> None:
        """A search that failed must fail the lookup, not read as "nothing found"."""
        mock_api_request_func.side_effect = ApiRequestError("itunes", "https://itunes/search", "failed")

        with pytest.raises(ApiRequestError):
            await fetch_and_score(client, "artist", "album", artist_context=ArtistContext())


class TestSharedRecordShape:
    """iTunes records carry the same release fields as the MusicBrainz and Discogs records."""

    @pytest.mark.asyncio
    async def test_records_have_the_shared_release_fields(self, sample_itunes_result: dict[str, Any]) -> None:
        client = AppleMusicClient(
            console_logger=logging.getLogger("test.itunes.console"),
            error_logger=logging.getLogger("test.itunes.error"),
            make_api_request_func=AsyncMock(return_value={"results": [sample_itunes_result]}),
            score_release_func=MagicMock(return_value=50),
            analytics=MagicMock(),
        )

        [record] = await client.fetch_release_records("pink floyd", "the dark side of the moon")

        assert ReleaseRecord.__required_keys__ <= record.keys()
        assert (record["label"], record["source"], record["genre"]) == ("℗ 1973 Pink Floyd Records", "itunes", "Rock")


class TestRecordsAndScoring:
    """iTunes fetches release records without the artist context or the clock, and scores them with both."""

    @staticmethod
    def create_client(score_release: MagicMock, response: dict[str, Any]) -> tuple[AppleMusicClient, AsyncMock]:
        """Create a client answering one search."""
        request = AsyncMock(return_value=response)
        client = AppleMusicClient(
            console_logger=logging.getLogger("test.itunes.console"),
            error_logger=logging.getLogger("test.itunes.error"),
            make_api_request_func=request,
            score_release_func=score_release,
            analytics=MagicMock(),
        )
        return client, request

    @pytest.mark.asyncio
    async def test_records_score_with_each_context_without_a_new_request(self, sample_itunes_result: dict[str, Any]) -> None:
        """One fetch serves any artist context: scoring asks iTunes nothing and still follows the context."""

        def score_release(release: dict[str, Any], artist_norm: str, album_norm: str, artist_context: ArtistContext, source: str) -> int:
            """Score higher when the artist region is known."""
            del release, artist_norm, album_norm, source
            return 80 if artist_context.region else 60

        client, request = self.create_client(MagicMock(side_effect=score_release), {"results": [sample_itunes_result]})

        records = await client.fetch_release_records("pink floyd", "the dark side of the moon")
        requests_made = request.await_count
        with_region = client.score_records(records, "pink floyd", "the dark side of the moon", ArtistContext(region="gb"))
        without_region = client.score_records(records, "pink floyd", "the dark side of the moon", ArtistContext())

        assert request.await_count == requests_made
        assert [release["score"] for release in with_region] == [80]
        assert [release["score"] for release in without_region] == [60]

    @pytest.mark.asyncio
    async def test_a_recent_release_is_not_marked_as_a_reissue(self, sample_itunes_result: dict[str, Any]) -> None:
        """A new album on iTunes is not a reissue just because it is recent; the scorer judges reissues by title."""
        score_release = MagicMock(return_value=50)
        this_year = datetime.now(UTC).year
        client, _ = self.create_client(score_release, {"results": [{**sample_itunes_result, "releaseDate": f"{this_year}-01-01T00:00:00Z"}]})
        records = await client.fetch_release_records("pink floyd", "the dark side of the moon")

        client.score_records(records, "pink floyd", "the dark side of the moon", ArtistContext())

        assert "is_reissue" not in score_release.call_args.kwargs["release"]

    @pytest.mark.asyncio
    async def test_broken_fetch_propagates(self) -> None:
        """An error while fetching is a failed provider, not an album iTunes does not know."""
        client, _ = self.create_client(MagicMock(return_value=50), {})
        client.make_api_request_func = AsyncMock(side_effect=ValueError("bad payload"))

        with pytest.raises(ValueError, match="bad payload"):
            await client.fetch_release_records("artist", "album")


class TestMalformedAnswers:
    """iTunes answers an empty search with an empty list, so a body without the list is a failure, not "nothing found"."""

    @pytest.mark.asyncio
    async def test_search_without_results_fails(self, client: AppleMusicClient, mock_api_request_func: AsyncMock) -> None:
        """A search body missing "results" fails the lookup instead of being cached as empty."""
        mock_api_request_func.return_value = {"resultCount": 0}

        with pytest.raises(ApiRequestError):
            await client.fetch_release_records("artist", "album")

    @pytest.mark.asyncio
    async def test_lookup_fallback_requires_results(self, client: AppleMusicClient, mock_api_request_func: AsyncMock) -> None:
        """The artist search and the album lookup of the fallback reject an answer without a results list."""
        mock_api_request_func.return_value = {"resultCount": 0}

        with pytest.raises(ApiRequestError):
            await client._find_artist_id("artist")
        with pytest.raises(ApiRequestError):
            await client._lookup_artist_albums(1)


class TestParityWithOtherProviders:
    """iTunes behaves like the MusicBrainz and Discogs clients where they overlap."""

    @pytest.mark.asyncio
    async def test_release_search_is_tracked(self, console_logger: logging.Logger, error_logger: logging.Logger) -> None:
        analytics = MockAnalytics()
        client = AppleMusicClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=AsyncMock(return_value={"resultCount": 0, "results": []}),
            score_release_func=MagicMock(return_value=0.0),
            analytics=analytics,
        )

        with patch.object(Analytics, "execute_async_wrapped_call", autospec=True, side_effect=Analytics.execute_async_wrapped_call) as tracked:
            await client.fetch_release_records("pink floyd", "animals")

        assert tracked.call_args.args[2] == "itunes_release_search"

    @pytest.mark.asyncio
    async def test_artist_start_year_lookup_is_tracked(self, console_logger: logging.Logger, error_logger: logging.Logger) -> None:
        analytics = MockAnalytics()
        client = AppleMusicClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=AsyncMock(return_value={"resultCount": 0, "results": []}),
            score_release_func=MagicMock(return_value=0.0),
            analytics=analytics,
        )

        with patch.object(Analytics, "execute_async_wrapped_call", autospec=True, side_effect=Analytics.execute_async_wrapped_call) as tracked:
            await client.get_artist_start_year("pink floyd")

        assert tracked.call_args.args[2] == "itunes_artist_period"

    def test_a_year_before_1900_is_not_a_release_year(self, client: AppleMusicClient, sample_itunes_result: dict[str, Any]) -> None:
        sample_itunes_result["releaseDate"] = "1850-01-01T00:00:00Z"

        assert client._build_release_record(sample_itunes_result) is None


class TestArtistIdMatch:
    """The artist lookup matches the search name whatever its case, as the library spells it."""

    @pytest.mark.asyncio
    async def test_mixed_case_artist_is_found(self, console_logger: logging.Logger, error_logger: logging.Logger) -> None:
        client = AppleMusicClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=AsyncMock(return_value={"results": [{"artistName": "Pink Floyd", "artistId": 487143}]}),
            score_release_func=MagicMock(return_value=0.0),
            analytics=MagicMock(),
        )

        assert await client._find_artist_id("Pink Floyd") == 487143
