"""Year search coordination logic extracted from ExternalApiOrchestrator.

This module handles the coordination of API calls to fetch release year
information from multiple providers (MusicBrainz, Discogs, Last.fm, Apple Music).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, cast

from core.debug_utils import debug
from core.models.script_detection import ScriptType, detect_primary_script
from core.models.search_strategy import SearchStrategy, detect_search_strategy

if TYPE_CHECKING:
    import logging
    from collections.abc import Coroutine

    from services.api.applemusic import AppleMusicClient
    from services.api.api_base import ScoredRelease
    from services.api.discogs import DiscogsClient
    from services.api.lastfm import LastFmClient
    from services.api.musicbrainz import MusicBrainzClient
    from services.api.year_scoring import ReleaseScorer


class _RegionAwareApi(Protocol):
    """Protocol for APIs that accept artist_region parameter."""

    async def get_scored_releases(self, artist_norm: str, album_norm: str, artist_region: str | None) -> list[ScoredRelease]:
        """Get scored releases with region awareness."""
        ...


class _SimpleApi(Protocol):
    """Protocol for APIs that don't accept artist_region parameter."""

    async def get_scored_releases(self, artist_norm: str, album_norm: str) -> list[ScoredRelease]:
        """Get scored releases."""
        ...


class YearSearchCoordinator:
    """Coordinates API calls to fetch release year information.

    Handles:
    - Script-optimized search (Cyrillic, CJK, etc.)
    - Concurrent API queries across multiple providers
    - API priority ordering based on configuration
    """

    def __init__(
        self,
        *,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: dict[str, Any],
        preferred_api: str,
        use_lastfm: bool,
        musicbrainz_client: MusicBrainzClient,
        discogs_client: DiscogsClient,
        lastfm_client: LastFmClient,
        applemusic_client: AppleMusicClient,
        release_scorer: ReleaseScorer,
    ) -> None:
        """Initialize the year search coordinator.

        Args:
            console_logger: Logger for console output
            error_logger: Logger for error output
            config: Full application config dict
            preferred_api: Preferred API name for ordering
            use_lastfm: Whether to include Last.fm in searches
            musicbrainz_client: MusicBrainz API client
            discogs_client: Discogs API client
            lastfm_client: Last.fm API client
            applemusic_client: Apple Music API client
            release_scorer: Release scoring service

        """
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.config = config
        self.preferred_api = preferred_api
        self.use_lastfm = use_lastfm
        self.musicbrainz_client = musicbrainz_client
        self.discogs_client = discogs_client
        self.lastfm_client = lastfm_client
        self.applemusic_client = applemusic_client
        self.release_scorer = release_scorer

    async def fetch_all_api_results(
        self,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
        log_artist: str,
        log_album: str,
    ) -> list[ScoredRelease]:
        """Fetch scored releases from all API providers with script-aware logic."""
        self._log_api_search_start(artist_norm, album_norm, artist_region, log_artist, log_album)

        # Try script-optimized search first
        artist_script = detect_primary_script(log_artist)
        album_script = detect_primary_script(log_album)
        primary_script = artist_script if artist_script != ScriptType.UNKNOWN else album_script

        if primary_script not in (ScriptType.LATIN, ScriptType.UNKNOWN):
            script_results = await self._try_script_optimized_search(primary_script, artist_norm, album_norm, artist_region)
            if script_results:
                return script_results

        # Standard API search (all providers concurrently)
        results = await self._execute_standard_api_search(artist_norm, album_norm, artist_region, log_artist, log_album)
        if results:
            return results

        # Fallback: try alternative search strategy
        return await self._try_alternative_search(album_norm, artist_region, log_artist, log_album)

    def _log_api_search_start(
        self,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
        log_artist: str,
        log_album: str,
    ) -> None:
        """Log API search initialization."""
        if not debug.api:
            return

        self.console_logger.info(
            "Starting API search with parameters: artist_norm='%s', album_norm='%s', artist_region='%s'",
            artist_norm,
            album_norm,
            artist_region or "None",
        )
        self.console_logger.info("Original names: artist='%s', album='%s'", log_artist, log_album)

    async def _try_script_optimized_search(
        self,
        script_type: ScriptType,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
    ) -> list[ScoredRelease] | None:
        """Try script-optimized API search based on detected script type."""
        if debug.api:
            self.console_logger.info(f"{script_type.value} detected - trying script-optimized search")

        api_lists = self._get_script_api_priorities(script_type)

        # Try primary APIs first
        results = await self._try_api_list(api_lists["primary"], artist_norm, album_norm, artist_region, script_type, is_fallback=False)
        if results:
            return results

        # Try fallback APIs if primary failed
        if debug.api:
            self.console_logger.info(f"Primary APIs failed for {script_type.value} - trying fallback")
        return await self._try_api_list(api_lists["fallback"], artist_norm, album_norm, artist_region, script_type, is_fallback=True)

    def _get_script_api_priorities(self, script_type: ScriptType) -> dict[str, list[str]]:
        """Get script-specific API priorities from config."""
        script_priorities = self._get_script_config_priorities(script_type)
        primary_raw = script_priorities.get("primary", ["musicbrainz"])
        fallback_raw = script_priorities.get("fallback", ["lastfm"])

        primary = primary_raw if isinstance(primary_raw, list) else ["musicbrainz"]
        fallback = fallback_raw if isinstance(fallback_raw, list) else ["lastfm"]

        return {
            "primary": self._apply_preferred_order(primary),
            "fallback": self._apply_preferred_order(fallback),
        }

    def _get_script_config_priorities(self, script_type: ScriptType) -> dict[str, Any]:
        """Get script-specific API priorities from configuration file."""
        year_config = self.config.get("year_retrieval", {})
        script_api_priorities = year_config.get("script_api_priorities", {})
        default_config = script_api_priorities.get("default", {})
        script_priorities: dict[str, Any] = script_api_priorities.get(script_type.value, default_config)
        return script_priorities

    def _apply_preferred_order(self, api_list: list[str]) -> list[str]:
        """Apply preferred API ordering to a list."""
        if not self.preferred_api or self.preferred_api not in api_list:
            return api_list

        # Move preferred API to front
        result = [self.preferred_api]
        result.extend(api for api in api_list if api != self.preferred_api)
        return result

    @staticmethod
    def _normalize_api_name(api_name: Any) -> str:
        """Normalize API name to lowercase string."""
        if isinstance(api_name, str):
            return api_name.lower().strip()
        return str(api_name).lower().strip() if api_name else "unknown"

    async def _try_api_list(
        self,
        api_names: list[str],
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
        script_type: ScriptType,
        is_fallback: bool,
    ) -> list[ScoredRelease] | None:
        """Try a list of API names and return the first successful result."""
        normalized_names = [self._normalize_api_name(name) for name in api_names]
        for api_name in normalized_names:
            results = await self._try_single_api(api_name, artist_norm, album_norm, artist_region, script_type, is_fallback)
            if results:
                return results
        return None

    async def _try_single_api(
        self,
        api_name: str,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
        script_type: ScriptType,
        is_fallback: bool,
    ) -> list[ScoredRelease] | None:
        """Try a single API and return results if successful."""
        try:
            api_client = self._get_api_client(api_name)
            if not api_client:
                if debug.api and not is_fallback:
                    self.console_logger.debug(f"{api_name} client not available, skipping")
                return None

            if debug.api:
                self.console_logger.info(f"Trying {api_name} for {script_type.value} text")
            results: list[ScoredRelease] = await self._call_api_with_proper_params(api_client, api_name, artist_norm, album_norm, artist_region)

            if results:
                if debug.api:
                    result_type = "Fallback" if is_fallback else "Primary"
                    self.console_logger.info(
                        f"{result_type} {api_name} found %d results for {script_type.value}",
                        len(results),
                    )
                return results

        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            if debug.api:
                self.console_logger.warning(f"{api_name} failed for {script_type.value}: %s", e)

        return None

    @staticmethod
    async def _call_api_with_proper_params(
        api_client: MusicBrainzClient | DiscogsClient | LastFmClient | AppleMusicClient,
        api_name: str,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
    ) -> list[ScoredRelease]:
        """Call API with proper parameters based on what the API accepts.

        MusicBrainz and Discogs accept artist_region parameter.
        LastFm and AppleMusic don't accept artist_region parameter.
        """
        if api_name in {"musicbrainz", "discogs"}:
            # Cast to protocol that accepts artist_region
            return await cast(_RegionAwareApi, api_client).get_scored_releases(artist_norm, album_norm, artist_region)
        # Cast to protocol that doesn't accept artist_region
        return await cast(_SimpleApi, api_client).get_scored_releases(artist_norm, album_norm)

    def _get_api_client(self, api_name: str) -> MusicBrainzClient | DiscogsClient | LastFmClient | AppleMusicClient | None:
        """Get API client by name."""
        api_mapping: dict[str, MusicBrainzClient | DiscogsClient | LastFmClient | AppleMusicClient] = {
            "musicbrainz": self.musicbrainz_client,
            "discogs": self.discogs_client,
            "lastfm": self.lastfm_client,
            "itunes": self.applemusic_client,
            "applemusic": self.applemusic_client,
        }
        return api_mapping.get(api_name)

    async def _execute_standard_api_search(
        self,
        artist_norm: str,
        album_norm: str,
        artist_region: str | None,
        log_artist: str,
        log_album: str,
    ) -> list[ScoredRelease]:
        """Execute standard concurrent API search across all providers."""
        api_order = self._apply_preferred_order(["musicbrainz", "discogs", "itunes"] + (["lastfm"] if self.use_lastfm else []))
        api_tasks: list[Coroutine[Any, Any, list[ScoredRelease]]] = [
            self._call_api_with_proper_params(api_client, api_name, artist_norm, album_norm, artist_region)
            for api_name in api_order
            if (api_client := self._get_api_client(api_name))
        ]

        # Execute all API calls concurrently
        results = await asyncio.gather(*api_tasks, return_exceptions=True)

        # Process results
        return self._process_api_task_results(results, api_order, log_artist, log_album)

    async def _try_alternative_search(
        self,
        album_norm: str,
        artist_region: str | None,
        log_artist: str,
        log_album: str,
    ) -> list[ScoredRelease]:
        """Try alternative search strategy when standard search fails."""
        strategy_info = detect_search_strategy(log_artist, log_album, self.config)

        if strategy_info.strategy == SearchStrategy.NORMAL:
            return []

        alt_artist = strategy_info.modified_artist
        alt_album = strategy_info.modified_album

        alt_artist_norm = alt_artist.lower().strip() if alt_artist else ""
        alt_album_norm = alt_album.lower().strip() if alt_album else album_norm

        self.console_logger.info(
            "Alternative search: %s - %s -> strategy=%s, query=(%s, %s)",
            log_artist,
            log_album,
            strategy_info.strategy.value,
            alt_artist or "(none)",
            alt_album or log_album,
        )

        return await self._execute_standard_api_search(
            alt_artist_norm,
            alt_album_norm,
            artist_region,
            alt_artist or log_artist,
            alt_album or log_album,
        )

    def _process_api_task_results(
        self,
        results: list[list[ScoredRelease] | BaseException],
        api_order: list[str],
        log_artist: str,
        log_album: str,
    ) -> list[ScoredRelease]:
        """Process results from concurrent API tasks."""
        all_releases: list[ScoredRelease] = []

        for api_name, result in zip(api_order, results, strict=True):
            if isinstance(result, BaseException):
                self._log_api_error(api_name, log_artist, log_album, result)
            elif result:
                all_releases.extend(result)
            elif debug.api:
                self._log_empty_api_result(api_name, log_artist, log_album)

        if debug.api:
            self._log_api_summary(log_artist, log_album, len(all_releases))

        return all_releases

    def _log_api_error(self, api_name: str, log_artist: str, log_album: str, error: BaseException) -> None:
        """Log API error."""
        self.error_logger.warning(
            "[%s] Error fetching release for '%s - %s': %s",
            api_name,
            log_artist,
            log_album,
            error,
        )

    def _log_empty_api_result(self, api_name: str, log_artist: str, log_album: str) -> None:
        """Log empty API result for debugging."""
        self.console_logger.debug(
            "[%s] No results for '%s - %s'",
            api_name,
            log_artist,
            log_album,
        )

    def _log_api_summary(self, log_artist: str, log_album: str, total_releases: int) -> None:
        """Log summary of API search results."""
        self.console_logger.info(
            "API search complete for '%s - %s': %d total releases found",
            log_artist,
            log_album,
            total_releases,
        )
