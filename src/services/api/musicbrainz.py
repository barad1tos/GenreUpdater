"""MusicBrainz API client for music metadata retrieval.

This module provides the MusicBrainz-specific implementation for fetching
and scoring music releases from the MusicBrainz database.
"""

import asyncio
import logging
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict, cast

from src.core.models.track_models import MBArtist
from src.metrics import Analytics

from .api_base import BaseApiClient, ScoredRelease

# Type alias for MusicBrainz API response data
MBApiData = dict[str, Any]


# MusicBrainz Type Definitions
class LifeSpan(TypedDict, total=False):
    """Type definition for artist life span data from MusicBrainz."""

    begin: str | None
    end: str | None
    ended: bool | None


class Area(TypedDict, total=False):
    """Type definition for area data from MusicBrainz."""

    type: str | None
    name: str | None
    sort_name: str | None


class Alias(TypedDict, total=False):
    """Type definition for artist alias from MusicBrainz."""

    name: str | None
    sort_name: str | None
    type: str | None
    primary: bool | None
    locale: str | None


# Use unified artist type from models
Artist = MBArtist


class ArtistCredit(TypedDict, total=False):
    """Type definition for artist credit from MusicBrainz."""

    artist: Artist
    name: str | None
    joinphrase: str | None  # MusicBrainz API field name (e.g., " feat. ", " & ")


class TextRepresentation(TypedDict, total=False):
    """Type definition for text representation from MusicBrainz."""

    language: str | None
    script: str | None


class ReleaseEvent(TypedDict, total=False):
    """Type definition for release event from MusicBrainz."""

    date: str | None
    area: Area | None


class Label(TypedDict, total=False):
    """Type definition for a label from MusicBrainz."""

    id: str
    name: str
    disambiguation: str | None
    label_code: str | None


class LabelInfo(TypedDict, total=False):
    """Type definition for label info from MusicBrainz."""

    label: Label | None
    catalog_number: str | None


class CoverArtArchive(TypedDict, total=False):
    """Type definition for cover arts archive from MusicBrainz."""

    artwork: bool
    count: int
    front: bool
    back: bool


class Recording(TypedDict, total=False):
    """Type definition for recording from MusicBrainz."""

    id: str
    title: str
    length: int | None
    disambiguation: str | None
    artist_credit: list[ArtistCredit] | None


class Track(TypedDict, total=False):
    """Type definition for a track from MusicBrainz."""

    id: str
    number: str | None
    title: str
    recording: Recording | None
    length: int | None
    position: int | None


class Medium(TypedDict, total=False):
    """Type definition for medium from MusicBrainz."""

    format: str | None
    disc_count: int | None
    track_count: int | None
    tracks: list[Track] | None
    title: str | None
    position: int | None


class Release(TypedDict, total=False):
    """Type definition for release from MusicBrainz."""

    id: str
    title: str
    status: str | None
    status_id: str | None
    packaging: str | None
    barcode: str | None
    country: str | None
    date: str | None
    year: int | None
    disambiguation: str | None
    artist_credit: list[ArtistCredit] | None
    release_events: list[ReleaseEvent] | None
    label_info: list[LabelInfo] | None
    media: list[Medium] | None
    text_representation: TextRepresentation | None
    cover_art_archive: CoverArtArchive | None
    release_group: dict[str, Any] | None


class ReleaseGroup(TypedDict, total=False):
    """Type definition for a release group from MusicBrainz."""

    id: str
    title: str
    primary_type: str | None
    primary_type_id: str | None
    secondary_types: list[str] | None
    secondary_type_ids: list[str] | None
    artist_credit: list[ArtistCredit] | None
    releases: list[Release] | None
    first_release_date: str | None
    disambiguation: str | None


class MusicBrainzReleasesResponse(TypedDict, total=False):
    """Type definition for MusicBrainz releases API response."""

    releases: list[Release]
    release_count: int
    release_offset: int


class MusicBrainzClient(BaseApiClient):
    """MusicBrainz API client for fetching music metadata."""

    def __init__(
        self,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        make_api_request_func: Callable[..., Awaitable[dict[str, Any] | None]],
        score_release_func: Callable[..., float],
    ) -> None:
        """Initialize MusicBrainz client.

        Args:
            console_logger: Logger for console output
            error_logger: Logger for error messages
            make_api_request_func: Function to make API requests with rate limiting
            score_release_func: Function to score releases for originality

        """
        super().__init__(console_logger, error_logger)
        self._make_api_request = make_api_request_func
        self._score_original_release = score_release_func

    @staticmethod
    def _escape_lucene(term: str) -> str:
        """Escape special characters for Lucene query syntax.

        Args:
            term: Search term to escape

        Returns:
            Escaped search term

        """
        term = term.replace("\\", "\\\\")
        # List of special characters to escape
        for char in r'+-&|!(){}[]^"~*?:\/':
            term = term.replace(char, f"\\{char}")
        return term

    def _filter_release_groups_by_artist(self, release_groups: list[dict[str, Any]], artist_norm: str) -> list[dict[str, Any]]:
        """Filter release groups to match the target artist.

        Args:
            release_groups: List of release groups from MusicBrainz
            artist_norm: Normalized artist name to match

        Returns:
            Filtered list of release groups matching the artist

        """
        matching_groups: list[dict[str, Any]] = []

        for rg in release_groups:
            artist_credits = rg.get("artist-credit", [])
            if not artist_credits:
                continue

            # Check if any artist credit matches our target artist
            if self._artist_matches_any_credit(artist_credits, artist_norm):
                matching_groups.append(rg)

        return matching_groups

    def _artist_matches_any_credit(self, artist_credits: list[dict[str, Any]], artist_norm: str) -> bool:
        """Check if artist matches any credit by name or alias.

        Args:
            artist_credits: List of artist credits from release group
            artist_norm: Normalized artist name to match

        Returns:
            True if artist matches any credit, False otherwise

        """
        for ac in artist_credits:
            artist_info = ac.get("artist", {})
            artist_name = artist_info.get("name", "")

            # Check direct name match
            if self._normalize_name(artist_name) == artist_norm:
                return True

            # Check aliases
            aliases = artist_info.get("aliases", [])
            for alias in aliases:
                alias_name = alias.get("name", "")
                if self._normalize_name(alias_name) == artist_norm:
                    return True

        return False

    @Analytics.track_instance_method("musicbrainz_artist_search")
    async def get_artist_info(self, artist_norm: str, include_aliases: bool = False) -> dict[str, Any] | None:
        """Get artist information from MusicBrainz.

        Args:
            artist_norm: Normalized artist name
            include_aliases: Whether to include aliases in the response

        Returns:
            Artist information or None if not found

        """
        search_url = "https://musicbrainz.org/ws/2/artist/"
        params = {
            "query": f'artist:"{self._escape_lucene(artist_norm)}"',
            "fmt": "json",
            "limit": "1",
        }

        if include_aliases:
            params["inc"] = "aliases"

        try:
            response = await self._make_api_request("musicbrainz", search_url, params=params)
            if response and response.get("artists"):
                return cast(MBApiData, response["artists"][0])
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
            self.error_logger.exception(f"Failed to get artist info for '{artist_norm}': {e}")

        return None

    @Analytics.track_instance_method("musicbrainz_artist_period")
    async def get_artist_activity_period(self, artist_norm: str) -> tuple[str | None, str | None]:
        """Get artist's activity period from MusicBrainz.

        Args:
            artist_norm: Normalized artist name

        Returns:
            Tuple of (begin_year, end_year) or (None, None) if not found

        """
        artist_info = await self.get_artist_info(artist_norm)

        if not artist_info:
            return None, None

        life_span = artist_info.get("life-span", {})
        begin = life_span.get("begin")
        end = life_span.get("end")

        # Extract years from dates
        begin_year = self._extract_year_from_date(begin) if begin else None
        end_year = self._extract_year_from_date(end) if end else None

        return begin_year, end_year

    @Analytics.track_instance_method("musicbrainz_artist_region")
    async def get_artist_region(self, artist_norm: str) -> str | None:
        """Get an artist's region/country from MusicBrainz.

        Args:
            artist_norm: Normalized artist name

        Returns:
            Region/country name or None if not found

        """
        artist_info = await self.get_artist_info(artist_norm)

        if not artist_info:
            return None

        # Try different area fields
        for area_field in ["area", "begin-area", "end-area"]:
            area = artist_info.get(area_field)
            if area and area.get("name"):
                return cast("str", area["name"])

        return None

    async def _perform_primary_search(self, artist_norm: str, album_norm: str) -> list[MBApiData]:
        """Perform a precise fielded search for release groups.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name

        Returns:
            List of release groups from primary search

        """
        base_search_url = "https://musicbrainz.org/ws/2/release-group/"
        primary_query = f'artist:"{self._escape_lucene(artist_norm)}" AND releasegroup:"{self._escape_lucene(album_norm)}"'
        params_rg1 = {"fmt": "json", "limit": "10", "query": primary_query}

        url_rg1 = f"{base_search_url}?{urllib.parse.urlencode(params_rg1)}"
        self.console_logger.debug(f"[musicbrainz] Attempt 1 URL: {url_rg1}")

        rg_data1 = await self._make_api_request("musicbrainz", base_search_url, params=params_rg1)

        if rg_data1 and rg_data1.get("count", 0) > 0 and rg_data1.get("release-groups"):
            self.console_logger.debug(f"[musicbrainz] Attempt 1 successful. Found {len(rg_data1['release-groups'])} release groups.")
            return cast("list[MBApiData]", rg_data1["release-groups"])

        self.console_logger.debug("[musicbrainz] Attempt 1 failed. Trying fallbacks.")
        return []

    async def _perform_fallback_searches(
        self,
        artist_norm: str,
        album_norm: str,
        artist_orig: str | None,
        album_orig: str | None,
    ) -> list[MBApiData]:
        """Perform fallback searches with broader queries.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_orig: Original artist name
            album_orig: Original album name

        Returns:
            List of release groups from fallback searches

        """
        all_groups: list[MBApiData] = []
        base_search_url = "https://musicbrainz.org/ws/2/release-group/"

        # Attempt 2: Broader search
        artist_fb = artist_orig or artist_norm
        album_fb = album_orig or album_norm
        secondary_query = f"{artist_fb} {album_fb}"
        params_rg2 = {"fmt": "json", "limit": "10", "query": secondary_query}

        rg_data2 = await self._make_api_request("musicbrainz", base_search_url, params=params_rg2)

        if rg_data2 and rg_data2.get("count", 0) > 0 and rg_data2.get("release-groups"):
            filtered_rgs = self._filter_release_groups_by_artist(rg_data2["release-groups"], artist_norm)
            self.console_logger.debug(f"[musicbrainz] Attempt 2 successful. Found {len(filtered_rgs)} matching groups after filtering.")
            all_groups.extend(filtered_rgs)

        if not all_groups:
            # Attempt 3: Album title only
            tertiary_query = f"{album_fb}"
            params_rg3 = {"fmt": "json", "limit": "10", "query": tertiary_query}

            rg_data3 = await self._make_api_request("musicbrainz", base_search_url, params=params_rg3)

            if rg_data3 and rg_data3.get("count", 0) > 0 and rg_data3.get("release-groups"):
                filtered_rgs = self._filter_release_groups_by_artist(rg_data3["release-groups"], artist_norm)
                self.console_logger.debug(f"[musicbrainz] Attempt 3 successful. Found {len(filtered_rgs)} matching groups after filtering.")
                all_groups.extend(filtered_rgs)

        return all_groups

    async def _fetch_releases_for_groups(self, release_groups: list[MBApiData]) -> list[tuple[MBApiData | None, MBApiData]]:
        """Fetch releases for given release groups.

        Args:
            release_groups: List of release groups

        Returns:
            List of (release_data, group_info) tuples

        """
        release_fetch_tasks: list[tuple[Awaitable[MBApiData | None], MBApiData]] = []
        max_groups_to_process = 3

        for rg_info in release_groups[:max_groups_to_process]:
            rg_id = rg_info.get("id")
            if not rg_id:
                continue

            release_search_url = "https://musicbrainz.org/ws/2/release/"
            release_params: dict[str, str] = {
                "release-group": rg_id,
                "inc": "media",
                "fmt": "json",
                "limit": "100",
            }

            task = self._make_api_request("musicbrainz", release_search_url, params=release_params)
            release_fetch_tasks.append((task, rg_info))

        results = await asyncio.gather(*[t[0] for t in release_fetch_tasks], return_exceptions=True)

        processed_results: list[tuple[MBApiData | None, MBApiData]] = []
        for i, result in enumerate(results):
            rg_info = release_fetch_tasks[i][1]

            if isinstance(result, Exception):
                self.error_logger.warning(f"Failed to fetch releases for MB RG ID {rg_info.get('id')}: {result}")
                processed_results.append((None, rg_info))
                continue

            if not result or not isinstance(result, dict) or "releases" not in result:
                processed_results.append((None, rg_info))
                continue

            processed_results.append((result, rg_info))

        return processed_results

    def _process_and_score_releases(
        self,
        release_results: list[tuple[MBApiData | None, MBApiData]],
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
    ) -> list[ScoredRelease]:
        """Process and score releases from fetched data.

        Args:
            release_results: List of (release_data, group_info) tuples
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_region: Artist's region for scoring

        Returns:
            List of scored releases

        """
        scored_releases: list[ScoredRelease] = []
        processed_release_ids: set[str] = set()

        for result, rg_info in release_results:
            if not result:
                continue

            # Result is already dict[str, Any], no need to cast to TypedDict
            # Just assert the structure we expect
            releases_list = result.get("releases", []) if isinstance(result, dict) else []

            for release in releases_list:
                release_id = release.get("id")
                if not release_id or release_id in processed_release_ids:
                    continue
                processed_release_ids.add(release_id)

                # Combine release and release group info for scoring
                release_to_score: MBApiData = {**release, "release_group": rg_info}

                score = self._score_original_release(
                    release_to_score,
                    artist_norm,
                    album_norm,
                    artist_region=artist_region,
                    source="musicbrainz",
                )

                if score > 0:
                    release_info = self._create_scored_release(release, rg_info, score, artist_norm)
                    scored_releases.append(release_info)

        return scored_releases

    def _create_scored_release(
        self,
        release: dict[str, Any],
        rg_info: dict[str, Any],
        score: float,
        artist_norm: str,
    ) -> ScoredRelease:
        """Create a ScoredRelease from MusicBrainz release data.

        Args:
            release: MusicBrainz release data
            rg_info: Release group information
            score: Calculated score for the release
            artist_norm: Normalized artist name

        Returns:
            ScoredRelease object with all fields populated

        """
        year_str = self._extract_year_from_date(release.get("date")) or self._extract_year_from_date(
            rg_info.get("first-release-date"),
        )

        return {
            "title": release.get("title", "") or "",
            "year": year_str,
            "score": score,
            "artist": artist_norm,
            "album_type": rg_info.get("primary-type", "Album"),
            "country": release.get("country", "") or "",
            "status": release.get("status", "Official"),
            "format": self._get_format_from_media(release.get("media")),
            "label": self._get_label_name(cast("list[LabelInfo] | None", release.get("label-info"))),
            "catalog_number": self._get_catalog_number(cast("list[LabelInfo] | None", release.get("label-info"))),
            "barcode": release.get("barcode"),
            "disambiguation": release.get("disambiguation"),
            "source": "musicbrainz",
        }

    @Analytics.track_instance_method("musicbrainz_release_search")
    async def get_scored_releases(
        self,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
        *,
        artist_orig: str | None = None,
        album_orig: str | None = None,
    ) -> list[ScoredRelease]:
        """Retrieve and score releases from MusicBrainz.

        Uses multiple search strategies with fallbacks if precise queries fail.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_region: Artist's region for scoring
            artist_orig: Original artist name (before normalization)
            album_orig: Original album name (before normalization)

        Returns:
            List of scored releases sorted by score

        """
        self.console_logger.debug(
            f"[musicbrainz] Start search | artist_orig='{artist_orig or artist_norm}' "
            f"artist_norm='{artist_norm}', album_orig='{album_orig or album_norm}', album_norm='{album_norm}'",
        )

        try:
            # Attempt primary search first
            all_release_groups = await self._perform_primary_search(artist_norm, album_norm) or await self._perform_fallback_searches(
                artist_norm, album_norm, artist_orig, album_orig
            )

            if not all_release_groups:
                self.console_logger.warning(f"[musicbrainz] All search attempts failed for '{artist_norm} - {album_norm}'.")
                return []

            # Fetch releases for found release groups
            release_results = await self._fetch_releases_for_groups(all_release_groups)

            # Process and score the releases
            scored_releases = self._process_and_score_releases(release_results, artist_norm, album_norm, artist_region)

        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            self.error_logger.exception(f"Error fetching from MusicBrainz for '{artist_norm} - {album_norm}': {e}")
            return []

        return sorted(scored_releases, key=lambda x: x["score"], reverse=True)

    @staticmethod
    def _get_format_from_media(media: list[Medium] | list[dict[str, Any]] | None) -> str | None:
        """Extract format information from a media list.

        Args:
            media: List of media information from MusicBrainz

        Returns:
            Format string or None

        """
        if not media:
            return None

        formats: list[str] = [format_str for medium in media if (format_str := medium.get("format"))]

        return ", ".join(formats) if formats else None

    @staticmethod
    def _get_label_name(label_info: list[LabelInfo] | list[dict[str, Any]] | None) -> str | None:
        """Extract a label name from label info.

        Args:
            label_info: List of label information

        Returns:
            Label name or None

        """
        if not label_info:
            return None

        for info in label_info:
            label = info.get("label")
            if label and "name" in label and label["name"]:
                return cast("str", label["name"])

        return None

    @staticmethod
    def _get_catalog_number(label_info: list[LabelInfo] | list[dict[str, Any]] | None) -> str | None:
        """Extract a catalog number from label info.

        Args:
            label_info: List of label information

        Returns:
            Catalog number or None

        """
        if not label_info:
            return None

        for info in label_info:
            # Access using dict key since API returns with dash, not underscore
            if isinstance(info, dict) and (catalog := info.get("catalog-number")):
                return cast("str", catalog)

        return None
