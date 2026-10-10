"""Year search coordination logic extracted from ExternalApiOrchestrator.

This module handles the coordination of API calls to fetch release year
information from multiple providers (MusicBrainz, Discogs, Apple Music).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from core.debug_utils import debug
from core.models.protocols import YearLookupUnavailableError
from core.models.script_detection import ScriptType, detect_primary_script
from core.models.search_strategy import SearchStrategy, detect_search_strategy
from services.api.request_executor import ApiRequestError

if TYPE_CHECKING:
    from core.models.release_record import ReleaseRecord
    import logging
    from collections.abc import Coroutine

    from core.models.protocols import CacheServiceProtocol
    from core.models.track_models import AppConfig
    from services.api.api_base import ScoredRelease
    from services.api.applemusic import AppleMusicClient
    from services.api.discogs import DiscogsClient
    from services.api.musicbrainz import MusicBrainzClient
    from services.api.year_scoring import ArtistContext, ReleaseScorer


class _ReleaseSource(Protocol):
    """A provider client that fetches release records for an album and scores them against one search's artist context."""

    async def fetch_release_records(self, artist_norm: str, album_norm: str) -> list[ReleaseRecord]:
        """Fetch the provider's release records for the album, without the artist context."""
        ...

    def score_records(
        self,
        records: list[ReleaseRecord],
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
    ) -> list[ScoredRelease]:
        """Score release records with the search's artist context."""
        ...


# Discogs answering these has refused its token; asking it again this run would get the same answer. Only Discogs sends a
# credential: from iTunes (which throttles with 403) or MusicBrainz these statuses are ordinary failures
_REJECTED_TOKEN = frozenset({401, 403})

# Two names reach the Apple Music client; its cached records are stored under one
_CACHE_SOURCES = {"applemusic": "itunes"}


@dataclass(slots=True)
class ProviderTally:
    """Whether any provider failed during one album's search; created per search because searches run concurrently."""

    failed: bool = False


class YearSearchCoordinator:
    """Coordinates API calls to fetch release year information.

    Handles:
    - Script-optimized search (Cyrillic, CJK, etc.)
    - Concurrent API queries across multiple providers
    - API priority ordering based on configuration

    Args:
        console_logger: Logger for console output
        error_logger: Logger for error output
        config: Typed application configuration
        preferred_api: Preferred API name for ordering
        musicbrainz_client: MusicBrainz API client
        discogs_client: Discogs API client
        applemusic_client: Apple Music API client
        release_scorer: Release scoring service
        cache_service: Cache holding each provider's release records per album
        max_concurrent_api_calls: Maximum concurrent API requests (default 50).
            Prevents socket exhaustion on large libraries.
        discogs_enabled: Whether Discogs has a token; without one it is not queried, which is not a failure

    """

    def __init__(
        self,
        *,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        config: AppConfig,
        preferred_api: str,
        musicbrainz_client: MusicBrainzClient,
        discogs_client: DiscogsClient,
        applemusic_client: AppleMusicClient,
        release_scorer: ReleaseScorer,
        cache_service: CacheServiceProtocol,
        max_concurrent_api_calls: int = 50,
        discogs_enabled: bool = True,
    ) -> None:
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.config = config
        self.preferred_api = preferred_api
        self.musicbrainz_client = musicbrainz_client
        self.discogs_client = discogs_client
        self.applemusic_client = applemusic_client
        self.release_scorer = release_scorer
        self.cache_service = cache_service
        self.discogs_enabled = discogs_enabled
        # Set when Discogs rejects its token: inactive for the rest of the run, like Discogs without a token
        self._discogs_rejected = False
        self._api_semaphore = asyncio.Semaphore(max_concurrent_api_calls)

    async def fetch_all_api_results(
        self,
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
        log_artist: str,
        log_album: str,
    ) -> list[ScoredRelease]:
        """Fetch scored releases from all API providers with script-aware logic.

        Args:
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_context: Artist region and activity period for scoring
            log_artist: Artist name as shown in logs
            log_album: Album name as shown in logs

        Returns:
            Scored releases, or an empty list when every queried provider answered without a match

        Raises:
            YearLookupUnavailableError: No provider returned releases and at least one failed
        """
        self._log_api_search_start(artist_norm, album_norm, artist_context, log_artist, log_album)
        tally = ProviderTally()

        # Try script-optimized search first
        artist_script = detect_primary_script(log_artist)
        album_script = detect_primary_script(log_album)
        primary_script = artist_script if artist_script != ScriptType.UNKNOWN else album_script

        if primary_script not in (ScriptType.LATIN, ScriptType.UNKNOWN):
            script_results = await self._try_script_optimized_search(primary_script, artist_norm, album_norm, artist_context, tally=tally)
            if script_results:
                return script_results

        # Standard API search (all providers concurrently)
        results = await self._execute_standard_api_search(artist_norm, album_norm, artist_context, log_artist, log_album, tally=tally)
        if results:
            return results

        # Fallback: try alternative search strategy
        results = await self._try_alternative_search(album_norm, artist_context, log_artist, log_album, tally=tally)
        if not results and tally.failed:
            message = f"No year provider could be reached for '{log_artist} - {log_album}'"
            raise YearLookupUnavailableError(message)
        return results

    def _log_api_search_start(
        self,
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
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
            artist_context.region or "None",
        )
        self.console_logger.info("Original names: artist='%s', album='%s'", log_artist, log_album)

    async def _try_script_optimized_search(
        self,
        script_type: ScriptType,
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
        *,
        tally: ProviderTally,
    ) -> list[ScoredRelease] | None:
        """Try script-optimized API search based on detected script type."""
        if debug.api:
            self.console_logger.info("%s detected - trying script-optimized search", script_type.value)

        api_lists = self._get_script_api_priorities(script_type)

        # Try primary APIs first
        results = await self._try_api_list(
            api_lists["primary"],
            artist_norm=artist_norm,
            album_norm=album_norm,
            artist_context=artist_context,
            script_type=script_type,
            is_fallback=False,
            tally=tally,
        )
        if results:
            return results

        # Try fallback APIs if primary failed
        if debug.api:
            self.console_logger.info("Primary APIs failed for %s - trying fallback", script_type.value)
        return await self._try_api_list(
            api_lists["fallback"],
            artist_norm=artist_norm,
            album_norm=album_norm,
            artist_context=artist_context,
            script_type=script_type,
            is_fallback=True,
            tally=tally,
        )

    def _get_script_api_priorities(self, script_type: ScriptType) -> dict[str, list[str]]:
        """Get script-specific API priorities from config."""
        script_priorities = self._get_script_config_priorities(script_type)
        primary_raw = script_priorities.get("primary", ["musicbrainz"])
        fallback_raw = script_priorities.get("fallback", ["discogs"])

        primary = primary_raw if isinstance(primary_raw, list) else ["musicbrainz"]
        fallback = fallback_raw if isinstance(fallback_raw, list) else ["discogs"]

        return {
            "primary": self._apply_preferred_order(primary),
            "fallback": self._apply_preferred_order(fallback),
        }

    def _get_script_config_priorities(self, script_type: ScriptType) -> dict[str, Any]:
        """Get script-specific API priorities from configuration file."""
        script_api_priorities = self.config.year_retrieval.script_api_priorities
        default_priority = script_api_priorities.get("default")
        default_config: dict[str, Any] = {"primary": default_priority.primary, "fallback": default_priority.fallback} if default_priority else {}
        script_priority = script_api_priorities.get(script_type.value)
        if script_priority is None:
            return default_config
        return {"primary": script_priority.primary, "fallback": script_priority.fallback}

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
        *,
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
        script_type: ScriptType,
        is_fallback: bool,
        tally: ProviderTally,
    ) -> list[ScoredRelease] | None:
        """Try a list of API names and return the first successful result."""
        normalized_names = [self._normalize_api_name(name) for name in api_names]
        for api_name in normalized_names:
            results = await self._try_single_api(
                api_name,
                artist_norm=artist_norm,
                album_norm=album_norm,
                artist_context=artist_context,
                script_type=script_type,
                is_fallback=is_fallback,
                tally=tally,
            )
            if results:
                return results
        return None

    async def _try_single_api(
        self,
        api_name: str,
        *,
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
        script_type: ScriptType,
        is_fallback: bool,
        tally: ProviderTally,
    ) -> list[ScoredRelease] | None:
        """Try a single API and return results if successful."""
        try:
            api_client = self._get_api_client(api_name)
            if not api_client:
                if debug.api and not is_fallback:
                    self.console_logger.debug("%s client not available, skipping", api_name)
                return None

            if debug.api:
                self.console_logger.info("Trying %s for %s text", api_name, script_type.value)
            results = await self._call_api_with_proper_params(api_client, api_name, artist_norm, album_norm, artist_context)

            if results:
                if debug.api:
                    result_type = "Fallback" if is_fallback else "Primary"
                    self.console_logger.info(
                        "%s %s found %d results for %s",
                        result_type,
                        api_name,
                        len(results),
                        script_type.value,
                    )
                return results

        except (ApiRequestError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            # Logged whatever the debug flags; the search goes on, and the tally keeps "failed" apart from "found nothing"
            self._record_failure(api_name, e, tally)
            self._log_api_error(api_name, artist_norm, album_norm, e)
            if debug.api:
                self.console_logger.warning("%s failed for %s: %s", api_name, script_type.value, e)

        return None

    async def _call_api_with_proper_params(
        self,
        api_client: _ReleaseSource,
        api_name: str,
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
    ) -> list[ScoredRelease]:
        """Score one provider's release records for the album, fetching them only when the result cache has none.

        The records are cached per provider and album, found ones for good and an empty answer until the negative TTL
        runs out; a failed fetch stores nothing. The fetch holds the semaphore that limits concurrent API requests, and
        the score uses this search's artist context, so a cached answer never carries another search's context.

        Args:
            api_client: The provider client
            api_name: The provider's name, as the coordinator lists it
            artist_norm: Normalized artist name
            album_norm: Normalized album name
            artist_context: Region and activity period of the artist, used in scoring

        Returns:
            The provider's releases with a positive score

        Raises:
            ApiRequestError: The provider could not be asked; nothing is cached for it
        """
        source = _CACHE_SOURCES.get(api_name, api_name)
        is_discogs = api_client is self.discogs_client
        async with self._api_semaphore:
            if is_discogs and self._discogs_rejected:
                # Queued before the token was rejected; sending it now would only be refused again
                return []
            cached = await self.cache_service.get_cached_api_result(artist_norm, album_norm, source)
            if cached is not None and cached.api_response is not None:
                records: list[ReleaseRecord] = cached.api_response["records"]
            else:
                try:
                    records = await api_client.fetch_release_records(artist_norm, album_norm)
                except ApiRequestError as error:
                    # Switch Discogs off here, not when the search ends, so calls queued by other searches are not sent
                    if is_discogs and error.status in _REJECTED_TOKEN and not self._discogs_rejected:
                        self._discogs_rejected = True
                        self.console_logger.warning(
                            "discogs rejected the token (HTTP %d); it is not queried again this run, so check discogs_token", error.status
                        )
                    raise
                await self.cache_service.set_cached_api_result(artist_norm, album_norm, source=source, records=records)
        return api_client.score_records(records, artist_norm, album_norm, artist_context)

    @staticmethod
    def _record_failure(api_name: str, error: BaseException, tally: ProviderTally) -> None:
        """Count a provider failure toward an unavailable lookup; a rejected Discogs token makes Discogs inactive instead."""
        if isinstance(error, ApiRequestError) and api_name == "discogs" and error.status in _REJECTED_TOKEN:
            return
        tally.failed = True

    def _get_api_client(self, api_name: str) -> MusicBrainzClient | DiscogsClient | AppleMusicClient | None:
        """Get an active API client by name: none for Discogs without a token or with a rejected one."""
        api_mapping: dict[str, MusicBrainzClient | DiscogsClient | AppleMusicClient | None] = {
            "musicbrainz": self.musicbrainz_client,
            "discogs": self.discogs_client if self.discogs_enabled and not self._discogs_rejected else None,
            "itunes": self.applemusic_client,
            "applemusic": self.applemusic_client,
        }
        return api_mapping.get(api_name)

    async def _execute_standard_api_search(
        self,
        artist_norm: str,
        album_norm: str,
        artist_context: ArtistContext,
        log_artist: str,
        log_album: str,
        *,
        tally: ProviderTally,
    ) -> list[ScoredRelease]:
        """Execute standard concurrent API search across all providers."""
        api_order = self._apply_preferred_order(["musicbrainz", "discogs", "itunes"])

        # Build tasks and track which api_names survived the filter
        active_api_names: list[str] = []
        api_tasks: list[Coroutine[Any, Any, list[ScoredRelease]]] = []
        for api_name in api_order:
            if api_client := self._get_api_client(api_name):
                active_api_names.append(api_name)
                api_tasks.append(
                    self._call_api_with_proper_params(api_client, api_name, artist_norm, album_norm, artist_context),
                )

        # Execute all API calls concurrently
        results = list(await asyncio.gather(*api_tasks, return_exceptions=True))

        # Process results (active_api_names matches results 1:1)
        return self._process_api_task_results(results, active_api_names, log_artist, log_album, tally=tally)

    async def _try_alternative_search(
        self,
        album_norm: str,
        artist_context: ArtistContext,
        log_artist: str,
        log_album: str,
        *,
        tally: ProviderTally,
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
            artist_context,
            alt_artist or log_artist,
            alt_album or log_album,
            tally=tally,
        )

    def _process_api_task_results(
        self,
        results: list[list[ScoredRelease] | BaseException],
        api_order: list[str],
        log_artist: str,
        log_album: str,
        *,
        tally: ProviderTally,
    ) -> list[ScoredRelease]:
        """Process results from concurrent API tasks."""
        all_releases: list[ScoredRelease] = []

        for api_name, result in zip(api_order, results, strict=True):
            if isinstance(result, BaseException):
                self._record_failure(api_name, result, tally)
                self._log_api_error(api_name, log_artist, log_album, result)
            elif result:
                all_releases.extend(result)
            elif debug.api:
                self._log_empty_api_result(api_name, log_artist, log_album)

        if debug.api:
            self._log_api_summary(log_artist, log_album, len(all_releases))

        return all_releases

    def _log_api_error(self, api_name: str, log_artist: str, log_album: str, error: BaseException) -> None:
        """Log API error with its type, which is all a cancelled search's empty CancelledError carries."""
        self.error_logger.warning(
            "[%s] Error fetching release for '%s - %s': %s: %s",
            api_name,
            log_artist,
            log_album,
            type(error).__name__,
            error,
            exc_info=error,
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
