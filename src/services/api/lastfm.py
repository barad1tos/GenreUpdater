"""Last.fm API client for music metadata retrieval.

This module provides the Last.fm-specific implementation for fetching
album information and release years.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict, cast

from metrics import Analytics

from .api_base import BaseApiClient, ScoredRelease


# Last.fm Type Definitions
class LastFmImage(TypedDict, total=False):
    """Type definition for image from Last.fm."""

    size: str
    text: str  # The URL is in '#text' but TypedDict doesn't allow that key name


class LastFmTag(TypedDict, total=False):
    """Type definition for tag from Last.fm."""

    name: str
    url: str
    count: int | None


class LastFmTextContent(TypedDict, total=False):
    """Type definition for text content (bio/wiki) from Last.fm.

    Used for both artist biographies and album/track wiki entries,
    as the API returns identical structures for both.
    """

    published: str | None
    summary: str | None
    content: str | None


class LastFmStats(TypedDict, total=False):
    """Type definition for stats from Last.fm."""

    listeners: str | None
    playcount: str | None


class LastFmSimilarArtist(TypedDict, total=False):
    """Type definition for a similar artist from Last.fm."""

    name: str
    url: str
    image: list[LastFmImage] | None


class LastFmArtist(TypedDict, total=False):
    """Type definition for artist from Last.fm."""

    name: str
    mbid: str | None
    url: str
    image: list[LastFmImage] | None
    streamable: str | None
    ontour: str | None
    stats: LastFmStats | None
    similar: dict[str, list[LastFmSimilarArtist]] | None
    tags: dict[str, list[LastFmTag]] | None
    bio: LastFmTextContent | None
    wiki: LastFmTextContent | None


class LastFmTrack(TypedDict, total=False):
    """Type definition for track from Last.fm."""

    name: str
    duration: str | None
    listeners: str | None
    mbid: str | None
    url: str
    streamable: dict[str, str] | None
    artist: LastFmArtist | None
    album: dict[str, Any] | None
    toptags: dict[str, list[LastFmTag]] | None
    wiki: LastFmTextContent | None


class LastFmAlbum(TypedDict, total=False):
    """Type definition for album from Last.fm."""

    name: str
    artist: str
    mbid: str | None
    url: str
    image: list[LastFmImage] | None
    listeners: str | None
    playcount: str | None
    tracks: dict[str, list[LastFmTrack]] | None
    tags: dict[str, list[LastFmTag]] | None
    wiki: LastFmTextContent | None
    releasedate: str | None


class LastFmClient(BaseApiClient):
    """Last.fm API client for fetching music metadata."""

    LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
    DEFAULT_ERROR_MESSAGE = "Unknown error"

    def __init__(
        self,
        api_key: str,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        make_api_request_func: Callable[..., Awaitable[dict[str, Any] | None]],
        score_release_func: Callable[..., float],
        use_lastfm: bool = True,
        *,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize Last.fm client.

        Args:
            api_key: Last.fm API key
            console_logger: Logger for console output
            error_logger: Logger for error messages
            make_api_request_func: Function to make API requests with rate-limiting
            score_release_func: Function to score releases for originality
            use_lastfm: Whether to use Last.fm API
            config: Application configuration for remaster_keywords

        """
        super().__init__(console_logger, error_logger)
        self.api_key = api_key
        self._make_api_request = make_api_request_func
        self._score_original_release = score_release_func
        self.use_lastfm = use_lastfm
        self.config = config or {}
        self._remaster_keywords = self.config.get("cleaning", {}).get("remaster_keywords", [])

    def _strip_trailing_keyword(self, text: str) -> tuple[str, bool]:
        """Strip a single trailing keyword from text.

        Args:
            text: Text to process

        Returns:
            Tuple of (processed_text, was_keyword_found)

        """
        text_lower = text.lower()
        for keyword in self._remaster_keywords:
            keyword_lower = keyword.lower()
            if text_lower.endswith(keyword_lower):
                idx = text_lower.rfind(keyword_lower)
                if idx > 0:
                    return text[:idx].strip(), True
        return text, False

    def _clean_album_for_search(self, album: str) -> str | None:
        """Clean album name for fallback search.

        Removes common suffixes and patterns that cause Last.fm lookup failures.
        Returns None if no changes were made (to avoid duplicate API calls).

        Args:
            album: Original album name

        Returns:
            Cleaned album name or None if unchanged

        """
        if not album:
            return None

        original = album
        cleaned = album.strip()

        # Step 1: Split on colon and take first part
        if ":" in cleaned:
            cleaned = cleaned.split(":")[0].strip()

        # Step 2: Strip trailing keywords iteratively
        if self._remaster_keywords:
            keyword_found = True
            while keyword_found:
                cleaned, keyword_found = self._strip_trailing_keyword(cleaned)

        # Return None if no change (avoid duplicate request)
        return None if cleaned == original or not cleaned else cleaned

    @staticmethod
    def _normalize_artist_for_matching(artist: str) -> str:
        """Normalize artist name for flexible matching.

        Extends basic normalization with Last.fm/Discogs-specific handling:
        - "Beatles, The" -> "the beatles" (common API format)
        - "Artist (2)" -> "artist" (disambiguation suffix removal)

        Args:
            artist: Artist name to normalize

        Returns:
            Normalized artist name for matching

        """
        if not artist:
            return ""

        # Basic normalization: strip + lowercase
        normalized = artist.strip().lower()

        # Handle "X, The" -> "the x" (common in some API responses)
        if normalized.endswith(", the"):
            normalized = f"the {normalized[:-5]}"

        # Remove trailing numbered suffix like "(2)", "(3)" for disambiguation
        return re.sub(r"\s*\(\d+\)\s*$", "", normalized)

    def _is_artist_match(self, result_artist: str, target_artist: str) -> bool:
        """Check if result artist matches target artist.

        Uses flexible matching to handle variations like:
        - "The Beatles" vs "Beatles, The"
        - "Artist (2)" vs "Artist"
        - Substring matching as fallback

        Args:
            result_artist: Artist name from search result
            target_artist: Target artist we're looking for

        Returns:
            True if artists match

        """
        result_norm = self._normalize_artist_for_matching(result_artist)
        target_norm = self._normalize_artist_for_matching(target_artist)

        # Prepare versions without "The" prefix
        result_no_the = result_norm.removeprefix("the ")
        target_no_the = target_norm.removeprefix("the ")

        # Exact match
        if result_norm == target_norm:
            return True

        # Match without "The" prefix
        if result_no_the == target_no_the:
            return True

        # Substring fallback (handles "Air" in "Air Supply" cases carefully)
        return target_norm in result_norm or target_no_the in result_norm

    def _extract_year_from_release_date(self, album_data: LastFmAlbum | dict[str, Any]) -> str | None:
        """Extract year from the explicit 'releasedate' field.

        Args:
            album_data: Album data from Last.fm API

        Returns:
            Year string if found, otherwise None

        """
        release_date_raw = album_data.get("releasedate", "")
        release_date_str = str(release_date_raw).strip() if release_date_raw else ""
        if release_date_str and (year_match := re.search(r"\b(\d{4})\b", release_date_str)):
            potential_year = year_match[1]
            if self._is_valid_year(potential_year):
                self.console_logger.debug("LastFM Year: %s from 'releasedate' field", potential_year)
                return potential_year
        return None

    def _extract_year_from_wiki_content(self, album_data: LastFmAlbum | dict[str, Any]) -> str | None:
        """Extract year from wiki content using pattern matching.

        Args:
            album_data: Album data from Last.fm API

        Returns:
            Year string if found, otherwise None

        """
        wiki = album_data.get("wiki")
        wiki_content_raw = wiki.get("content") if isinstance(wiki, dict) else None

        if not (wiki and isinstance(wiki, dict) and isinstance(wiki_content_raw, str) and wiki_content_raw.strip()):
            return None

        wiki_content = wiki_content_raw
        # Define patterns to search for year information
        patterns = [
            # Specific phrases like "Originally released in/on YYYY"
            r"(?:originally\s+)?released\s+(?:in|on)\s+(?:(?:\d{1,2}\s+)?"
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
            r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+)?(\d{4})",
            # Phrases like "YYYY release" or "a YYYY album"
            r"\b(19\d{2}|20\d{2})\s+(?:release|album)\b",
            # Common date formats
            r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
            r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+(\d{4})\b",
            r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
            r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})\b",
        ]

        for pattern in patterns:
            if match := re.search(pattern, wiki_content, re.IGNORECASE):
                # Find the actual year group
                potential_year = next(
                    (g for g in match.groups() if g is not None),
                    None,
                )
                if potential_year and self._is_valid_year(potential_year):
                    self.console_logger.debug("LastFM Year: %s from wiki content pattern", potential_year)
                    return potential_year
        return None

    def _extract_year_from_tags(self, album_data: LastFmAlbum | dict[str, Any]) -> str | None:
        """Extract year from tags if the tag name is a valid year.

        Args:
            album_data: Album data from Last.fm API

        Returns:
            Year string if found, otherwise None

        """
        tags = album_data.get("tags")
        if not (tags and isinstance(tags, dict)):
            return None

        # Note: LastFM API sometimes returns list[str] instead of list[LastFmTag]
        if tag_list := tags.get("tag", []):
            for tag in tag_list:
                # Handle both dict (LastFmTag) and string cases from API
                if isinstance(tag, str):
                    tag_name = tag
                elif isinstance(tag, dict):
                    tag_name = tag.get("name", "")
                else:
                    # Unexpected tag type - log and skip
                    self.console_logger.debug("LastFM: Unexpected tag type %s, skipping", type(tag).__name__)
                    continue
                # Check if the tag is a year
                if tag_name and self._is_valid_year(tag_name):
                    self.console_logger.debug("LastFM Year: %s from tags", tag_name)
                    return tag_name
        return None

    def _extract_year_from_lastfm_data(
        self,
        album_data: LastFmAlbum | dict[str, Any],
    ) -> str | None:
        """Extract the most likely year from Last.fm album data.

        Prioritizes explicit release date, then wiki content patterns, then tags.

        Args:
            album_data: Album data from Last.fm API

        Returns:
            Year string if found, otherwise None

        """
        # Priority 1: Explicit 'releasedate' field
        if year := self._extract_year_from_release_date(album_data):
            return year

        # Priority 2: Wiki Content
        if year := self._extract_year_from_wiki_content(album_data):
            return year

        # Priority 3: Tags
        return self._extract_year_from_tags(album_data)

    async def _search_albums(self, album: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search for albums using album.search API method.

        This is a fuzzy search that returns multiple potential matches.
        Results need post-filtering by artist.

        Args:
            album: Album name to search for
            limit: Maximum number of results to return

        Returns:
            List of album matches from search results

        """
        if not self.use_lastfm:
            return []

        url = self.LASTFM_API_URL
        params: dict[str, str] = {
            "method": "album.search",
            "album": album,
            "api_key": str(self.api_key or ""),
            "format": "json",
            "limit": str(limit),
        }

        try:
            data = await self._make_api_request("lastfm", url, params=params)

            if not data:
                return []

            if "error" in data:
                self.console_logger.debug(
                    "Last.fm album.search error %s: %s",
                    data.get("error"),
                    data.get("message", self.DEFAULT_ERROR_MESSAGE),
                )
                return []

            # Navigate to results: data.results.albummatches.album
            results = data.get("results", {})
            album_matches = results.get("albummatches", {})
            albums = album_matches.get("album", [])

            if isinstance(albums, list):
                self.console_logger.debug("Last.fm album.search found %d results for '%s'", len(albums), album)
                return albums

        except (OSError, ValueError, KeyError, TypeError):
            self.error_logger.exception("Error in Last.fm album.search")

        return []

    @Analytics.track_instance_method("lastfm_album_search")
    async def get_album_info(
        self,
        artist: str,
        album: str,
    ) -> LastFmAlbum | None:
        """Get album information from Last.fm.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Album information or None if not found

        """
        if not self.use_lastfm:
            return None

        url = self.LASTFM_API_URL
        params: dict[str, str] = {
            "method": "album.getInfo",
            "artist": artist,
            "album": album,
            "api_key": str(self.api_key or ""),
            "format": "json",
            "autocorrect": "1",
        }

        try:
            data = await self._make_api_request("lastfm", url, params=params)

            if not data:
                return None

            if "error" in data:
                self.console_logger.warning(
                    "Last.fm API error %s: %s",
                    data.get("error"),
                    data.get("message", self.DEFAULT_ERROR_MESSAGE),
                )
                return None

            if "album" in data and isinstance(data["album"], dict):
                return cast(LastFmAlbum, data["album"])

        except (OSError, ValueError, KeyError, TypeError):
            self.error_logger.exception("Error fetching from Last.fm")

        return None

    async def _perform_cleaned_search(self, artist: str, album: str) -> LastFmAlbum | None:
        """Perform search with cleaned album name (Fallback 1).

        Strips common suffixes and content after colons that cause lookup failures.

        Args:
            artist: Artist name
            album: Original album name

        Returns:
            Album data if found, None otherwise

        """
        cleaned_album = self._clean_album_for_search(album)
        if not cleaned_album:
            return None

        self.console_logger.debug(
            "Last.fm fallback 1: trying cleaned album '%s' -> '%s'",
            album,
            cleaned_album,
        )
        return await self.get_album_info(artist, cleaned_album)

    async def _perform_fuzzy_search(self, artist: str, album: str) -> LastFmAlbum | None:
        """Perform fuzzy search using album.search API (Fallback 2).

        Searches by album name only, then filters results by artist match.

        Args:
            artist: Target artist name
            album: Album name to search for

        Returns:
            Album data if found and artist matches, None otherwise

        """
        # Try with original album first, then cleaned version
        search_terms = [album]
        cleaned_album = self._clean_album_for_search(album)
        if cleaned_album:
            search_terms.append(cleaned_album)

        for search_term in search_terms:
            results = await self._search_albums(search_term)

            for result in results:
                result_artist = result.get("artist", "")
                if self._is_artist_match(result_artist, artist):
                    # Found a match - get full album info
                    result_album = result.get("name", "")
                    self.console_logger.debug(
                        "Last.fm fallback 2: matched '%s - %s' via album.search",
                        result_artist,
                        result_album,
                    )
                    # Fetch full album info for this match
                    return await self.get_album_info(result_artist, result_album)

        return None

    @Analytics.track_instance_method("lastfm_release_search")
    async def get_scored_releases(
        self,
        artist_norm: str,
        album_norm: str,
    ) -> list[ScoredRelease]:
        """Retrieve album release year from Last.fm and return scored releases.

        Uses a 3-level fallback strategy (similar to Discogs):
        1. Primary: album.getInfo with exact artist + album
        2. Fallback 1: album.getInfo with cleaned album name (suffixes stripped)
        3. Fallback 2: album.search with artist post-filter

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name

        Returns:
            List of scored releases (usually 0 or 1 for Last.fm)

        """
        scored_releases: list[ScoredRelease] = []

        if not self.use_lastfm:
            return []

        try:
            self.console_logger.debug("Searching Last.fm for: '%s - %s'", artist_norm, album_norm)

            # Primary: Exact album.getInfo, then fallbacks (short-circuit evaluation)
            album_data = (
                await self.get_album_info(artist_norm, album_norm)
                or await self._perform_cleaned_search(artist_norm, album_norm)
                or await self._perform_fuzzy_search(artist_norm, album_norm)
            )

            if not album_data:
                self.console_logger.warning(
                    "Last.fm all strategies failed for '%s - %s'",
                    artist_norm,
                    album_norm,
                )
                return []

            # Extract year information
            year = self._extract_year_from_lastfm_data(album_data)

            if year and self._is_valid_year(year):
                # Basic assumptions for Last.fm data
                release_type = "Album"
                status = "Official"

                # Prepare data for scoring
                release_info: ScoredRelease = {
                    "title": album_data.get("name", album_norm),
                    "year": year,
                    "score": 0.0,
                    "artist": album_data.get("artist", artist_norm),
                    "album_type": release_type,
                    "country": None,  # Not provided by Last.fm
                    "status": status,
                    "format": None,  # Not provided
                    "label": None,  # Not provided
                    "catalog_number": None,  # Not provided
                    "barcode": None,  # Not provided
                    "disambiguation": None,
                    "source": "lastfm",
                }

                # Score this result
                release_info["score"] = self._score_original_release(
                    release_info,
                    artist_norm,
                    album_norm,
                    artist_region=None,
                    source="lastfm",
                )

                if release_info["score"] > 0:
                    scored_releases.append(release_info)
                    self.console_logger.info(
                        "Scored LastFM Release: '%s' (%s) Score: %s",
                        release_info["title"],
                        release_info["year"],
                        release_info["score"],
                    )

        except (OSError, ValueError, KeyError, TypeError):
            self.error_logger.exception("Error retrieving from Last.fm for '%s - %s'", artist_norm, album_norm)

        return scored_releases

    @Analytics.track_instance_method("lastfm_artist_search")
    async def get_artist_info(
        self,
        artist: str,
    ) -> LastFmArtist | None:
        """Get artist information from Last.fm.

        Args:
            artist: Artist name

        Returns:
            Artist information or None if not found

        """
        if not self.use_lastfm:
            return None

        url = self.LASTFM_API_URL
        params: dict[str, str] = {
            "method": "artist.getInfo",
            "artist": artist,
            "api_key": str(self.api_key or ""),
            "format": "json",
            "autocorrect": "1",
        }

        try:
            data = await self._make_api_request("lastfm", url, params=params)

            if not data:
                return None

            if "error" in data:
                self.console_logger.warning(
                    "Last.fm API error %s: %s",
                    data.get("error"),
                    data.get("message", self.DEFAULT_ERROR_MESSAGE),
                )
                return None

            if "artist" in data and isinstance(data["artist"], dict):
                return cast(LastFmArtist, data["artist"])

        except (OSError, ValueError, KeyError, TypeError):
            self.error_logger.exception("Error fetching artist from Last.fm")

        return None
