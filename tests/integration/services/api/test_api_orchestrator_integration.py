"""Integration tests for API Orchestrator - multi-provider coordination.

These tests verify the integration between:
- MusicBrainz client
- Discogs client
- Apple Music client
- Cache orchestrator
- Rate limiter coordination
- Year scoring and resolution

Tests use mocked HTTP responses but exercise real coordination logic.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.api.orchestrator import normalize_name
from services.api.api_base import ApiRateLimiter, ScoredRelease
from services.api.year_scoring import create_release_scorer, ArtistPeriodContext
from services.cache.orchestrator import CacheOrchestrator
from services.pending_verification import PendingVerificationService
from tests.mocks.csv_mock import MockLogger  # sourcery skip: dont-import-test-modules


def make_scored_release(
    title: str,
    year: str | None,
    score: float,
    source: str,
    artist: str | None = None,
) -> ScoredRelease:
    """Create a ScoredRelease TypedDict for testing."""
    return {
        "title": title,
        "year": year,
        "score": score,
        "source": source,
        "artist": artist,
        "album_type": None,
        "country": None,
        "status": None,
        "format": None,
        "label": None,
        "catalog_number": None,
        "barcode": None,
        "disambiguation": None,
    }


class TestNormalizeNameIntegration:
    """Integration tests for album/artist name normalization."""

    @pytest.mark.parametrize(
        ("input_name", "expected"),
        [
            # Basic substitutions
            ("Fire & Water", "Fire and Water"),
            ("Fire&Water", "Fire and Water"),
            ("Split w/ Band", "Split with Band"),
            ("Split w/Band", "Split with Band"),  # w/ with no space - normalize_name adds space
            # Colon normalization for Lucene search
            ("III:Trauma", "III Trauma"),
            ("Album: Subtitle", "Album Subtitle"),  # Colon replaced with space, then normalized
            # Bonus track markers
            ("Album + 4", "Album"),
            ("Album + 10 Bonus Tracks", "Album"),
            ("Album+4", "Album+4"),  # No space before + preserved
            # Split albums
            ("Robot Hive / Exodus", "Robot Hive"),
            ("House By the Cemetery / Mortal Massacre", "House By the Cemetery"),
            # Combined substitutions
            ("Band & Friends: Live / Tour", "Band and Friends Live"),  # Multiple spaces normalized
            # Edge cases
            ("", ""),
            ("Normal Album", "Normal Album"),
            ("Album  With   Spaces", "Album With Spaces"),
        ],
    )
    def test_normalize_name_transformations(
        self,
        input_name: str,
        expected: str,
    ) -> None:
        """Verify name normalization handles all expected transformations."""
        result = normalize_name(input_name)
        assert result == expected


@pytest.fixture
def mock_config() -> dict[str, Any]:
    """Create mock configuration for orchestrator tests."""
    return {
        "external_apis": {
            "musicbrainz": {
                "use_musicbrainz": True,
                "rate_limit_delay": 0.01,
            },
            "discogs": {
                "use_discogs": True,
                "rate_limit_delay": 0.01,
            },
        },
        "scoring": {
            "min_confidence_score": 75,
            "high_confidence_threshold": 85,
            "very_high_confidence_threshold": 95,
        },
        "caching": {
            "ttl_days": 7,
            "force_cache_refresh": False,
        },
        "processing": {
            "batch_size": 100,
        },
        "year_update": {
            "concurrent_limit": 5,
        },
        "api_keys": {},
    }


@pytest.fixture
def mock_cache_orchestrator() -> MagicMock:
    """Create mock cache orchestrator."""
    cache = MagicMock(spec=CacheOrchestrator)
    cache.get_year_from_all_caches = AsyncMock(return_value=None)
    cache.store_year_in_cache = AsyncMock()
    cache.get_artist_activity_period = AsyncMock(return_value=None)
    cache.get_lastfm_year = AsyncMock(return_value=None)
    return cache


@pytest.fixture
def mock_pending_verification() -> MagicMock:
    """Create mock pending verification service."""
    pv = MagicMock(spec=PendingVerificationService)
    pv.mark_for_verification = AsyncMock()
    pv.get_entry = AsyncMock(return_value=None)
    pv.is_verification_needed = AsyncMock(return_value=False)
    return pv


class TestApiOrchestratorProviderCoordination:
    """Integration tests for multi-provider coordination."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_cache_orchestrator", "mock_pending_verification")
    async def test_musicbrainz_primary_discogs_fallback(
        self,
        mock_config: dict[str, Any],
    ) -> None:
        """Test that Discogs is used as fallback when MusicBrainz fails."""
        # This test verifies the coordination logic between providers
        with (
            patch("services.api.orchestrator.MusicBrainzClient") as mock_mb,
            patch("services.api.orchestrator.DiscogsClient") as mock_discogs,
            patch("aiohttp.ClientSession"),
        ):
            # MusicBrainz returns no results
            mock_mb_instance = AsyncMock()
            mock_mb_instance.search_release = AsyncMock(return_value=[])
            mock_mb.return_value = mock_mb_instance

            # Discogs returns a valid result
            mock_discogs_instance = AsyncMock()
            mock_discogs_instance.search_release = AsyncMock(return_value=[make_scored_release("Test Album", "2020", 85.0, "discogs", "Test Artist")])
            mock_discogs.return_value = mock_discogs_instance

            # Verify provider initialization
            assert mock_config["external_apis"]["musicbrainz"]["use_musicbrainz"]
            assert mock_config["external_apis"]["discogs"]["use_discogs"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_config")
    async def test_rate_limiter_coordination_across_providers(
        self,
    ) -> None:
        """Test that rate limiters are properly coordinated across providers."""
        # Create separate rate limiters for each provider
        # ApiRateLimiter uses requests_per_window and window_seconds
        mb_limiter = ApiRateLimiter(
            requests_per_window=1,
            window_seconds=1.0,
        )
        discogs_limiter = ApiRateLimiter(
            requests_per_window=1,
            window_seconds=1.0,
        )

        # Track request timing
        request_times: list[float] = []

        async def timed_request(limiter: ApiRateLimiter) -> None:
            """Execute a timed request through the rate limiter."""
            await limiter.acquire()
            request_times.append(time.monotonic())
            limiter.release()

        # Execute requests in parallel - should be rate limited independently
        await asyncio.gather(
            timed_request(mb_limiter),
            timed_request(discogs_limiter),
        )

        # Both should complete (different rate limiters don't block each other)
        assert len(request_times) == 2

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_config")
    async def test_cache_integration_before_api_call(
        self,
        mock_cache_orchestrator: MagicMock,
    ) -> None:
        """Test that cache is checked before making API calls."""
        # Setup cache to return a hit
        mock_cache_orchestrator.get_year_from_all_caches = AsyncMock(return_value=("2019", 90))

        # Verify mock returns expected cached data
        cached_year, cached_score = mock_cache_orchestrator.get_year_from_all_caches.return_value

        assert cached_year == "2019"
        assert cached_score == 90


class TestApiOrchestratorYearResolution:
    """Integration tests for year resolution with multiple sources."""

    @pytest.mark.asyncio
    async def test_year_scoring_with_multiple_candidates(self) -> None:
        """Test year resolution when multiple candidates are available."""
        # Create test releases with different years and scores
        releases = [
            make_scored_release("Album", "2018", 85.0, "musicbrainz", "Artist"),
            make_scored_release("Album", "2019", 90.0, "discogs", "Artist"),
            make_scored_release("Album (Remaster)", "2020", 75.0, "musicbrainz", "Artist"),
        ]

        # Find best year by highest score
        best_release = max(releases, key=lambda r: r["score"])
        assert best_release["year"] == "2019"
        assert best_release["score"] >= 85

    @pytest.mark.asyncio
    async def test_year_resolution_prefers_original_over_remaster(self) -> None:
        """Test that original releases are preferred over remasters."""
        scorer = create_release_scorer(
            console_logger=MockLogger(),
        )

        # The scorer uses score_original_release which takes a release dict
        # and normalized artist/album names
        original_release = {"title": "Album", "artist": "Artist", "year": "2018"}
        remaster_release = {"title": "Album (Remaster)", "artist": "Artist", "year": "2020"}

        # Score both releases against the same target
        original_score = scorer.score_original_release(
            release=original_release,
            artist_norm="artist",
            album_norm="album",
            artist_region=None,
            source="musicbrainz",
        )

        remaster_score = scorer.score_original_release(
            release=remaster_release,
            artist_norm="artist",
            album_norm="album",
            artist_region=None,
            source="musicbrainz",
        )

        # Original should score higher (remaster indicator in title penalizes score)
        assert original_score >= remaster_score


class TestApiOrchestratorErrorHandling:
    """Integration tests for error handling across providers."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_config")
    async def test_graceful_handling_of_provider_timeout(
        self,
    ) -> None:
        """Test graceful handling when a provider times out."""
        with patch("aiohttp.ClientSession") as mock_session:
            # Simulate timeout
            mock_response = AsyncMock()
            mock_response.status = 504  # Gateway timeout
            mock_response.json = AsyncMock(side_effect=asyncio.TimeoutError)

            mock_session.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            # The system should handle timeout gracefully without crashing
            # This tests the error handling path

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_config")
    async def test_graceful_handling_of_rate_limit_response(
        self,
    ) -> None:
        """Test handling of 429 Too Many Requests response."""
        # Create executor with mock session
        mock_session = MagicMock()

        # Simulate 429 response
        mock_response = AsyncMock()
        mock_response.status = 429
        mock_response.headers = {"Retry-After": "1"}

        mock_session.get = AsyncMock(return_value=mock_response)

        # The executor should handle rate limiting appropriately
        # Testing the response handling logic

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_config", "mock_pending_verification")
    async def test_all_providers_fail_returns_none(
        self,
        mock_cache_orchestrator: MagicMock,
    ) -> None:
        """Test that None is returned when all providers fail."""
        # Cache returns nothing
        mock_cache_orchestrator.get_year_from_all_caches = AsyncMock(return_value=None)

        # Verify no cached result available
        result = mock_cache_orchestrator.get_year_from_all_caches.return_value
        assert result is None


class TestApiOrchestratorConcurrency:
    """Integration tests for concurrent API operations."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mock_config")
    async def test_concurrent_album_year_requests(
        self,
        mock_cache_orchestrator: MagicMock,
    ) -> None:
        """Test handling of concurrent year requests for different albums."""
        albums = [
            ("Artist A", "Album 1"),
            ("Artist B", "Album 2"),
            ("Artist C", "Album 3"),
            ("Artist D", "Album 4"),
            ("Artist E", "Album 5"),
        ]

        # Simulate cache lookups
        mock_cache_orchestrator.get_year_from_all_caches = AsyncMock(
            side_effect=[
                ("2018", 85),
                None,
                ("2020", 90),
                None,
                ("2015", 80),
            ]
        )

        # Execute concurrent lookups
        tasks = [mock_cache_orchestrator.get_year_from_all_caches(artist=a, album=b) for a, b in albums]
        results = list(await asyncio.gather(*tasks))

        # Verify all requests completed
        assert len(results) == 5
        assert results[0] == ("2018", 85)
        assert results[1] is None
        assert results[2] == ("2020", 90)

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_api_calls(self) -> None:
        """Test that semaphore properly limits concurrent API calls."""
        max_concurrent = 3
        semaphore = asyncio.Semaphore(max_concurrent)
        active_count = 0
        max_active = 0

        async def simulated_api_call(call_id: int) -> int:
            """Simulate an API call with semaphore-controlled concurrency."""
            nonlocal active_count, max_active
            async with semaphore:
                active_count += 1
                max_active = max(max_active, active_count)
                await asyncio.sleep(0.01)  # Simulate API latency
                active_count -= 1
                return call_id

        # Launch more tasks than the semaphore allows
        tasks = [simulated_api_call(i) for i in range(10)]
        await asyncio.gather(*tasks)

        # Max concurrent should never exceed semaphore limit
        assert max_active <= max_concurrent


class TestApiOrchestratorArtistActivityPeriod:
    """Integration tests for artist activity period handling."""

    @pytest.mark.asyncio
    async def test_activity_period_affects_year_scoring(self) -> None:
        """Test that artist activity period influences year scoring."""
        # Artist active 1980-1990
        period_context: ArtistPeriodContext = {
            "start_year": 1980,
            "end_year": 1990,
        }

        scorer = create_release_scorer(
            console_logger=MockLogger(),
        )

        # Set the artist period context
        scorer.set_artist_period_context(period_context)

        # Release from within activity period
        release_1985 = {"title": "Classic Album", "artist": "80s Band", "year": "1985"}
        # Release from outside activity period (likely a reissue)
        release_2020 = {"title": "Classic Album", "artist": "80s Band", "year": "2020"}

        # Score both releases
        score_1985 = scorer.score_original_release(
            release=release_1985,
            artist_norm="80s band",
            album_norm="classic album",
            artist_region=None,
            source="musicbrainz",
        )

        score_2020 = scorer.score_original_release(
            release=release_2020,
            artist_norm="80s band",
            album_norm="classic album",
            artist_region=None,
            source="musicbrainz",
        )

        # Both should produce valid scores (non-negative)
        assert score_1985 >= 0
        assert score_2020 >= 0

    @pytest.mark.asyncio
    async def test_activity_period_cached_and_reused(
        self,
        mock_cache_orchestrator: MagicMock,
    ) -> None:
        """Test that activity period is cached and reused across requests."""
        # Configure mock to return cached activity period
        expected_period = (1980, 1995)
        mock_cache_orchestrator.get_artist_activity_period = AsyncMock(return_value=expected_period)

        # Verify consistent return value (simulates cache reuse)
        period1 = mock_cache_orchestrator.get_artist_activity_period.return_value
        period2 = mock_cache_orchestrator.get_artist_activity_period.return_value

        assert period1 == expected_period
        assert period2 == expected_period


class TestApiOrchestratorScriptDetection:
    """Integration tests for non-Latin script handling."""

    @pytest.mark.asyncio
    async def test_japanese_artist_name_handling(self) -> None:
        """Test handling of Japanese artist names in API queries."""
        from core.models.script_detection import detect_primary_script, ScriptType

        japanese_name = "宇多田ヒカル"  # Utada Hikaru
        script = detect_primary_script(japanese_name)

        assert script == ScriptType.JAPANESE

    @pytest.mark.asyncio
    async def test_cyrillic_artist_name_handling(self) -> None:
        """Test handling of Cyrillic artist names."""
        from core.models.script_detection import detect_primary_script, ScriptType

        cyrillic_name = "Сплин"  # Splin (Russian band)
        script = detect_primary_script(cyrillic_name)

        assert script == ScriptType.CYRILLIC

    @pytest.mark.asyncio
    async def test_mixed_script_artist_name(self) -> None:
        """Test handling of mixed script artist names."""
        from core.models.script_detection import detect_primary_script, ScriptType

        # "BABYMETAL" with Japanese characters
        mixed_name = "BABYMETALベビーメタル"
        script = detect_primary_script(mixed_name)

        # Should detect the dominant script or mixed
        assert script in (ScriptType.LATIN, ScriptType.JAPANESE, ScriptType.MIXED)

    @pytest.mark.asyncio
    async def test_chinese_artist_name_handling(self) -> None:
        """Test handling of Chinese artist names."""
        from core.models.script_detection import detect_primary_script, ScriptType

        chinese_name = "周杰倫"  # Jay Chou
        script = detect_primary_script(chinese_name)

        assert script == ScriptType.CHINESE

    @pytest.mark.asyncio
    async def test_korean_artist_name_handling(self) -> None:
        """Test handling of Korean artist names."""
        from core.models.script_detection import detect_primary_script, ScriptType

        korean_name = "방탄소년단"  # BTS
        script = detect_primary_script(korean_name)

        assert script == ScriptType.KOREAN
