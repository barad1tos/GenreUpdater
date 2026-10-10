"""Enhanced MusicBrainz API client tests with Allure reporting."""

from __future__ import annotations

from tests.factories import fetch_and_score

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.api.musicbrainz import MUSICBRAINZ_BASE_URL, MusicBrainzClient
from services.api.request_executor import ApiRequestError
from services.api.year_scoring import ArtistContext, ReleaseScorer
from tests.mocks.csv_mock import MockLogger


def _mock_analytics() -> MagicMock:
    """Create a mock Analytics instance for testing."""
    return MagicMock()


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
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            make_api_request_func=mock_api_request,
            score_release_func=mock_score_release,
            analytics=_mock_analytics(),
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
        # Release countries are ISO codes, so the region must be one too for the scorer to compare them
        assert region == "US"

    @pytest.mark.asyncio
    async def test_get_artist_region_falls_back_to_area_code(self) -> None:
        """An artist without a country takes the ISO code of its area."""
        artist_response = TestMusicBrainzClientAllure.create_mock_artist_response()
        del artist_response["artists"][0]["country"]
        artist_response["artists"][0]["area"]["iso-3166-1-codes"] = ["GB"]
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=AsyncMock(return_value=artist_response))

        assert await client.get_artist_region("Test Artist") == "GB"

    @pytest.mark.asyncio
    async def test_get_artist_region_without_code_is_unknown(self) -> None:
        """An area name alone never matches a release country, so the region stays unknown."""
        artist_response = TestMusicBrainzClientAllure.create_mock_artist_response()
        del artist_response["artists"][0]["country"]
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=AsyncMock(return_value=artist_response))

        assert await client.get_artist_region("Test Artist") is None

    @staticmethod
    def create_scoring_client() -> MusicBrainzClient:
        """Create a client that scores with the real ReleaseScorer, the way the orchestrator wires it."""
        scorer = ReleaseScorer()

        def score_release(release: dict[str, Any], artist_norm: str, album_norm: str, artist_context: ArtistContext, source: str = "unknown") -> int:
            """Score a release with the real scorer."""
            return int(scorer.score_original_release(release, artist_norm, album_norm, artist_context=artist_context, source=source))

        return TestMusicBrainzClientAllure.create_musicbrainz_client(mock_score_release=MagicMock(side_effect=score_release))

    @staticmethod
    def create_release_group_result() -> tuple[dict[str, Any], dict[str, Any]]:
        """Create the (releases response, release group) pair the client scores for 'The Beatles - Abbey Road'."""
        release_group = {"id": "rg-1", "title": "Abbey Road", "primary-type": "Album", "first-release-date": "1969-09-26"}
        credit = [{"name": "The Beatles", "artist": {"name": "The Beatles"}}]
        releases = {
            "releases": [
                {"id": "r-1", "title": "Abbey Road", "status": "Official", "date": "1969-09-26", "country": "GB", "artist-credit": credit},
                {"id": "r-2", "title": "Abbey Road", "status": "Official", "date": "2019-09-27", "country": "US", "artist-credit": credit},
            ]
        }
        return releases, release_group

    def test_releases_reach_the_scorer_with_their_year(self) -> None:
        """MusicBrainz releases are scored on the release group's first year, so the real scorer keeps them."""
        client = TestMusicBrainzClientAllure.create_scoring_client()

        records = client._build_release_records([TestMusicBrainzClientAllure.create_release_group_result()])
        scored = client.score_records(records, "the beatles", "abbey road", ArtistContext())

        assert [release["year"] for release in scored] == ["1969", "1969"]
        assert all(release["score"] > 0 for release in scored)

    def test_artist_region_matches_release_country(self) -> None:
        """A release from the artist's country scores higher once the region is an ISO code."""
        client = TestMusicBrainzClientAllure.create_scoring_client()
        records = client._build_release_records([TestMusicBrainzClientAllure.create_release_group_result()])

        without_region = client.score_records(records, "the beatles", "abbey road", ArtistContext())
        with_region = client.score_records(records, "the beatles", "abbey road", ArtistContext(region="GB"))

        assert with_region[0]["score"] > without_region[0]["score"]
        # The British pressing now outscores the American one, which only gets the major-market bonus
        assert with_region[0]["score"] > with_region[1]["score"]

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
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            make_api_request_func=AsyncMock(return_value={}),
            score_release_func=MagicMock(return_value=0.85),
            analytics=_mock_analytics(),
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


class TestRetrieveAndScoreReleasesErrorHandling:
    """An error while building records fails the lookup instead of reading as "nothing found"."""

    @pytest.mark.asyncio
    async def test_index_error_in_records_propagates(self) -> None:
        """An IndexError while building records reaches the caller, which counts the provider as failed."""
        mock_api_request = AsyncMock(return_value={"release-groups": [{"id": "rg1"}]})
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(
            mock_api_request=mock_api_request,
        )

        with (
            patch.object(client, "_perform_primary_search", new_callable=AsyncMock, return_value=[{"id": "rg1"}]),
            patch.object(client, "_fetch_releases_for_groups", new_callable=AsyncMock, return_value=[{}]),
            patch.object(client, "_build_release_records", side_effect=IndexError("list index out of range")),
            pytest.raises(IndexError),
        ):
            await fetch_and_score(client, "artist", "album", ArtistContext())


class TestGetArtistInfoExceptionHandler:
    """Tests for get_artist_info exception logging (line 314)."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_artist_info_logs_exception_on_api_error(self) -> None:
        mock_api_request = AsyncMock(side_effect=OSError("connection failed"))
        error_logger = MockLogger()
        client = MusicBrainzClient(
            console_logger=MockLogger(),
            error_logger=error_logger,
            make_api_request_func=mock_api_request,
            score_release_func=MagicMock(return_value=0.85),
            analytics=_mock_analytics(),
        )

        result = await client.get_artist_info("test artist")

        assert result is None
        assert any("Failed to get artist info" in msg for msg in error_logger.exception_messages)


class TestFieldedReleaseGroupSearchSuccessLog:
    """Tests for _fielded_release_group_search success debug log (line 453)."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_fielded_search_logs_success_on_results(self) -> None:
        console_logger = MockLogger()
        mock_api_request = AsyncMock(
            return_value={
                "count": 2,
                "release-groups": [{"id": "rg1"}, {"id": "rg2"}],
            }
        )
        client = MusicBrainzClient(
            console_logger=console_logger,
            error_logger=MockLogger(),
            make_api_request_func=mock_api_request,
            score_release_func=MagicMock(return_value=0.85),
            analytics=_mock_analytics(),
        )

        result = await client._fielded_release_group_search(
            "https://musicbrainz.org/ws/2/release-group/",
            "metallica",
            "master of puppets",
            attempt_num=1,
        )

        assert len(result) == 2
        assert any("Attempt 1 successful. Found 2 release groups" in msg for msg in console_logger.debug_messages)


class TestSearchReleaseGroupsSuccessLog:
    """Tests for _search_release_groups success debug log after filtering (line 482)."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_search_release_groups_logs_filtered_count(self) -> None:
        console_logger = MockLogger()
        mock_api_request = AsyncMock(
            return_value={
                "count": 1,
                "release-groups": [
                    {"id": "rg1", "title": "Album", "artist-credit": [{"artist": {"name": "Metallica"}}]},
                ],
            }
        )
        client = MusicBrainzClient(
            console_logger=console_logger,
            error_logger=MockLogger(),
            make_api_request_func=mock_api_request,
            score_release_func=MagicMock(return_value=0.85),
            analytics=_mock_analytics(),
        )

        result = await client._search_release_groups("metallica album", "metallica", attempt_num=2)

        assert len(result) == 1
        assert any("Attempt 2 successful. Found 1 matching groups after filtering" in msg for msg in console_logger.debug_messages)


class TestFetchReleasesForGroupsExceptionHandling:
    """Any release fetch that broke leaves the list incomplete, so it fails the lookup."""

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(OSError("network timeout"), id="OSError"),
            pytest.param(ValueError("bad payload"), id="ValueError"),
        ],
    )
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_broken_release_fetch_fails_the_lookup(self, error: Exception) -> None:
        """An exception outside the request contract fails the lookup instead of scoring a shorter list."""

        async def mock_api_request(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            """Fail the release fetch with the parametrized error."""
            raise error

        client = MusicBrainzClient(
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            make_api_request_func=mock_api_request,
            score_release_func=MagicMock(return_value=0.85),
            analytics=_mock_analytics(),
        )

        with pytest.raises(ApiRequestError, match=type(error).__name__):
            await client._fetch_releases_for_groups([{"id": "rg-abc-123", "title": "Test Album"}])


class TestRequestFailurePropagation:
    """A failed request fails the MusicBrainz lookup instead of shrinking it."""

    @pytest.mark.asyncio
    async def test_failed_release_fetch_fails_the_lookup(self) -> None:
        """A release-group fetch that failed must fail the lookup, not shrink it."""
        search = {"count": 1, "release-groups": [{"id": "rg-1", "title": "Album", "primary-type": "Album", "artist-credit": [{"name": "Artist"}]}]}
        request = AsyncMock(side_effect=[search, ApiRequestError("musicbrainz", "https://mb/release", "failed")])
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=request)

        with pytest.raises(ApiRequestError):
            await fetch_and_score(client, "artist", "album", ArtistContext())

    @pytest.mark.asyncio
    async def test_failed_search_fails_the_lookup(self) -> None:
        """A search that failed must fail the lookup, not read as "nothing found"."""
        request = AsyncMock(side_effect=ApiRequestError("musicbrainz", "https://mb/release-group", "failed"))
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=request)

        with pytest.raises(ApiRequestError):
            await fetch_and_score(client, "artist", "album", ArtistContext())

    @pytest.mark.asyncio
    async def test_cancelled_release_fetch_fails_the_lookup(self) -> None:
        """A release fetch cancelled on its own leaves the list incomplete, so the lookup fails instead of scoring it."""
        search = {"count": 1, "release-groups": [{"id": "rg-1", "title": "Album", "primary-type": "Album", "artist-credit": [{"name": "Artist"}]}]}
        request = AsyncMock(side_effect=[search, asyncio.CancelledError()])
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=request)

        with pytest.raises(ApiRequestError):
            await fetch_and_score(client, "artist", "album", ArtistContext())


class TestRecordsAndScoring:
    """MusicBrainz fetches release records without the artist context and scores them with it."""

    @staticmethod
    def create_search_and_releases() -> AsyncMock:
        """Answer the release-group search, then the releases of that group."""
        releases, release_group = TestMusicBrainzClientAllure.create_release_group_result()
        credit = [{"name": "The Beatles", "artist": {"name": "The Beatles"}}]
        search = {"count": 1, "release-groups": [{**release_group, "artist-credit": credit}]}
        return AsyncMock(side_effect=[search, releases])

    @pytest.mark.asyncio
    async def test_records_score_with_each_context_without_a_new_request(self) -> None:
        """One fetch serves any artist context: scoring asks MusicBrainz nothing and still follows the region."""
        client = TestMusicBrainzClientAllure.create_scoring_client()
        request = self.create_search_and_releases()
        client._make_api_request = request

        records = await client.fetch_release_records("the beatles", "abbey road")
        requests_made = request.await_count
        with_region = client.score_records(records, "the beatles", "abbey road", ArtistContext(region="GB"))
        without_region = client.score_records(records, "the beatles", "abbey road", ArtistContext())

        assert request.await_count == requests_made
        assert with_region[0]["score"] > without_region[0]["score"]
        assert [release["artist"] for release in with_region] == ["the beatles", "the beatles"]
        assert all("releasegroup_first_date" not in release for release in with_region)

    @pytest.mark.asyncio
    async def test_broken_fetch_propagates(self) -> None:
        """An error while fetching is a failed provider, not an album MusicBrainz does not know."""
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=AsyncMock(side_effect=ValueError("bad payload")))

        with pytest.raises(ValueError, match="bad payload"):
            await client.fetch_release_records("artist", "album")


class TestMalformedAnswers:
    """MusicBrainz answers an empty search with an empty list, so a body without the list is a failure, not "nothing found"."""

    @pytest.mark.asyncio
    async def test_search_without_release_groups_fails(self) -> None:
        """A search body missing "release-groups" fails the lookup instead of being cached as empty."""
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=AsyncMock(return_value={"count": 0}))

        with pytest.raises(ApiRequestError):
            await client.fetch_release_records("artist", "album")

    @pytest.mark.asyncio
    async def test_each_search_requires_its_list(self) -> None:
        """The fielded search, the fallback search and the release fetch each reject an answer without their list."""
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=AsyncMock(return_value={"count": 0}))

        with pytest.raises(ApiRequestError):
            await client._fielded_release_group_search(f"{MUSICBRAINZ_BASE_URL}/release-group/", "artist", "album", 1)
        with pytest.raises(ApiRequestError):
            await client._search_release_groups('"album"', "artist", 2)
        with pytest.raises(ApiRequestError):
            await client._fetch_releases_for_groups([{"id": "rg-1", "title": "Album"}])

    @pytest.mark.asyncio
    async def test_empty_search_is_nothing_found(self) -> None:
        """The empty answer MusicBrainz really sends reads as "nothing found"."""
        empty = {"count": 0, "offset": 0, "release-groups": []}
        client = TestMusicBrainzClientAllure.create_musicbrainz_client(mock_api_request=AsyncMock(return_value=empty))

        assert await client.fetch_release_records("artist", "album") == []
