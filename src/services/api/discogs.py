"""Discogs API client for music metadata retrieval.

This module provides the Discogs-specific implementation for fetching
and scoring music releases from the Discogs database.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING, Any, TypedDict, cast

from core.analytics_decorator import track_instance_method
from core.models.normalization import normalize_for_matching

from .api_base import BaseApiClient, ScoredRelease
from .request_executor import ApiRequestError

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

    from core.models.track_models import AppConfig, YearRetrievalConfig
    from metrics import Analytics
    from services.api.year_scoring import ArtistContext


# Discogs API v2 base URL
DISCOGS_BASE_URL: str = "https://api.discogs.com"


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


class DiscogsMasterRelease(TypedDict, total=False):
    """Type definition for Discogs master release response."""

    id: int
    title: str
    year: int | None
    main_release: int
    main_release_url: str
    resource_url: str
    uri: str
    genres: list[str]
    styles: list[str]
    data_quality: str


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
    """Discogs API client for fetching music metadata.

    Args:
        token: Discogs API token
        console_logger: Logger for console output
        error_logger: Logger for error messages
        analytics: Analytics service for performance tracking
        make_api_request_func: Function to make API requests with rate-limiting
        score_release_func: Function to score releases for originality
        scoring_config: Year retrieval configuration with scoring rules
        config: Typed application configuration

    """

    def __init__(
        self,
        token: str,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        analytics: Analytics,
        make_api_request_func: Callable[..., Awaitable[dict[str, Any] | None]],
        *,
        score_release_func: Callable[..., float],
        scoring_config: YearRetrievalConfig,
        config: AppConfig,
    ) -> None:
        super().__init__(console_logger, error_logger)
        self.analytics = analytics
        self.token = token
        self._make_api_request = make_api_request_func
        self._score_original_release = score_release_func
        self.scoring_config = scoring_config
        self.config = config

    @track_instance_method("discogs_release_details")
    async def _fetch_discogs_release_details(self, release_id: int) -> dict[str, Any] | None:
        """Fetch detailed information for a specific Discogs release.

        Args:
            release_id: Discogs release ID

        Returns:
            Release details, or None when Discogs has no such release (HTTP 404); a failed request raises ApiRequestError

        """
        detail_url = f"{DISCOGS_BASE_URL}/releases/{release_id}"
        self.console_logger.debug("[discogs] Fetching details for release ID %s", release_id)
        # Auth is handled in headers
        detail_data = await self._make_api_request("discogs", detail_url, params={})
        if detail_data:
            return detail_data

        self.console_logger.warning("[discogs] No details for release %s", release_id)
        return None

    @track_instance_method("discogs_master_release")
    async def _fetch_master_release_year(self, master_id: int) -> int | None:
        """Fetch the original release year from a Discogs master release.

        Master releases in Discogs contain the original release year,
        analogous to MusicBrainz release groups.

        Args:
            master_id: Discogs master release ID

        Returns:
            Original release year, or None when the master has no year or does not exist (HTTP 404); a failed request raises ApiRequestError

        """
        master_url = f"{DISCOGS_BASE_URL}/masters/{master_id}"
        self.console_logger.debug("[discogs] Fetching master release ID %s", master_id)
        master_data = await self._make_api_request("discogs", master_url, params={})
        if not master_data:
            return None

        # A year Discogs cannot give as a number is no year, the same on every lookup, so it is not an error
        year = master_data.get("year")
        if isinstance(year, int) or (isinstance(year, str) and year.isdigit()):
            self.console_logger.debug("[discogs] Master release %s year: %s", master_id, year)
            return int(year)

        self.console_logger.debug("[discogs] Master release %s has no year", master_id)
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

    @staticmethod
    def _normalize_artist_for_matching(artist: str) -> str:
        """Normalize artist name for flexible Discogs matching.

        Extends the base normalize_for_matching() with Discogs-specific handling:
        - "Beatles, The" -> "the beatles" (Discogs format to standard)
        - "Artist (2)" -> "artist" (numbered suffix removal)

        Args:
            artist: Artist name to normalize

        Returns:
            Normalized artist name for matching

        """
        # Use centralized normalization as base (strip + lowercase)
        normalized = normalize_for_matching(artist)
        if not normalized:
            return ""

        # Handle "X, The" -> "the x" (normalize Discogs format to standard)
        if normalized.endswith(", the"):
            normalized = f"the {normalized[:-5]}"

        # Remove trailing numbered suffix like "(2)", "(3)" and return
        return re.sub(r"\s*\(\d+\)\s*$", "", normalized)

    def _is_artist_match(
        self,
        item: DiscogsRelease,
        artist_norm: str,
    ) -> bool:
        """Check if a Discogs release matches the target artist.

        Uses flexible matching to handle variations like:
        - "The Beatles" vs "Beatles, The"
        - "Artist (2)" vs "Artist"
        - Substring matching as fallback

        Args:
            item: Discogs release item
            artist_norm: Normalized artist name

        Returns:
            True if the artist matches

        """
        # Prepare normalized target for matching
        target_normalized = self._normalize_artist_for_matching(artist_norm)
        target_no_the = target_normalized.removeprefix("the ")

        # Try to extract artist from title
        title_artist, _ = DiscogsClient._extract_artist_from_title(item.get("title", ""))

        if title_artist:
            item_artist_normalized = self._normalize_artist_for_matching(title_artist)
            item_artist_no_the = item_artist_normalized.removeprefix("the ")

            # Exact match
            if item_artist_normalized == target_normalized:
                return True

            # Match without "The" prefix (handles "The Beatles" vs "Beatles")
            if item_artist_no_the == target_no_the:
                return True

        # Fallback: Check if artist appears anywhere in title (substring match)
        title_full = item.get("title", "")
        title_normalized = self._normalize_artist_for_matching(title_full)

        # Check both with and without "The" prefix
        return target_normalized in title_normalized or target_no_the in title_normalized

    def _get_reissue_keywords(self) -> list[str]:
        """Get reissue detection keywords from configuration.

        Returns:
            List of keywords used to detect reissues

        """
        reissue_keywords = list(self.scoring_config.reissue_detection.reissue_keywords)
        remaster_keywords = list(self.config.cleaning.remaster_keywords)
        # Use concatenation to avoid mutating the original config lists
        return reissue_keywords + remaster_keywords

    async def _execute_search(
        self,
        params: dict[str, str],
        strategy_name: str,
    ) -> dict[str, Any] | None:
        """Execute a single search request to Discogs API.

        Args:
            params: Search parameters
            strategy_name: Name of the search strategy (for logging)

        Returns:
            Discogs search response dict, or None when nothing matched

        Raises:
            ApiRequestError: The search answered with an error body instead of results

        """
        search_url = f"{DISCOGS_BASE_URL}/database/search"
        log_url = f"{search_url}?{urllib.parse.urlencode(params, safe=':/')}"
        self.console_logger.debug("[discogs] %s URL: %s", strategy_name, log_url)

        data = await self._make_api_request("discogs", search_url, params=params)

        # No such resource (HTTP 404) is an answer; a body that is not a search result is not
        if data is None:
            return None
        if "message" in data or "results" not in data:
            api_name, reason = "discogs", f"search answered without results: {data.get('message', 'no results field')}"
            raise ApiRequestError(api_name, log_url, reason)

        results = data.get("results", [])
        if not results:
            return None

        self.console_logger.debug("[discogs] %s: found %s results", strategy_name, len(results))
        return data

    async def _perform_primary_search(
        self,
        artist_norm: str,
        album_norm: str,
    ) -> dict[str, Any] | None:
        """Perform primary fielded search using artist and release_title parameters.

        This is more precise than generic query search as it uses Discogs API
        field-specific parameters.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name

        Returns:
            Discogs search response dict or None if no results

        """
        params = {
            "artist": artist_norm,
            "release_title": album_norm,
            "type": "release",
            "per_page": "25",
        }
        return await self._execute_search(params, "Primary search (fielded)")

    async def _perform_fallback_searches(
        self,
        artist_norm: str,
        album_norm: str,
        artist_orig: str | None,
        album_orig: str | None,
    ) -> dict[str, Any] | None:
        """Perform fallback searches with broader queries.

        Fallback 1: Generic query with artist and album concatenation
        Fallback 2: Album-only search (will need post-filtering by artist)

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_orig: Original artist name
            album_orig: Original album name

        Returns:
            Discogs search response dict or None if no results

        """
        # Use original names for fallback if available (better matching)
        artist_fb = artist_orig or artist_norm
        album_fb = album_orig or album_norm

        # Fallback 1: Generic concatenated query (current approach)
        self.console_logger.debug("[discogs] Primary search failed, trying fallback 1 (generic query)")
        search_query = f"{artist_fb} {album_fb}"
        params_generic = {"q": search_query, "type": "release", "per_page": "25"}
        result = await self._execute_search(params_generic, "Fallback 1 (generic)")
        if result:
            return result

        # Fallback 2: Album-only search with post-filtering
        self.console_logger.debug("[discogs] Fallback 1 failed, trying fallback 2 (album-only)")
        params_album_only = {"release_title": album_fb, "type": "release", "per_page": "25"}
        return await self._execute_search(params_album_only, "Fallback 2 (album-only)")

    async def _make_discogs_search_request(
        self,
        artist_norm: str,
        album_norm: str,
        artist_orig: str | None = None,
        album_orig: str | None = None,
    ) -> dict[str, Any] | None:
        """Make search request to Discogs API with fallback strategies.

        Uses multiple search strategies similar to MusicBrainz:
        1. Primary: Fielded search with artist= and release_title= parameters
        2. Fallback 1: Generic query with artist and album concatenation
        3. Fallback 2: Album-only search with artist post-filtering

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_orig: Original artist name (for logging and fallbacks)
            album_orig: Original album name (for logging and fallbacks)

        Returns:
            Discogs search response dict, or None when no strategy matched; a failed request raises ApiRequestError

        """
        self.console_logger.debug(
            "[discogs] Start search | artist_orig='%s' artist_norm='%s', album_orig='%s', album_norm='%s'",
            artist_orig or artist_norm,
            artist_norm,
            album_orig or album_norm,
            album_norm,
        )

        # Try primary fielded search first
        result = await self._perform_primary_search(artist_norm, album_norm)

        # If primary fails, try fallback strategies
        if result is None:
            result = await self._perform_fallback_searches(artist_norm, album_norm, artist_orig, album_orig)

        if result is None:
            self.console_logger.warning(
                "[discogs] All search strategies failed for: '%s' - '%s'", artist_orig or artist_norm, album_orig or album_norm
            )
            return None

        results_count = len(result.get("results", []))
        self.console_logger.debug("[discogs] Found %s potential matches", results_count)
        return result

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
        year_value: Any = item.get("year", "")
        year_str = str(year_value)

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
            self.console_logger.debug("[discogs] Filled missing year via detail fetch: %s", extracted_year)
            year_str = extracted_year

        return year_str, detail_fetch_count

    @staticmethod
    def _build_release_record(
        item: DiscogsRelease,
        artist_norm: str,
        *,
        year_str: str,
        master_year: int | None = None,
    ) -> dict[str, Any]:
        """Build a release record from a Discogs item, the way the scorer reads it, without scoring.

        The record holds the ScoredRelease fields except the score, plus the master year as the release-group date.
        Nothing in it depends on the artist context, the clock or the configured reissue keywords.

        Args:
            item: Discogs release item
            artist_norm: Normalized artist name, used when the title names no artist
            year_str: Year string for the release
            master_year: Original release year from Discogs master release

        Returns:
            The release record
        """
        title_artist, title_album = DiscogsClient._extract_artist_from_title(item.get("title", ""))

        # The master year is the original release year; the release's own year is the fallback
        record: dict[str, Any] = {
            "title": title_album if title_album is not None else item.get("title", ""),
            "year": str(master_year) if master_year else year_str,
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
        # The master year as the release-group date, so the year-difference penalty applies as for MusicBrainz
        if master_year is not None:
            record["releasegroup_first_date"] = str(master_year)
        return record

    def score_records(
        self,
        records: list[dict[str, Any]],
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
    ) -> list[ScoredRelease]:
        """Score release records with this search's artist context and the configured reissue keywords.

        Records are cached for good, so the reissue flag is decided here from the keywords in force, not stored.

        Args:
            records: Release records from fetch_release_records
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_context: Region and activity period of the artist, used in scoring

        Returns:
            Releases with a positive score, highest first
        """
        reissue_keywords = [keyword.lower() for keyword in self._get_reissue_keywords()]
        scored_releases: list[ScoredRelease] = []
        for record in records:
            title = str(record["title"]).lower()
            to_score = {**record, "is_reissue": True} if any(keyword in title for keyword in reissue_keywords) else record
            score = self._score_original_release(to_score, artist_norm, album_norm, artist_context=artist_context, source="discogs")
            if score > 0:
                release = {key: value for key, value in record.items() if key not in {"is_reissue", "releasegroup_first_date"}}
                scored_releases.append(cast("ScoredRelease", {**release, "score": score}))
                self.console_logger.info("Scored Discogs Release: '%s' (%s) Score: %.2f", release["title"], release["year"], score)
        return sorted(scored_releases, key=lambda scored: scored["score"], reverse=True)

    async def _process_single_discogs_item(
        self,
        item: DiscogsRelease,
        artist_norm: str,
        *,
        detail_fetch_count: int,
        detail_fetch_limit: int,
    ) -> tuple[dict[str, Any] | None, int]:
        """Turn one Discogs search result into a release record.

        Args:
            item: Discogs release item to process
            artist_norm: Normalized artist name
            detail_fetch_count: Current number of detail fetches performed
            detail_fetch_limit: Maximum number of detail fetches allowed

        Returns:
            Tuple of (release record or None, updated_detail_fetch_count)

        """
        # Fetch missing year details if needed
        year_str, updated_detail_fetch_count = await self._fetch_missing_year_details(item, detail_fetch_count, detail_fetch_limit)

        # Check artist match
        if not self._is_artist_match(item, artist_norm):
            self.console_logger.debug("[discogs] Skipping '%s' - artist mismatch", item.get("title"))
            return None, updated_detail_fetch_count

        # Skip if no valid year
        if not self._is_valid_year(year_str):
            return None, updated_detail_fetch_count

        # The master release year, analogous to the MusicBrainz release-group first date. Discogs gives master_id 0 to a
        # release outside any master; pressings sharing a master repeat one URL, which the request cache answers
        master_id = item.get("master_id")
        master_year = await self._fetch_master_release_year(master_id) if master_id else None

        record = self._build_release_record(item, artist_norm, year_str=year_str, master_year=master_year)
        return record, updated_detail_fetch_count

    async def _process_discogs_results(
        self,
        results: list[DiscogsRelease],
        artist_norm: str,
    ) -> list[dict[str, Any]]:
        """Turn Discogs search results into release records.

        Args:
            results: List of Discogs release items
            artist_norm: Normalized artist name

        Returns:
            Release records for the items that match the artist and have a valid year

        """
        records: list[dict[str, Any]] = []
        detail_fetch_count = 0
        detail_fetch_limit = 10

        for item in results:
            record, detail_fetch_count = await self._process_single_discogs_item(
                item,
                artist_norm,
                detail_fetch_count=detail_fetch_count,
                detail_fetch_limit=detail_fetch_limit,
            )
            if record:
                records.append(record)

        return records

    @track_instance_method("discogs_release_search")
    async def fetch_release_records(
        self,
        artist_norm: str,
        album_norm: str,
        *,
        artist_orig: str | None = None,
        album_orig: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch Discogs release records for an album.

        Any error propagates: a failed lookup must not read as an album Discogs does not know.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_orig: Original artist name (before normalization)
            album_orig: Original album name (before normalization)

        Returns:
            Release records, empty when nothing matched
        """
        discogs_response = await self._make_discogs_search_request(artist_norm, album_norm, artist_orig, album_orig)
        if discogs_response is None:
            return []
        return await self._process_discogs_results(discogs_response.get("results", []), artist_norm)

    async def get_scored_releases(
        self,
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
        *,
        artist_orig: str | None = None,
        album_orig: str | None = None,
    ) -> list[ScoredRelease]:
        """Fetch Discogs release records and score them with the artist context.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_context: Region and activity period of the artist, used in scoring
            artist_orig: Original artist name (before normalization)
            album_orig: Original album name (before normalization)

        Returns:
            Releases with a positive score, highest first
        """
        records = await self.fetch_release_records(artist_norm, album_norm, artist_orig=artist_orig, album_orig=album_orig)
        return self.score_records(records, artist_norm, album_norm, artist_context)
