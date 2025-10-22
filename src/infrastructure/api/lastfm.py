"""Last.fm API client for music metadata retrieval.

This module provides the Last.fm-specific implementation for fetching
album information and release years.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict, cast

from src.shared.monitoring import Analytics

from .base import BaseApiClient, ScoredRelease


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


class LastFmBio(TypedDict, total=False):
    """Type definition for biography from Last.fm."""

    published: str | None
    summary: str | None
    content: str | None


class LastFmWiki(TypedDict, total=False):
    """Type definition for wiki from Last.fm."""

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
    bio: LastFmBio | None
    wiki: LastFmWiki | None


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
    wiki: LastFmWiki | None


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
    wiki: LastFmWiki | None
    releasedate: str | None


class LastFmClient(BaseApiClient):
    """Last.fm API client for fetching music metadata."""

    def __init__(
        self,
        api_key: str,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        make_api_request_func: Callable[..., Awaitable[dict[str, Any] | None]],
        score_release_func: Callable[..., float],
        use_lastfm: bool = True,
    ) -> None:
        """Initialize Last.fm client.

        Args:
            api_key: Last.fm API key
            console_logger: Logger for console output
            error_logger: Logger for error messages
            make_api_request_func: Function to make API requests with rate-limiting
            score_release_func: Function to score releases for originality
            use_lastfm: Whether to use Last.fm API

        """
        super().__init__(console_logger, error_logger)
        self.api_key = api_key
        self._make_api_request = make_api_request_func
        self._score_original_release = score_release_func
        self.use_lastfm = use_lastfm

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

        # Cast tags to the proper type after isinstance check
        # Note: LastFM API sometimes returns list[str] instead of list[LastFmTag]
        tags_typed = cast("dict[str, list[LastFmTag | str]]", tags)
        if tag_list := tags_typed.get("tag", []):
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

        url = "https://ws.audioscrobbler.com/2.0/"
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
                    data.get("message", "Unknown error"),
                )
                return None

            if "album" in data and isinstance(data["album"], dict):
                return cast("LastFmAlbum", data["album"])

        except (OSError, ValueError, KeyError, TypeError):
            self.error_logger.exception("Error fetching from Last.fm")

        return None

    @Analytics.track_instance_method("lastfm_release_search")
    async def get_scored_releases(
        self,
        artist_norm: str,
        album_norm: str,
    ) -> list[ScoredRelease]:
        """Retrieve album release year from Last.fm and return scored releases.

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

            album_data = await self.get_album_info(artist_norm, album_norm)

            if not album_data:
                self.console_logger.warning("Last.fm getInfo failed for '%s - %s'", artist_norm, album_norm)
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

        url = "https://ws.audioscrobbler.com/2.0/"
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
                    data.get("message", "Unknown error"),
                )
                return None

            if "artist" in data and isinstance(data["artist"], dict):
                return cast("LastFmArtist", data["artist"])

        except (OSError, ValueError, KeyError, TypeError):
            self.error_logger.exception("Error fetching artist from Last.fm")

        return None
