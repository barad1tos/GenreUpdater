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
        """Get scored releases from iTunes Search API.

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

            if not response_data:
                self.console_logger.info("[itunes] No response data for '%s'", search_term)
                return []

            # Parse results
            results = response_data.get("results", [])
            if not results:
                self.console_logger.info("[itunes] No results found for query: '%s'", search_term)
                return []

            # Filter and score results
            scored_releases: list[ScoredRelease] = []
            skipped_count = 0
            for result in results:
                try:
                    if scored_release := self._process_itunes_result(result, artist_norm, album_norm):
                        scored_releases.append(scored_release)
                    else:
                        skipped_count += 1
                except (KeyError, ValueError, TypeError) as e:
                    # Expected errors from malformed API responses or missing fields
                    self.error_logger.warning(
                        "[itunes] Expected error processing result for '%s': %s (type: %s)",
                        search_term,
                        e,
                        type(e).__name__,
                    )
                    skipped_count += 1
                    continue
                except Exception as e:
                    # Unexpected errors that need investigation
                    self.error_logger.exception(
                        "[itunes] Unexpected error processing result for '%s': %s (type: %s)\n%s",
                        search_term,
                        e,
                        type(e).__name__,
                        traceback.format_exc(),
                    )
                    skipped_count += 1
                    continue

            self.console_logger.debug(
                "[itunes] Processed %d results, returning %d scored releases (%d skipped)",
                len(results),
                len(scored_releases),
                skipped_count,
            )

        except (OSError, ValueError, RuntimeError) as e:
            self.error_logger.warning(
                "[itunes] Error fetching data for '%s - %s': %s",
                artist_norm,
                album_norm,
                e,
            )
            return []

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

            self.console_logger.debug(
                "[itunes] Scored release: '%s - %s' (%s) Score: %.2f",
                artist_name,
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

    def _extract_release_years(
        self, results: list[dict[str, Any]], artist_norm: str
    ) -> list[int]:
        """Extract valid release years from iTunes search results.

        Args:
            results: List of iTunes API result dictionaries
            artist_norm: Normalized artist name for filtering

        Returns:
            List of valid release years

        """
        years: list[int] = []
        artist_norm_lower = artist_norm.lower()

        for result in results:
            year = self._extract_year_from_result(result, artist_norm_lower)
            if year is not None:
                years.append(year)

        return years

    @staticmethod
    def _extract_year_from_result(
            result: dict[str, Any], artist_norm_lower: str
    ) -> int | None:
        """Extract release year from a single iTunes result if it matches artist.

        Args:
            result: Single iTunes API result dictionary
            artist_norm_lower: Lowercased normalized artist name

        Returns:
            Release year as int, or None if not valid/matching

        """
        artist_name = result.get("artistName", "").strip().lower()
        release_date = result.get("releaseDate", "").strip()

        # Filter by artist name (fuzzy match)
        if artist_norm_lower not in artist_name and artist_name not in artist_norm_lower:
            return None

        if not release_date:
            return None

        with contextlib.suppress(IndexError, ValueError):
            year_str = release_date.split("-")[0]
            if year_str.isdigit() and len(year_str) == VALID_YEAR_LENGTH:
                return int(year_str)
        return None
