"""iTunes Search API client for retrieving album release information.

This module provides access to Apple's iTunes Search API, which offers:
- Album release dates and metadata
- Artist information
- No authentication required (public API)
- Useful for new releases that may not be in other databases yet

The iTunes Search API is particularly valuable for:
- Recent releases (Apple gets data directly from labels)
- Albums available on Apple Music/iTunes Store
- Official release dates and metadata validation

API Reference: https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from core.analytics_decorator import track_instance_method
from core.models.normalization import normalize_for_matching
from services.api.api_base import BaseApiClient
from services.api.request_executor import ApiRequestError

if TYPE_CHECKING:
    from core.models.release_record import ReleaseRecord
    import logging

    from metrics.analytics import Analytics
    from services.api.api_base import ScoredRelease
    from services.api.year_scoring import ArtistContext
    from collections.abc import Callable, Coroutine

# iTunes Search API base URL
ITUNES_BASE_URL: str = "https://itunes.apple.com"


def _results_of(response: dict[str, Any], url: str) -> list[dict[str, Any]]:
    """Return the results list of an iTunes answer, which iTunes always sends, empty when nothing matched.

    Args:
        response: The parsed answer
        url: The request URL, for the error

    Returns:
        The results list

    Raises:
        ApiRequestError: The answer has no results list, so it is not an answer to the query
    """
    results = response.get("results")
    if not isinstance(results, list):
        api_name, reason = "itunes", "answer without a results list"
        raise ApiRequestError(api_name, url, reason)
    return results


class AppleMusicClient(BaseApiClient):
    """Client for iTunes Search API operations.

    Provides album search and metadata retrieval using Apple's public iTunes Search API.
    No authentication required - this is a public API service.

    Args:
        console_logger: Logger for console output
        error_logger: Logger for error messages
        make_api_request_func: Injected function for making API requests
        score_release_func: Injected function for scoring releases
        analytics: Analytics service for performance tracking
        country_code: Country code for search results (default: US)
        entity: Type of content to search for (default: album)
        limit: Maximum number of results to return (default: 50)

    """

    def __init__(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        make_api_request_func: Callable[..., Coroutine[Any, Any, dict[str, Any] | None]],
        score_release_func: Callable[..., float],
        analytics: Analytics,
        *,
        country_code: str = "US",
        entity: str = "album",
        limit: int = 50,
    ) -> None:
        super().__init__(console_logger, error_logger)
        self.analytics = analytics
        self.make_api_request_func = make_api_request_func
        self.score_release_func = score_release_func

        # iTunes Search API configuration
        self.base_url = f"{ITUNES_BASE_URL}/search"
        self.country_code = country_code
        self.entity = entity
        # Ensure limit is positive and within iTunes API bounds [1, 200]
        validated_limit = max(1, limit)
        self.limit = min(validated_limit, 200)

        self.console_logger.debug(
            "iTunes Search API client initialized (country=%s, entity=%s, limit=%d)",
            self.country_code,
            self.entity,
            self.limit,
        )

    @track_instance_method("itunes_release_search")
    async def fetch_release_records(self, artist_norm: str, album_norm: str) -> list[ReleaseRecord]:
        """Fetch iTunes release records for an album, falling back to the artist's albums when the search finds nothing.

        Any error propagates: a failed lookup must not read as an album iTunes does not know.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name

        Returns:
            Release records, empty when iTunes has nothing for the album
        """
        # iTunes Search works best with "artist album"; aiohttp encodes the parameters
        search_term = f"{artist_norm} {album_norm}".strip()
        params = {
            "term": search_term,
            "country": self.country_code,
            "entity": self.entity,
            "limit": str(self.limit),
        }
        self.console_logger.debug("[itunes] Searching for: '%s' (country=%s)", search_term, self.country_code)
        response_data = await self.make_api_request_func(
            api_name="itunes",
            url=self.base_url,
            params=params,
            max_retries=2,
            base_delay=0.5,
        )

        # Search results, else the artist's albums from the lookup fallback
        results = (_results_of(response_data, self.base_url) if response_data else []) or await self._try_lookup_fallback(artist_norm, search_term)
        if not results:
            self.console_logger.info("[itunes] No results found for query: '%s'", search_term)
            return []
        return self._build_release_records(results, search_term)

    def score_records(
        self,
        records: list[ReleaseRecord],
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
    ) -> list[ScoredRelease]:
        """Score release records with this search's artist context.

        Args:
            records: Release records from fetch_release_records
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_context: Region and activity period of the artist, used in scoring

        Returns:
            Releases with a positive score, in iTunes order
        """
        scored_releases: list[ScoredRelease] = []
        for record in records:
            score = self.score_release_func(
                release=record, artist_norm=artist_norm, album_norm=album_norm, artist_context=artist_context, source="itunes"
            )
            if score <= 0:
                self.console_logger.debug(
                    "[itunes] Filtered out '%s - %s' (%s): score %.2f <= 0", record["artist"], record["title"], record["year"], score
                )
                continue
            self.console_logger.debug("Scored iTunes Release: '%s' (%s) Score: %.2f", record["title"], record["year"], score)
            scored_releases.append({**record, "score": score})
        return scored_releases

    async def get_scored_releases(
        self,
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
    ) -> list[ScoredRelease]:
        """Fetch iTunes release records and score them with the artist context.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_context: Region and activity period of the artist, used in scoring

        Returns:
            Releases with a positive score, in iTunes order
        """
        records = await self.fetch_release_records(artist_norm, album_norm)
        return self.score_records(records, artist_norm, album_norm, artist_context)

    async def _try_lookup_fallback(
        self,
        artist_norm: str,
        search_term: str,
    ) -> list[dict[str, Any]]:
        """Try artist lookup as fallback when search returns no results.

        Args:
            artist_norm: Normalized artist name
            search_term: Original search term for logging

        Returns:
            List of album results from lookup, or empty list if fallback fails
        """
        self.console_logger.debug(
            "[itunes] Search returned no results for '%s', trying artist lookup fallback",
            search_term,
        )
        artist_id = await self._find_artist_id(artist_norm)
        if not artist_id:
            self.console_logger.debug(
                "[itunes] Could not find artist ID for '%s', no fallback possible",
                artist_norm,
            )
            return []

        results = await self._lookup_artist_albums(artist_id)
        if results:
            self.console_logger.info(
                "[itunes] Lookup fallback found %d albums for artist '%s'",
                len(results),
                artist_norm,
            )
        else:
            self.console_logger.debug(
                "[itunes] Lookup fallback returned no albums for artist ID %s",
                artist_id,
            )
        return results

    def _build_release_records(self, results: list[dict[str, Any]], search_term: str) -> list[ReleaseRecord]:
        """Turn iTunes results into release records, skipping results that cannot be read.

        Args:
            results: Raw API results to process
            search_term: Original search term for logging

        Returns:
            Release records for the results with an artist, an album and a release year
        """
        records: list[ReleaseRecord] = []
        for result in results:
            try:
                if record := self._build_release_record(result):
                    records.append(record)
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                # A malformed result reads the same on every lookup, so it is skipped rather than failing the provider
                self.error_logger.warning("[itunes] Skipping unreadable result for '%s': %s: %s", search_term, type(e).__name__, e)

        self.console_logger.debug("[itunes] Built %d records from %d results", len(records), len(results))
        return records

    def _build_release_record(self, result: dict[str, Any]) -> ReleaseRecord | None:
        """Build a release record from one iTunes result: its raw fields, nothing that depends on the clock or context.

        Args:
            result: Raw result from iTunes Search API

        Returns:
            The release record, or None when the result has no artist, album or release year
        """
        artist_name = result.get("artistName", "").strip()
        collection_name = result.get("collectionName", "").strip()
        if not artist_name or not collection_name:
            self.console_logger.debug("[itunes] Skipping result: missing artist or album name")
            return None

        release_year = self._extract_year_from_date(result.get("releaseDate", "").strip())
        if not release_year:
            self.console_logger.debug("[itunes] Skipping '%s - %s': no valid release year", artist_name, collection_name)
            return None

        # iTunes has no label field, so the copyright text stands in for it; the storefront searched is not where
        # the release came from, so it carries no country
        return {
            "title": collection_name,
            "year": release_year,
            "artist": artist_name,
            "album_type": result.get("collectionType", ""),
            "country": None,
            "status": "official",  # iTunes only has official releases
            "format": "Digital",
            "label": result.get("copyright", "") or None,
            "source": "itunes",
            "genre": result.get("primaryGenreName", ""),
        }

    @track_instance_method("itunes_artist_period")
    async def get_artist_start_year(self, artist_norm: str) -> int | None:
        """Get artist's earliest release year from iTunes.

        iTunes doesn't have an explicit artist start date, so we use
        the earliest album release year as a proxy.

        Args:
            artist_norm: Normalized artist name

        Returns:
            Earliest release year found, or None if no releases found

        """
        self.console_logger.debug(
            "[itunes] get_artist_start_year called for artist='%s'",
            artist_norm,
        )

        try:
            response_data = await self._fetch_artist_albums(artist_norm)
            if not response_data:
                return None

            results = response_data.get("results", [])
            if not results:
                self.console_logger.debug(
                    "[itunes] No albums found for artist: '%s'",
                    artist_norm,
                )
                return None

            years = self._extract_release_years(results, artist_norm)
            if not years:
                self.console_logger.debug(
                    "[itunes] No valid release years found for artist: '%s'",
                    artist_norm,
                )
                return None

            earliest_year = min(years)
            self.console_logger.debug(
                "[itunes] Artist '%s' earliest release year: %d (from %d albums)",
                artist_norm,
                earliest_year,
                len(years),
            )
            return earliest_year

        except (OSError, ValueError, RuntimeError) as e:
            self.error_logger.warning(
                "[itunes] Error fetching artist start year for '%s': %s",
                artist_norm,
                e,
            )
            return None

    async def _fetch_artist_albums(self, artist_norm: str) -> dict[str, Any] | None:
        """Fetch all albums for an artist from iTunes API.

        Args:
            artist_norm: Normalized artist name

        Returns:
            API response data, or None when nothing was found (HTTP 404); a failed request raises ApiRequestError

        """
        params = {
            "term": artist_norm,
            "country": self.country_code,
            "entity": "album",
            "limit": "200",
        }

        response_data = await self.make_api_request_func(
            api_name="itunes",
            url=self.base_url,
            params=params,
            max_retries=2,
            base_delay=0.5,
        )

        if not response_data:
            self.console_logger.debug(
                "[itunes] No response data for artist albums query: '%s'",
                artist_norm,
            )
            return None

        return response_data

    async def _find_artist_id(self, artist_norm: str) -> int | None:
        """Find iTunes artist ID by searching for artist.

        Args:
            artist_norm: Normalized artist name

        Returns:
            iTunes artist ID or None if not found

        """
        params = {
            "term": artist_norm,
            "country": self.country_code,
            "entity": "musicArtist",
            "limit": "5",
        }

        response_data = await self.make_api_request_func(
            api_name="itunes",
            url=self.base_url,
            params=params,
            max_retries=2,
            base_delay=0.5,
        )

        if not response_data:
            self.console_logger.debug("[itunes] No response finding artist ID for: '%s'", artist_norm)
            return None

        results = _results_of(response_data, self.base_url)
        for result in results:
            result_artist = normalize_for_matching(result.get("artistName", ""))
            # Exact match only - no substring matching to avoid cross-artist pollution
            # e.g., "madonna" should NOT match "madonna remixers"
            if result_artist == artist_norm:
                artist_id = result.get("artistId")
                self.console_logger.debug(
                    "[itunes] Found artist ID %s for '%s' (matched: '%s')",
                    artist_id,
                    artist_norm,
                    result.get("artistName"),
                )
                return artist_id

        self.console_logger.debug("[itunes] No matching artist ID found for: '%s'", artist_norm)
        return None

    async def _lookup_artist_albums(self, artist_id: int) -> list[dict[str, Any]]:
        """Get all albums for an artist using lookup API.

        This uses the /lookup endpoint which is more reliable than /search
        for getting all albums by an artist.

        Args:
            artist_id: iTunes artist ID

        Returns:
            List of album results

        """
        lookup_url = f"{ITUNES_BASE_URL}/lookup"
        params = {
            "id": str(artist_id),
            "entity": "album",
            "limit": "200",
        }

        response_data = await self.make_api_request_func(
            api_name="itunes",
            url=lookup_url,
            params=params,
            max_retries=2,
            base_delay=0.5,
        )

        if not response_data:
            self.console_logger.debug("[itunes] No response from lookup for artist ID: %s", artist_id)
            return []

        # First result is artist info, rest are albums (wrapperType == "collection")
        results = _results_of(response_data, lookup_url)
        albums = [r for r in results if r.get("wrapperType") == "collection"]

        self.console_logger.debug(
            "[itunes] Lookup found %d albums for artist ID %s",
            len(albums),
            artist_id,
        )
        return albums

    def _extract_release_years(self, results: list[dict[str, Any]], artist_norm: str) -> list[int]:
        """Extract valid release years from iTunes search results.

        Args:
            results: List of iTunes API result dictionaries
            artist_norm: Normalized artist name for filtering

        Returns:
            List of valid release years

        """
        years: list[int] = []
        artist_normalized = normalize_for_matching(artist_norm)

        for result in results:
            year = self._extract_year_from_result(result, artist_normalized)
            if year is not None:
                years.append(year)

        return years

    @classmethod
    def _extract_year_from_result(cls, result: dict[str, Any], artist_normalized: str) -> int | None:
        """Extract release year from a single iTunes result if it matches artist.

        Args:
            result: Single iTunes API result dictionary
            artist_normalized: Normalized artist name for matching

        Returns:
            Release year as int, or None if not valid/matching

        """
        artist_name = normalize_for_matching(result.get("artistName", ""))
        release_date = result.get("releaseDate", "").strip()

        # Filter by artist name (fuzzy match)
        if artist_normalized not in artist_name and artist_name not in artist_normalized:
            return None

        year = cls._extract_year_from_date(release_date)
        return int(year) if year else None
