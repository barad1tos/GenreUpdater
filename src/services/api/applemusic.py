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

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from src.services.api.base import ScoredRelease

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
        self.limit = min(limit, 200)  # iTunes API max is 200

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
            for result in results:
                try:
                    if scored_release := self._process_itunes_result(result, artist_norm, album_norm):
                        scored_releases.append(scored_release)
                except (KeyError, ValueError, TypeError) as e:
                    self.error_logger.warning("[itunes] Error processing result for '%s': %s", search_term, e)
                    continue

            self.console_logger.debug(
                "[itunes] Processed %d results, returning %d scored releases",
                len(results),
                len(scored_releases),
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

            # Create scored release
            scored_release: ScoredRelease = {
                "title": collection_name,
                "year": release_year,
                "score": score,
                "artist": artist_name,
                "album_type": result.get("collectionType", ""),
                "country": self.country_code,
                "status": "official",
                "format": "Digital",
                "label": (result.get("copyright", "")[:100] if result.get("copyright") else None),  # Truncate long labels
                "catalog_number": None,  # iTunes doesn't provide catalog numbers
                "barcode": None,  # iTunes doesn't provide barcodes
                "disambiguation": result.get("collectionCensoredName", ""),
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

