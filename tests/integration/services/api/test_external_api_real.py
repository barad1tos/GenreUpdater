"""Integration tests for ExternalApiOrchestrator with real APIs.

These tests require:
- Internet connection
- Valid API credentials in config
- Rate limiting awareness (tests are slow by design)

Run with: pytest tests/integration/services/api/ -v --tb=short -m integration
"""

import asyncio
import logging
import socket
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from app.app_config import Config
from metrics.analytics import Analytics, LoggerContainer
from services.api.orchestrator import ExternalApiOrchestrator
from services.cache.orchestrator import CacheOrchestrator
from services.pending_verification import PendingVerificationService


def is_internet_available() -> bool:
    """Check if internet is available via socket connection."""
    try:
        socket.create_connection(("musicbrainz.org", 443), timeout=5).close()
        return True
    except (OSError, TimeoutError):
        return False


# Skip all tests in this module if no internet
pytestmark = [
    pytest.mark.skipif(not is_internet_available(), reason="Requires internet connection"),
    pytest.mark.integration,
    pytest.mark.slow,  # API tests are inherently slow
]


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create a console logger for tests."""
    logger = logging.getLogger("test.integration.api")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
    return logger


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create an error logger for tests."""
    logger = logging.getLogger("test.integration.api.error")
    logger.setLevel(logging.ERROR)
    return logger


@pytest.fixture
def app_config() -> dict[str, Any]:
    """Load real application config."""
    config = Config()
    return config.load()


@pytest.fixture
def analytics(
    app_config: dict[str, Any],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> Analytics:
    """Create analytics instance for tests."""
    analytics_logger = logging.getLogger("test.integration.analytics")
    loggers = LoggerContainer(
        console_logger=console_logger,
        error_logger=error_logger,
        analytics_logger=analytics_logger,
    )
    return Analytics(config=app_config, loggers=loggers)


@pytest.fixture
def cache_service(
    app_config: dict[str, Any],
    console_logger: logging.Logger,
) -> CacheOrchestrator:
    """Create a cache orchestrator for tests."""
    return CacheOrchestrator(config=app_config, logger=console_logger)


@pytest.fixture
def pending_verification_service(
    app_config: dict[str, Any],
    console_logger: logging.Logger,
    error_logger: logging.Logger,
) -> PendingVerificationService:
    """Create a pending verification service for tests."""
    return PendingVerificationService(
        config=app_config,
        console_logger=console_logger,
        error_logger=error_logger,
    )


@pytest.fixture
async def api_orchestrator(
    app_config: dict[str, Any],
    analytics: Analytics,
    console_logger: logging.Logger,
    error_logger: logging.Logger,
    cache_service: CacheOrchestrator,
    pending_verification_service: PendingVerificationService,
) -> AsyncGenerator[ExternalApiOrchestrator]:
    """Create and initialize a real ExternalApiOrchestrator."""
    orchestrator = ExternalApiOrchestrator(
        config=app_config,
        analytics=analytics,
        console_logger=console_logger,
        error_logger=error_logger,
        cache_service=cache_service,
        pending_verification_service=pending_verification_service,
    )
    await orchestrator.initialize()
    yield orchestrator
    await orchestrator.close()


class TestApiOrchestratorInitialization:
    """Tests for API orchestrator initialization."""

    @pytest.mark.asyncio
    async def test_orchestrator_initializes(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test orchestrator can be initialized with real config."""
        assert api_orchestrator is not None
        assert api_orchestrator._initialized is True
        assert api_orchestrator.session is not None

    @pytest.mark.asyncio
    async def test_rate_limiters_initialized(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test rate limiters are properly initialized."""
        assert api_orchestrator.rate_limiters is not None
        # Should have rate limiters for each API
        assert len(api_orchestrator.rate_limiters) > 0

    @pytest.mark.asyncio
    async def test_api_clients_initialized(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test API clients are initialized."""
        # At least one of these should be initialized (MusicBrainz doesn't need key)
        clients = [
            api_orchestrator.musicbrainz_client,
            api_orchestrator.discogs_client,
        ]
        assert any(c is not None for c in clients), "At least one API client should be initialized"


class TestMusicBrainzIntegration:
    """Tests for MusicBrainz API integration."""

    @pytest.mark.asyncio
    async def test_get_album_year_known_album(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test getting year for a well-known album (Abbey Road)."""
        # Abbey Road by The Beatles was released in 1969
        # get_album_year returns (year_str, is_reliable) tuple
        result = await api_orchestrator.get_album_year(
            artist="The Beatles",
            album="Abbey Road",
        )

        # Should find the year - 1969 is well-documented
        assert result is not None
        year, is_reliable, _confidence, _year_scores = result
        assert year == "1969"
        assert is_reliable is True

    @pytest.mark.asyncio
    async def test_get_album_year_returns_none_for_nonexistent(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test returns (None, False) for non-existent album."""
        result = await api_orchestrator.get_album_year(
            artist="Completely Fake Artist Name XYZ123",
            album="This Album Does Not Exist ABC456",
        )

        # Should return (None, False, 0, {}) for non-existent album
        assert result is not None
        year, is_reliable, _confidence, _year_scores = result
        assert year is None
        assert is_reliable is False

    @pytest.mark.asyncio
    async def test_get_album_year_classic_rock(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test getting year for classic rock album."""
        # Dark Side of the Moon by Pink Floyd was released in 1973
        result = await api_orchestrator.get_album_year(
            artist="Pink Floyd",
            album="The Dark Side of the Moon",
        )

        assert result is not None
        year, _, _confidence, _year_scores = result
        assert year == "1973"

    @pytest.mark.asyncio
    async def test_get_album_year_modern_album(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test getting year for a more recent album."""
        # 21 by Adele was released in 2011
        result = await api_orchestrator.get_album_year(
            artist="Adele",
            album="21",
        )

        assert result is not None
        year, _, _confidence, _year_scores = result
        assert year == "2011"


class TestArtistActivityPeriod:
    """Tests for artist activity period retrieval."""

    @pytest.mark.asyncio
    async def test_get_activity_period_classic_band(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test getting activity period for a classic band."""
        # The Beatles were active 1960-1970
        period = await api_orchestrator.get_artist_activity_period("The Beatles")

        assert period is not None
        start_year, end_year = period

        # Beatles started in early 60s
        assert start_year is not None
        assert 1958 <= start_year <= 1962

        # Beatles ended around 1970
        assert end_year is not None
        assert 1968 <= end_year <= 1972

    @pytest.mark.asyncio
    async def test_get_activity_period_active_artist(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test getting activity period for still-active artist."""
        # Taylor Swift - the APIs may return different dates
        period = await api_orchestrator.get_artist_activity_period("Taylor Swift")

        if period is not None:
            start_year, _end_year = period

            # APIs return various start years for Taylor Swift (1989 = birth year, or 2004-2006 = career start)
            # Just verify we got some reasonable values
            if start_year is not None:
                assert 1985 <= start_year <= 2010  # Reasonable range

            # _end_year should either be None (still active) or recent year
            # APIs may report differently for active artists

    @pytest.mark.asyncio
    async def test_get_activity_period_nonexistent_artist(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test returns (None, None) for non-existent artist."""
        period = await api_orchestrator.get_artist_activity_period("Completely Fake Artist That Does Not Exist 12345")

        # Method returns (None, None) when artist not found
        assert period is not None
        start_year, end_year = period
        assert start_year is None
        assert end_year is None


class TestConcurrentApiCalls:
    """Tests for concurrent API operations."""

    @pytest.mark.asyncio
    async def test_concurrent_year_lookups(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test running multiple year lookups concurrently."""
        # These are all well-known albums
        albums = [
            ("The Beatles", "Abbey Road"),
            ("Pink Floyd", "The Wall"),
            ("Queen", "A Night at the Opera"),
        ]

        async def get_year(artist: str, album: str) -> tuple[str, str, str | None]:
            """Fetch album year from API."""
            album_year, _, _confidence, _year_scores = await api_orchestrator.get_album_year(artist=artist, album=album)
            return artist, album, album_year

        # Run lookups concurrently
        tasks = [get_year(artist, album) for artist, album in albums]
        results = await asyncio.gather(*tasks)

        # All should return valid years
        for artist, album, year in results:
            assert year is not None, f"No year found for {artist} - {album}"
            year_int = int(year)
            assert 1960 <= year_int <= 2025, f"Year {year} out of range for {artist} - {album}"


class TestApiErrorHandling:
    """Tests for API error handling."""

    @pytest.mark.asyncio
    async def test_handles_special_characters_in_artist(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test handling of special characters in artist name."""
        # AC/DC has special characters
        result = await api_orchestrator.get_album_year(
            artist="AC/DC",
            album="Back in Black",
        )

        # Back in Black was released in 1980
        assert result is not None
        year, _, _confidence, _year_scores = result
        assert year == "1980"

    @pytest.mark.asyncio
    async def test_handles_unicode_artist(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test handling of Unicode characters in artist name."""
        # Björk has Unicode character
        result = await api_orchestrator.get_album_year(
            artist="Björk",
            album="Post",
        )

        # Post was released in 1995
        if result is not None:  # May not find if API doesn't handle Unicode well
            year, _, _confidence, _year_scores = result
            assert year == "1995"

    @pytest.mark.asyncio
    async def test_handles_very_long_album_name(
        self,
        api_orchestrator: ExternalApiOrchestrator,
    ) -> None:
        """Test handling of very long album names."""
        # This very long album name shouldn't crash the API
        result = await api_orchestrator.get_album_year(
            artist="Test Artist",
            album="This Is A Very Long Album Name That Should Not Cause Any Problems " * 5,
        )

        # Should handle gracefully, probably returning None
        # The important thing is it doesn't crash
        assert result is None or isinstance(result, tuple)


class TestApiCleanup:
    """Tests for proper resource cleanup."""

    @pytest.mark.asyncio
    async def test_close_releases_resources(
        self,
        app_config: dict[str, Any],
        analytics: Analytics,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        cache_service: CacheOrchestrator,
        pending_verification_service: PendingVerificationService,
    ) -> None:
        """Test that close() properly releases resources."""
        orchestrator = ExternalApiOrchestrator(
            config=app_config,
            analytics=analytics,
            console_logger=console_logger,
            error_logger=error_logger,
            cache_service=cache_service,
            pending_verification_service=pending_verification_service,
        )
        await orchestrator.initialize()

        # Verify session is open
        assert orchestrator.session is not None
        assert not orchestrator.session.closed

        # Close the orchestrator
        await orchestrator.close()

        # Session should be closed
        assert orchestrator.session.closed

    @pytest.mark.asyncio
    async def test_double_close_safe(
        self,
        app_config: dict[str, Any],
        analytics: Analytics,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        cache_service: CacheOrchestrator,
        pending_verification_service: PendingVerificationService,
    ) -> None:
        """Test that calling close() twice doesn't cause errors."""
        orchestrator = ExternalApiOrchestrator(
            config=app_config,
            analytics=analytics,
            console_logger=console_logger,
            error_logger=error_logger,
            cache_service=cache_service,
            pending_verification_service=pending_verification_service,
        )
        await orchestrator.initialize()

        # Close twice - should not raise
        await orchestrator.close()
        await orchestrator.close()
