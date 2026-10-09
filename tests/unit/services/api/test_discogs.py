"""Enhanced Discogs API client tests with Allure reporting."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest

from services.api.discogs import DiscogsClient, DiscogsRelease
from services.api.request_executor import ApiRequestError
from services.api.year_scoring import ArtistContext
from tests.factories import create_test_app_config
from tests.mocks.csv_mock import MockAnalytics, MockLogger


class TestDiscogsClientAllure:
    """Enhanced tests for DiscogsClient with Allure reporting."""

    @staticmethod
    def create_discogs_client(
        mock_api_request: AsyncMock | None = None,
        mock_score_release: MagicMock | None = None,
    ) -> DiscogsClient:
        """Create a DiscogsClient instance for testing."""
        if mock_api_request is None:
            mock_api_request = AsyncMock(return_value={"results": []})

        if mock_score_release is None:
            mock_score_release = MagicMock(return_value=0.85)

        test_api_token = "test_token"  # noqa: S105
        app_config = create_test_app_config()
        return DiscogsClient(
            token=test_api_token,
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=MockAnalytics(),
            make_api_request_func=mock_api_request,
            score_release_func=mock_score_release,
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
        result = await client.get_scored_releases("The Beatles", "Abbey Road", ArtistContext(region="US"))
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
        result = await client.get_scored_releases("NonExistentArtist123", "NonExistentAlbum456", ArtistContext())
        assert result == []

    @pytest.mark.asyncio
    async def test_get_release_year(self) -> None:
        """Test release year retrieval."""
        mock_response = TestDiscogsClientAllure.create_mock_discogs_response()
        mock_response["results"][0]["year"] = 1969  # Set specific year
        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        releases = await client.get_scored_releases("test artist", "test album", ArtistContext())
        assert [release["year"] for release in releases] == ["1969"]

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
        await client.get_scored_releases("Test Artist", "Test Album", ArtistContext())

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
        result = await client.get_scored_releases("Test Artist", "Test Album", ArtistContext())
        # Client should handle quota exceeded gracefully
        assert result == []

    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """Test timeout handling."""
        # Mock API request that returns None (timeout)
        mock_api_request = AsyncMock(return_value=None)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        result = await client.get_scored_releases("Test Artist", "Test Album", ArtistContext())
        # Client should handle timeouts gracefully
        assert result == []

    @staticmethod
    def create_pressing(release_id: int, master_id: int | None) -> DiscogsRelease:
        """Create one 'Test Artist - Test Album' search result that scoring keeps."""
        return {
            "id": release_id,
            "title": "Test Artist - Test Album",
            "year": 2020,
            "type": "release",
            "formats": [],
            "genre": [],
            "style": [],
            "label": [],
            "resource_url": "",
            "uri": "",
            "master_id": master_id,
            "master_url": None,
        }

    @pytest.mark.asyncio
    async def test_release_outside_any_master_skips_master_fetch(self) -> None:
        """Discogs gives master_id 0 to a release outside any master, so no master is requested for it."""
        mock_api_request = AsyncMock(return_value=None)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        pressing = TestDiscogsClientAllure.create_pressing(1, master_id=0)

        records = await client._process_discogs_results([pressing], "test artist")

        assert len(records) == 1
        mock_api_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_primary_search_success_no_fallback(self) -> None:
        """Test that fallback is not called when primary search succeeds."""
        # Create response without master_id to avoid additional API calls
        mock_response = TestDiscogsClientAllure.create_mock_discogs_response()
        mock_response["results"][0]["master_id"] = None  # No master release lookup
        mock_response["results"][0]["master_url"] = None

        mock_api_request = AsyncMock(return_value=mock_response)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)
        result = await client.get_scored_releases("Test Artist", "Test Album", ArtistContext())

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
        result = await client.get_scored_releases("Test Artist", "Test Album", ArtistContext())

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
        result = await client.get_scored_releases("Test Artist", "Test Album", ArtistContext())

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
        await client.get_scored_releases(artist, album, ArtistContext())

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

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_fetch_release_details_error_propagates(self) -> None:
        """A broken detail fetch fails the lookup, so a record without its year is never cached."""
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=AsyncMock(side_effect=OSError("connection reset")))

        with pytest.raises(OSError, match="connection reset"):
            await client._fetch_discogs_release_details(99999)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_fetch_master_release_year_error_propagates(self) -> None:
        """A broken master fetch fails the lookup, so a record without its master year is never cached."""
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=AsyncMock(side_effect=RuntimeError("unexpected failure")))

        with pytest.raises(RuntimeError, match="unexpected failure"):
            await client._fetch_master_release_year(12345)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("year", [None, "", "unknown"])
    async def test_master_without_a_numeric_year_has_no_year(self, year: str | None) -> None:
        """A master whose year is missing or not a number gives no year, the same on every lookup."""
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=AsyncMock(return_value={"id": 12345, "year": year}))

        assert await client._fetch_master_release_year(12345) is None

    @pytest.mark.asyncio
    @pytest.mark.unit
    @pytest.mark.parametrize("body", [{"message": "You must authenticate"}, {"pagination": {}}], ids=["message", "no-results"])
    async def test_execute_search_error_body_fails_the_lookup(self, body: dict[str, Any]) -> None:
        """A successful status with an error-shaped body is no answer, so it fails the lookup instead of reading as "nothing found"."""
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=AsyncMock(return_value=body))

        with pytest.raises(ApiRequestError):
            await client._execute_search({"artist": "Test", "release_title": "Album", "type": "release"}, "Test strategy")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_fetch_missing_year_details_fills_year_from_detail(self) -> None:
        detail_response = {"year": 2005, "released": "2005-03-15"}
        mock_api_request = AsyncMock(return_value=detail_response)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)

        item: DiscogsRelease = {"id": 555, "year": "", "title": "Artist - Album"}
        year_str, detail_count = await client._fetch_missing_year_details(item, 0, 10)

        assert year_str == "2005"
        assert detail_count == 1
        assert isinstance(client.console_logger, MockLogger)
        assert any("Filled missing year via detail fetch: 2005" in msg for msg in client.console_logger.debug_messages)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_process_search_result_artist_mismatch_skips(self) -> None:
        mock_api_request = AsyncMock(return_value=None)
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=mock_api_request)

        item: DiscogsRelease = {
            "id": 100,
            "title": "Completely Different Artist - Some Album",
            "year": 2020,
            "type": "release",
            "formats": [],
            "genre": [],
            "style": [],
            "label": [],
            "resource_url": "",
            "uri": "",
            "master_id": None,
            "master_url": None,
        }

        scored, _detail_count = await client._process_single_discogs_item(
            item,
            "target artist",
            detail_fetch_count=0,
            detail_fetch_limit=10,
        )

        assert scored is None
        assert isinstance(client.console_logger, MockLogger)
        assert any("Skipping" in msg and "artist mismatch" in msg for msg in client.console_logger.debug_messages)


class TestRequestFailurePropagation:
    """A failed request fails the Discogs lookup."""

    @pytest.mark.asyncio
    async def test_failed_search_fails_the_lookup(self) -> None:
        """A search that failed must fail the lookup instead of reading as "nothing found"."""
        request = AsyncMock(side_effect=ApiRequestError("discogs", "https://discogs/search", "failed"))
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=request)

        with pytest.raises(ApiRequestError):
            await client.get_scored_releases("artist", "album", ArtistContext())


class TestRecordsAndScoring:
    """Discogs fetches release records without the artist context and scores them with it."""

    @staticmethod
    def create_region_aware_client(request: AsyncMock) -> DiscogsClient:
        """Create a client whose score depends on the artist region, so a stale score would show."""

        def score_release(release: dict[str, Any], artist_norm: str, album_norm: str, artist_context: ArtistContext, source: str = "unknown") -> int:
            """Score higher when the artist region is known."""
            del release, artist_norm, album_norm, source
            return 80 if artist_context.region else 60

        return TestDiscogsClientAllure.create_discogs_client(mock_api_request=request, mock_score_release=MagicMock(side_effect=score_release))

    @staticmethod
    def create_search_without_master() -> AsyncMock:
        """Answer one search with a release outside any master."""
        response = TestDiscogsClientAllure.create_mock_discogs_response()
        response["results"][0]["master_id"] = 0
        return AsyncMock(return_value=response)

    @pytest.mark.asyncio
    async def test_records_score_with_each_context_without_a_new_request(self) -> None:
        """One fetch serves any artist context: scoring asks Discogs nothing and still follows the context."""
        request = self.create_search_without_master()
        client = self.create_region_aware_client(request)

        records = await client.fetch_release_records("test artist", "test album")
        requests_made = request.await_count
        with_region = client.score_records(records, "test artist", "test album", ArtistContext(region="us"))
        without_region = client.score_records(records, "test artist", "test album", ArtistContext())

        assert request.await_count == requests_made
        assert [release["score"] for release in with_region] == [80]
        assert [release["score"] for release in without_region] == [60]
        assert all("is_reissue" not in release and "releasegroup_first_date" not in release for release in with_region)

    @pytest.mark.asyncio
    async def test_scored_releases_match_records_then_scores(self) -> None:
        """get_scored_releases is the fetched records scored with the given context."""
        client = self.create_region_aware_client(self.create_search_without_master())
        records = await client.fetch_release_records("test artist", "test album")

        scored = await client.get_scored_releases("test artist", "test album", ArtistContext(region="us"))

        assert scored == client.score_records(records, "test artist", "test album", ArtistContext(region="us"))

    @pytest.mark.asyncio
    async def test_broken_fetch_propagates(self) -> None:
        """An error while fetching is a failed provider, not an album Discogs does not know."""
        client = TestDiscogsClientAllure.create_discogs_client(mock_api_request=AsyncMock(side_effect=ValueError("bad payload")))

        with pytest.raises(ValueError, match="bad payload"):
            await client.fetch_release_records("artist", "album")

    def test_client_keeps_no_cache_of_its_own(self) -> None:
        """Discogs relies on the shared caches like the other providers, so it takes no cache service."""
        assert "cache_service" not in inspect.signature(DiscogsClient).parameters

    @pytest.mark.asyncio
    async def test_reissue_keywords_apply_at_scoring_time(self) -> None:
        """Records are kept for good, so the reissue keywords in force when scoring decide the flag, not those at fetch time."""
        response = TestDiscogsClientAllure.create_mock_discogs_response(album_name="Test Album (Remastered)")
        response["results"][0]["master_id"] = 0
        seen: list[dict[str, Any]] = []

        def score_release(release: dict[str, Any], *_args: Any, **_kwargs: Any) -> int:
            """Record what the scorer was given."""
            seen.append(release)
            return 50

        client = TestDiscogsClientAllure.create_discogs_client(
            mock_api_request=AsyncMock(return_value=response), mock_score_release=MagicMock(side_effect=score_release)
        )
        with patch.object(client, "_get_reissue_keywords", return_value=[]):
            records = await client.fetch_release_records("test artist", "test album")
        with patch.object(client, "_get_reissue_keywords", return_value=["remaster"]):
            client.score_records(records, "test artist", "test album", ArtistContext())

        assert seen[0].get("is_reissue") is True
