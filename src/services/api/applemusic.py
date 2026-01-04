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

import contextlib
import logging
import traceback
from collections.abc import Callable, Coroutine
from typing import Any

from core.models.normalization import normalize_for_matching
from services.api.api_base import ScoredRelease

# Constants for data validation
VALID_YEAR_LENGTH = 4  # Expected length of a year string (e.g., "2025")


class AppleMusicClient:
    """Client for iTunes Search API operations.

    Provides album search and metadata retrieval using Apple's public iTunes Search API.
    No authentication required - this is a public API service.
    """

    def __init__(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        make_api_request_func: Callable[..., Coroutine[Any, Any, dict[str, Any] | None]],
        score_release_func: Callable[..., float],
        *,
        country_code: str = "US",
        entity: str = "album",
        limit: int = 50,
    ) -> None:
        """Initialize the iTunes Search API client.

        Args:
            console_logger: Logger for console output
            error_logger: Logger for error messages
            make_api_request_func: Injected function for making API requests
            score_release_func: Injected function for scoring releases
            country_code: Country code for search results (default: US)
            entity: Type of content to search for (default: album)
            limit: Maximum number of results to return (default: 50)

        """
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.make_api_request_func = make_api_request_func
        self.score_release_func = score_release_func

        # iTunes Search API configuration
        self.base_url = "https://itunes.apple.com/search"
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

    async def get_scored_releases(
        self,
        artist_norm: str,
        album_norm: str,
    ) -> list[ScoredRelease]:
        """Get scored releases from iTunes Search API with lookup fallback.

        Uses the search API first. If no results are found, falls back to
        looking up the artist and fetching all their albums.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name

        Returns:
            List of scored releases from iTunes Search API

        """
        self.console_logger.debug(
            "[itunes] get_scored_releases called with artist='%s', album='%s'",
            artist_norm,
            album_norm,
        )
        try:
            # Build search query - iTunes Search works best with "artist album" format
            search_term = f"{artist_norm} {album_norm}".strip()

            # Build request parameters (aiohttp handles URL encoding automatically)
            params = {
                "term": search_term,
                "country": self.country_code,
                "entity": self.entity,
                "limit": str(self.limit),
            }

            self.console_logger.debug(
                "[itunes] Searching for: '%s' (country=%s)",
                search_term,
                self.country_code,
            )

            self.console_logger.debug(
                "[itunes] About to call make_api_request_func with url=%s, params=%s",
                self.base_url,
                params,
            )

            # Make the API request
            self.console_logger.debug("[itunes] Calling make_api_request_func now...")
            response_data = await self.make_api_request_func(
                api_name="itunes",
                url=self.base_url,
                params=params,
                max_retries=2,
                base_delay=0.5,
            )
            self.console_logger.debug(
                "[itunes] make_api_request_func completed, response_data type: %s",
                type(response_data),
            )

            self.console_logger.debug(
                "[itunes] make_api_request_func returned: %s",
                "data" if response_data else "None/empty",
            )

            # Get results from search API, fallback to artist lookup if empty
            results = response_data.get("results", []) if response_data else []
            if not results:
                results = await self._try_lookup_fallback(artist_norm, search_term)

            if not results:
                self.console_logger.info("[itunes] No results found for query: '%s'", search_term)
                return []

            # Filter and score results
            return self._process_api_results(results, artist_norm, album_norm, search_term)

        except (OSError, ValueError, RuntimeError) as e:
            self.error_logger.warning(
                "[itunes] Error fetching data for '%s - %s': %s",
                artist_norm,
                album_norm,
                e,
            )
            return []

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

    def _process_api_results(
        self,
        results: list[dict[str, Any]],
        artist_norm: str,
        album_norm: str,
        search_term: str,
    ) -> list[ScoredRelease]:
        """Process API results into scored releases with error handling.

        Args:
            results: Raw API results to process
            artist_norm: Normalized artist name for scoring
            album_norm: Normalized album name for scoring
            search_term: Original search term for logging

        Returns:
            List of successfully processed ScoredRelease objects
        """
        scored_releases: list[ScoredRelease] = []
        skipped_count = 0

        for result in results:
            try:
                if scored_release := self._process_itunes_result(result, artist_norm, album_norm):
                    scored_releases.append(scored_release)
                else:
                    skipped_count += 1
            except (KeyError, ValueError, TypeError) as e:
                self.error_logger.warning(
                    "[itunes] Expected error processing result for '%s': %s (type: %s)",
                    search_term,
                    e,
                    type(e).__name__,
                )
                skipped_count += 1
            except Exception as e:
                self.error_logger.exception(
                    "[itunes] Unexpected error processing result for '%s': %s (type: %s)\n%s",
                    search_term,
                    e,
                    type(e).__name__,
                    traceback.format_exc(),
                )
                skipped_count += 1

        self.console_logger.debug(
            "[itunes] Processed %d results, returning %d scored releases (%d skipped)",
            len(results),
            len(scored_releases),
            skipped_count,
        )
        return scored_releases

    def _process_itunes_result(self, result: dict[str, Any], target_artist_norm: str, target_album_norm: str) -> ScoredRelease | None:
        """Process a single iTunes Search API result into a ScoredRelease.

        Args:
            result: Raw result from iTunes Search API
            target_artist_norm: Normalized target artist name
            target_album_norm: Normalized target album name

        Returns:
            ScoredRelease object or None if result should be filtered out

        """
        try:
            # Extract basic information
            artist_name = result.get("artistName", "").strip()
            collection_name = result.get("collectionName", "").strip()
            release_date = result.get("releaseDate", "").strip()

            if not artist_name or not collection_name:
                self.console_logger.debug("[itunes] Skipping result: missing artist or album name")
                return None

            # Extract release year from date
            release_year = None
            if release_date:
                try:
                    # iTunes returns dates in ISO format: "2024-03-15T12:00:00Z"
                    release_year = release_date.split("-")[0]
                    if not release_year.isdigit() or len(release_year) != VALID_YEAR_LENGTH:
                        release_year = None
                except (IndexError, ValueError):
                    self.console_logger.debug("[itunes] Could not parse release date: '%s'", release_date)
                    release_year = None

            if not release_year:
                self.console_logger.debug(
                    "[itunes] Skipping '%s - %s': no valid release year",
                    artist_name,
                    collection_name,
                )
                return None

            # Score the release using the injected scoring function
            try:
                score = self.score_release_func(
                    release={
                        "title": collection_name,
                        "artist": artist_name,
                        "year": release_year,
                        "album_type": result.get("collectionType", ""),
                        "country": self.country_code,
                        "status": "official",  # iTunes only has official releases
                        "format": "Digital",  # iTunes is digital distribution
                        "label": result.get("copyright", ""),
                        "genre": result.get("primaryGenreName", ""),
                    },
                    artist_norm=target_artist_norm,
                    album_norm=target_album_norm,
                    artist_region=None,  # Not used in scoring
                    source="itunes",
                )
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                self.console_logger.debug(
                    "[itunes] Failed to score release '%s - %s': %s",
                    artist_name,
                    collection_name,
                    e,
                )
                return None

            # Create scored release
            # Note: iTunes API does not provide a dedicated label/publisher field.
            # The copyright field is used as label, which may contain full legal text.
            # This field is primarily used for scoring, not display.
            scored_release: ScoredRelease = {
                "title": collection_name,
                "year": release_year,
                "score": score,
                "artist": artist_name,
                "album_type": result.get("collectionType", ""),
                "country": self.country_code,
                "status": "official",
                "format": "Digital",
                "label": result.get("copyright") or None,
                "catalog_number": None,  # iTunes doesn't provide catalog numbers
                "barcode": None,  # iTunes doesn't provide barcodes
                "disambiguation": result.get("collectionCensoredName") or None,
                "source": "itunes",
            }

            # Filter out releases with zero or negative scores (same as MusicBrainz, Discogs, Last.fm)
            if score <= 0:
                self.console_logger.debug(
                    "[itunes] Filtered out '%s - %s' (%s): score %.2f <= 0",
                    artist_name,
                    collection_name,
                    release_year,
                    score,
                )
                return None

            self.console_logger.debug(
                "Scored iTunes Release: '%s' (%s) Score: %.2f",
                collection_name,
                release_year,
                score,
            )

        except (KeyError, ValueError, TypeError) as e:
            self.error_logger.warning("[itunes] Error processing result: %s", e)
            return None

        return scored_release

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
            API response data or None if request failed

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

        results = response_data.get("results", [])
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
        lookup_url = "https://itunes.apple.com/lookup"
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
        results = response_data.get("results", [])
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

    @staticmethod
    def _extract_year_from_result(result: dict[str, Any], artist_normalized: str) -> int | None:
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

        if not release_date:
            return None

        with contextlib.suppress(IndexError, ValueError):
            year_str = release_date.split("-")[0]
            if year_str.isdigit() and len(year_str) == VALID_YEAR_LENGTH:
                return int(year_str)
        return None
