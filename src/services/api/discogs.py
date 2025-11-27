"""Discogs API client for music metadata retrieval.

This module provides the Discogs-specific implementation for fetching
and scoring music releases from the Discogs database.
"""

import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict, cast

from src.metrics import Analytics

from .api_base import BaseApiClient, ScoredRelease


# Discogs Type Definitions
class DiscogsFormat(TypedDict, total=False):
    """Type definition for Discogs format information."""

    name: str
    descriptions: list[str]
    qty: str | None
    text: str | None


class DiscogsRelease(TypedDict, total=False):
    """Type definition for Discogs release from search results."""

    id: int | str
    title: str
    year: int | str | None
    formats: list[DiscogsFormat]
    released: str | None
    country: str | None
    genre: list[str]
    style: list[str]
    label: list[str]
    type: str
    thumb: str | None
    cover_image: str | None
    resource_url: str
    uri: str
    master_id: int | None
    master_url: str | None


class DiscogsSearchResponse(TypedDict, total=False):
    """Type definition for Discogs search API response."""

    results: list[DiscogsRelease]
    pagination: dict[str, Any]


def _get_format_details(formats: list[DiscogsFormat]) -> str:
    """Extract format details from a Discogs format list.

    Args:
        formats: List of format information

    Returns:
        Formatted string of format details

    """
    if not formats:
        return ""

    format_parts: list[str] = []
    for fmt in formats:
        name = fmt.get("name", "")
        descriptions = fmt.get("descriptions", [])
        if name:
            format_parts.append(f"{name} ({', '.join(descriptions)})" if descriptions else name)

    return ", ".join(format_parts)


class DiscogsClient(BaseApiClient):
    """Discogs API client for fetching music metadata."""

    def __init__(
        self,
        token: str,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics: Analytics,
        make_api_request_func: Callable[..., Awaitable[dict[str, Any] | None]],
        *,
        score_release_func: Callable[..., float],
        cache_service: Any,  # Type as Any for now since CacheServiceProtocol is in utils
        scoring_config: dict[str, Any],
        config: dict[str, Any],
        cache_ttl_days: int = 30,
    ) -> None:
        """Initialize Discogs client.

        Args:
            token: Discogs API token
            console_logger: Logger for console output
            error_logger: Logger for error messages
            analytics: Analytics service for performance tracking
            make_api_request_func: Function to make API requests with rate-limiting
            score_release_func: Function to score releases for originality
            cache_service: Cache service for storing results
            scoring_config: Scoring configuration
            config: General configuration
            cache_ttl_days: Cache TTL in days

        """
        super().__init__(console_logger, error_logger)
        self.analytics = analytics
        self.token = token
        self._make_api_request = make_api_request_func
        self._score_original_release = score_release_func
        self.cache_service = cache_service
        self.scoring_config = scoring_config
        self.config = config
        self.cache_ttl_days = cache_ttl_days

    @Analytics.track_instance_method("discogs_release_details")
    async def _fetch_discogs_release_details(self, release_id: int) -> dict[str, Any] | None:
        """Fetch detailed information for a specific Discogs release.

        Args:
            release_id: Discogs release ID

        Returns:
            Release details or None if fetch fails

        """
        try:
            detail_url = f"https://api.discogs.com/releases/{release_id}"
            params: dict[str, Any] = {}  # Auth is handled in headers

            self.console_logger.debug("[discogs] Fetching details for release ID %s", release_id)

            detail_data = await self._make_api_request("discogs", detail_url, params=params)

            if detail_data:
                return detail_data

            self.console_logger.warning("[discogs] Failed to fetch details for release %s", release_id)
            return None

        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
            self.error_logger.exception(f"[discogs] Error fetching release details for ID {release_id}: {e}")
            return None

    @staticmethod
    def _extract_artist_from_title(title: str) -> tuple[str | None, str | None]:
        """Extract artist and album from Discogs title format.

        Discogs titles are often in the format "Artist - Album."

        Args:
            title: Title string from Discogs

        Returns:
            Tuple of (artist, album) or (None, None) if it cannot parse

        """
        if " - " in title:
            parts = title.split(" - ", 1)
            expected_parts = 2
            if len(parts) == expected_parts:
                return parts[0].strip(), parts[1].strip()
        return None, None

    def _is_artist_match(
        self,
        item: DiscogsRelease,
        artist_norm: str,
    ) -> bool:
        """Check if a Discogs release matches the target artist.

        Args:
            item: Discogs release item
            artist_norm: Normalized artist name

        Returns:
            True if the artist matches

        """
        # Try to extract artist from title
        title_artist, _ = DiscogsClient._extract_artist_from_title(item.get("title", ""))

        if title_artist and self._normalize_name(title_artist) == artist_norm:
            return True

        # Check the title directly (sometimes it's just the album name)
        title_full = item.get("title", "")
        return artist_norm in self._normalize_name(title_full)

    @Analytics.track_instance_method("discogs_year_search")
    async def get_year_from_discogs(self, artist: str, album: str) -> str | None:
        """Get year from Discogs (for backward compatibility).

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Year string or None

        """
        releases = await self.get_scored_releases(self._normalize_name(artist), self._normalize_name(album), None)

        if releases:
            # Return the year from the highest scored release
            best_release = max(releases, key=lambda x: x["score"])
            year = best_release.get("year")
            return str(year) if year is not None else None

        return None

    async def _get_cached_discogs_releases(self, cache_key: str) -> list[ScoredRelease] | None:
        """Retrieve cached Discogs releases if available.

        Args:
            cache_key: The cache key to look up

        Returns:
            List of cached scored releases or None if not found/invalid

        """
        cached_data = await self.cache_service.get_async(cache_key)
        if cached_data is not None:
            if isinstance(cached_data, list):
                self.console_logger.debug(f"Using cached Discogs results for cache key: {cache_key}")
                return cast("list[ScoredRelease]", cached_data)
            self.console_logger.warning("Cached Discogs data has unexpected type. Ignoring cache.")
        return None

    def _get_reissue_keywords(self) -> list[str]:
        """Get reissue detection keywords from configuration.

        Returns:
            List of keywords used to detect reissues

        """
        scoring_cfg = self.scoring_config
        reissue_keywords: list[str] = scoring_cfg.get("reissue_detection", {}).get("reissue_keywords", [])
        remaster_keywords: list[str] = self.config.get("cleaning", {}).get("remaster_keywords", [])
        # Use concatenation to avoid mutating the original config lists
        return reissue_keywords + remaster_keywords

    async def _make_discogs_search_request(
        self,
        artist_norm: str,
        album_norm: str,
        artist_orig: str | None = None,
        album_orig: str | None = None,
    ) -> dict[str, Any] | None:
        """Make search request to Discogs API.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_orig: Original artist name (for logging)
            album_orig: Original album name (for logging)

        Returns:
            Discogs search response dict or None if failed

        """
        self.console_logger.debug(
            f"[discogs] Start search | artist_orig='{artist_orig or artist_norm}' "
            f"artist_norm='{artist_norm}', album_orig='{album_orig or album_norm}', album_norm='{album_norm}'",
        )

        # Build search query
        search_query = f"{artist_norm} {album_norm}"
        params = {"q": search_query, "type": "release", "per_page": "25"}
        search_url = "https://api.discogs.com/database/search"

        log_discogs_url = f"{search_url}?{urllib.parse.urlencode(params, safe=':/')}"
        self.console_logger.debug(f"[discogs] Search URL: {log_discogs_url}")

        data = await self._make_api_request("discogs", search_url, params=params)

        # Check for an error message
        if isinstance(data, dict) and "message" in data:
            self.error_logger.warning(f"[discogs] API message: {data.get('message')}")
            return None

        # Process results
        if not data or "results" not in data:
            self.console_logger.warning(f"[discogs] Search failed or no results for query: '{search_query}'")
            return None

        # data is already checked to be dict with "results" key
        results = data.get("results", [])

        if not results:
            self.console_logger.info(f"[discogs] No results found for query: '{search_query}'")
            return None

        self.console_logger.debug(f"Found {len(results)} potential Discogs matches for query: '{search_query}'")
        return data

    def _should_fetch_details(self, year_str: str, detail_fetch_count: int, detail_fetch_limit: int) -> bool:
        """Check if details should be fetched based on year validity and fetch limits."""
        return not self._is_valid_year(year_str) and detail_fetch_count < detail_fetch_limit

    @staticmethod
    def _get_validated_release_id(item: DiscogsRelease) -> int | None:
        """Safely extract and validate release ID from item."""
        release_id_raw = item.get("id")
        if release_id_raw is None:
            return None

        try:
            return int(release_id_raw)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_year_from_released_field(released_value: str) -> str | None:
        """Parse year from the released field using regex."""
        match = re.match(r"^(\d{4})", released_value)
        return match[1] if match else None

    def _extract_year_from_detail_data(self, detail_data: dict[str, Any]) -> str | None:
        """Extract valid year from detail data."""
        year_from_detail = detail_data.get("year")

        # If no direct year, try parsing from released field
        if not year_from_detail:
            released_value = detail_data.get("released")
            if isinstance(released_value, str):
                year_from_detail = self._parse_year_from_released_field(released_value)

        # Validate and return
        if year_from_detail and self._is_valid_year(str(year_from_detail)):
            return str(year_from_detail)

        return None

    async def _fetch_missing_year_details(self, item: DiscogsRelease, detail_fetch_count: int, detail_fetch_limit: int) -> tuple[str, int]:
        """Fetch missing year details from Discogs release details API.

        Args:
            item: Discogs release item
            detail_fetch_count: Current number of detail fetches performed
            detail_fetch_limit: Maximum number of detail fetches allowed

        Returns:
            Tuple of (year_string, updated_detail_fetch_count)

        """
        year_str = str(item.get("year", ""))

        # Early return if no need to fetch details
        if not self._should_fetch_details(year_str, detail_fetch_count, detail_fetch_limit):
            return year_str, detail_fetch_count

        # Get validated release ID
        release_id = self._get_validated_release_id(item)
        if release_id is None:
            return year_str, detail_fetch_count

        # Fetch detail data
        detail_data = await self._fetch_discogs_release_details(release_id)
        detail_fetch_count += 1

        # Extract year from detail data
        if detail_data and (extracted_year := self._extract_year_from_detail_data(detail_data)):
            self.console_logger.debug(f"[discogs] Filled missing year via detail fetch: {extracted_year}")
            year_str = extracted_year

        return year_str, detail_fetch_count

    def _create_scored_release(
        self,
        item: DiscogsRelease,
        artist_norm: str,
        album_norm: str,
        *,
        artist_region: str | None,
        year_str: str,
        is_reissue: bool,
    ) -> ScoredRelease | None:
        """Create and score a release from Discogs item.

        Args:
            item: Discogs release item
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_region: Artist's region for scoring
            year_str: Year string for the release
            is_reissue: Whether this is detected as a reissue

        Returns:
            Scored release or None if score is 0

        """
        # Extract artist and album from the title
        title_artist, title_album = DiscogsClient._extract_artist_from_title(item.get("title", ""))

        # Create a scored release
        release_info: ScoredRelease = {
            "title": title_album if title_album is not None else item.get("title", ""),
            "year": year_str,
            "score": 0.0,
            "artist": title_artist if title_artist is not None else artist_norm,
            "album_type": item.get("type", "Album"),
            "country": item.get("country"),
            "status": "Official",  # Discogs doesn't provide status
            "format": _get_format_details(item.get("formats", [])),
            "label": ", ".join(item.get("label", [])) if item.get("label") else None,
            "catalog_number": None,  # Not in search results
            "barcode": None,  # Not in search results
            "disambiguation": None,
            "source": "discogs",
        }

        # Store reissue flag separately for scoring
        release_info_with_meta = dict(release_info)
        if is_reissue:
            release_info_with_meta["is_reissue"] = True

        # Score the release (pass extended dict with metadata)
        score = self._score_original_release(
            release_info_with_meta if is_reissue else release_info,
            artist_norm,
            album_norm,
            artist_region=artist_region,
            source="discogs",
        )

        if score > 0:
            release_info["score"] = score
            self.console_logger.info(f"Scored Discogs Release: '{release_info['title']}' ({release_info['year']}) Score: {score:.2f}")
            return release_info

        return None

    async def _process_single_discogs_item(
        self,
        item: DiscogsRelease,
        artist_norm: str,
        album_norm: str,
        *,
        artist_region: str | None,
        reissue_keywords: list[str],
        detail_fetch_count: int,
        detail_fetch_limit: int,
    ) -> tuple[ScoredRelease | None, int]:
        """Process a single Discogs search result item.

        Args:
            item: Discogs release item to process
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_region: Artist's region for scoring
            reissue_keywords: Keywords to detect reissues
            detail_fetch_count: Current number of detail fetches performed
            detail_fetch_limit: Maximum number of detail fetches allowed

        Returns:
            Tuple of (scored_release or None, updated_detail_fetch_count)

        """
        # Fetch missing year details if needed
        year_str, updated_detail_fetch_count = await self._fetch_missing_year_details(item, detail_fetch_count, detail_fetch_limit)

        # Check artist match
        if not self._is_artist_match(item, artist_norm):
            self.console_logger.debug(f"[discogs] Skipping '{item.get('title')}' - artist mismatch")
            return None, updated_detail_fetch_count

        # Skip if no valid year
        if not self._is_valid_year(year_str):
            return None, updated_detail_fetch_count

        # Check if this is a reissue
        _, title_album = DiscogsClient._extract_artist_from_title(item.get("title", ""))
        title_lower = (title_album or item.get("title", "")).lower()
        is_reissue = any(keyword.lower() in title_lower for keyword in reissue_keywords)

        # Create and return scored release
        scored_release = self._create_scored_release(
            item,
            artist_norm,
            album_norm,
            artist_region=artist_region,
            year_str=year_str,
            is_reissue=is_reissue,
        )

        return scored_release, updated_detail_fetch_count

    async def _process_discogs_results(
        self,
        results: list[DiscogsRelease],
        artist_norm: str,
        album_norm: str,
        *,
        artist_region: str | None,
        reissue_keywords: list[str],
    ) -> list[ScoredRelease]:
        """Process Discogs search results and create scored releases.

        Args:
            results: List of Discogs release items
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_region: Artist's region for scoring
            reissue_keywords: Keywords to detect reissues

        Returns:
            List of scored releases

        """
        scored_releases: list[ScoredRelease] = []
        detail_fetch_count = 0
        detail_fetch_limit = 10

        for item in results:
            scored_release, detail_fetch_count = await self._process_single_discogs_item(
                item,
                artist_norm,
                album_norm,
                artist_region=artist_region,
                reissue_keywords=reissue_keywords,
                detail_fetch_count=detail_fetch_count,
                detail_fetch_limit=detail_fetch_limit,
            )

            if scored_release:
                scored_releases.append(scored_release)

        return scored_releases

    @Analytics.track_instance_method("discogs_release_search")
    async def get_scored_releases(
        self,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
        *,
        artist_orig: str | None = None,
        album_orig: str | None = None,
    ) -> list[ScoredRelease]:
        """Retrieve and score releases from Discogs.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_region: Artist's region for scoring
            artist_orig: Original artist name (before normalization)
            album_orig: Original album name (before normalization)

        Returns:
            List of scored releases sorted by score

        """
        cache_key = f"discogs_{artist_norm}_{album_norm}"
        cache_ttl_seconds = self.cache_ttl_days * 86400

        # Check cache first
        cached_releases = await self._get_cached_discogs_releases(cache_key)
        if cached_releases is not None:
            return cached_releases

        try:
            # Make search request
            discogs_response = await self._make_discogs_search_request(artist_norm, album_norm, artist_orig, album_orig)

            if discogs_response is None:
                await self.cache_service.set_async(cache_key, [], ttl=cache_ttl_seconds)
                return []

            results = discogs_response.get("results", [])

            # Get reissue keywords
            reissue_keywords = self._get_reissue_keywords()

            # Process results
            scored_releases: list[ScoredRelease] = await self._process_discogs_results(
                results,
                artist_norm,
                album_norm,
                artist_region=artist_region,
                reissue_keywords=reissue_keywords,
            )

            # Cache results
            await self.cache_service.set_async(cache_key, scored_releases, ttl=cache_ttl_seconds)

        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            self.error_logger.exception(f"Error fetching from Discogs for '{artist_norm} - {album_norm}': {e}")
            return []

        return sorted(scored_releases, key=lambda x: x["score"], reverse=True)
