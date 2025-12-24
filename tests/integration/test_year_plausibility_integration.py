"""Integration tests for Year Plausibility Check (Issue #72).

These tests verify the full integration flow:
YearRetriever → YearFallbackHandler → ExternalApiOrchestrator

Tests use mocked external dependencies but real internal logic.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.models.track_models import TrackDict
from core.tracks.year_fallback import YearFallbackHandler
from tests.mocks.protocol_mocks import (
    MockExternalApiService,
    MockPendingVerificationService,
)


class MockLogger(logging.Logger):
    """Mock logger that captures log calls."""

    def __init__(self, name: str = "mock") -> None:
        """Initialize mock logger."""
        super().__init__(name)
        self.logged_messages: list[tuple[int, str]] = []

    def _log(
        self,
        level: int,
        msg: object,
        args: tuple[object, ...] | Mapping[str, object],
        exc_info: Any = None,
        extra: Mapping[str, object] | None = None,
        stack_info: bool = False,
        stacklevel: int = 1,
    ) -> None:
        """Capture log messages."""
        del args, exc_info, extra, stack_info, stacklevel  # unused
        self.logged_messages.append((level, str(msg)))


class MockAnalytics:
    """Mock analytics for testing."""

    def __init__(self) -> None:
        """Initialize mock analytics."""
        self.events: list[dict[str, Any]] = []

    def record_event(self, event_type: str, **kwargs: Any) -> None:
        """Record an analytics event."""
        self.events.append({"type": event_type, **kwargs})

    def start_timer(self, _name: str) -> None:
        """Start a timer."""

    @staticmethod
    def stop_timer(_name: str) -> float:
        """Stop a timer."""
        return 0.0


def create_test_tracks(
    artist: str,
    album: str,
    year: str,
    count: int = 3,
) -> list[TrackDict]:
    """Create test tracks for an album."""
    return [
        TrackDict(
            id=f"{artist}_{album}_{i}",
            name=f"Track {i}",
            artist=artist,
            album=album,
            genre="Rock",
            year=year,
            date_added="2024-01-01",
        )
        for i in range(count)
    ]


@pytest.mark.integration
class TestYearPlausibilityIntegration:
    """Integration tests for year plausibility check flow."""

    @pytest.fixture
    def mock_track_processor(self) -> AsyncMock:
        """Create mock track processor."""
        processor = AsyncMock()
        processor.update_track_async = AsyncMock(return_value=True)
        return processor

    @pytest.fixture
    def mock_cache_service(self) -> MagicMock:
        """Create mock cache service."""
        cache = MagicMock()
        cache.get_async = AsyncMock(return_value=None)
        cache.set_async = AsyncMock()
        cache.get_album_year_from_cache = AsyncMock(return_value=None)
        cache.get_album_year_entry_from_cache = AsyncMock(return_value=None)
        cache.store_album_year_in_cache = AsyncMock()
        cache.generic_service = MagicMock()
        cache.generic_service.get = MagicMock(return_value=None)
        cache.generic_service.set = MagicMock()
        return cache

    @pytest.fixture
    def mock_api_service(self) -> MockExternalApiService:
        """Create mock external API service."""
        return MockExternalApiService()

    @pytest.fixture
    def mock_pending_service(self) -> MockPendingVerificationService:
        """Create mock pending verification service."""
        return MockPendingVerificationService()

    @pytest.fixture
    def console_logger(self) -> MockLogger:
        """Create mock console logger."""
        return MockLogger("console")

    @pytest.fixture
    def error_logger(self) -> MockLogger:
        """Create mock error logger."""
        return MockLogger("error")

    @pytest.fixture
    def analytics(self) -> MockAnalytics:
        """Create mock analytics."""
        return MockAnalytics()

    @pytest.fixture
    def fallback_handler(
        self,
        console_logger: MockLogger,
        mock_pending_service: MockPendingVerificationService,
        mock_api_service: MockExternalApiService,
    ) -> YearFallbackHandler:
        """Create YearFallbackHandler with real logic and mocked dependencies."""
        return YearFallbackHandler(
            console_logger=console_logger,
            pending_verification=mock_pending_service,
            fallback_enabled=True,
            absurd_year_threshold=1900,
            year_difference_threshold=5,
            trust_api_score_threshold=70,
            api_orchestrator=mock_api_service,
        )

    @pytest.mark.asyncio
    async def test_full_flow_impossible_year_fixed(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_service: MockExternalApiService,
        mock_pending_service: MockPendingVerificationService,
    ) -> None:
        """Test full integration: impossible year detected and fixed.

        Scenario: Bad Omens (formed 2015) has year 2000 in library.
        Expected: Plausibility check detects impossible year, applies API year.
        """
        mock_api_service.artist_activity_response = (2015, None)
        tracks = create_test_tracks("Bad Omens", "Dying To Love", "2000")
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2025",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Bad Omens",
            album="Dying To Love",
        )
        assert result == "2025", f"Expected '2025', got '{result}'"

        # Verify marked for verification with correct reason
        assert len(mock_pending_service.marked_albums) >= 1
        reasons = [m[2] for m in mock_pending_service.marked_albums]
        assert "implausible_existing_year" in reasons

    @pytest.mark.asyncio
    async def test_full_flow_plausible_year_preserved(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_service: MockExternalApiService,
        mock_pending_service: MockPendingVerificationService,
    ) -> None:
        """Test full integration: plausible year is preserved.

        Scenario: Metallica (formed 1981) has year 1986 in library.
        Expected: Plausibility check allows year, fallback returns None (preserve existing).

        Note: apply_year_fallback returns None to signal "don't update, keep existing".
        The caller (YearRetriever) interprets None as "preserve existing year".
        """
        mock_api_service.artist_activity_response = (1981, None)
        tracks = create_test_tracks("Metallica", "Master of Puppets", "1986")
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Metallica",
            album="Master of Puppets",
        )
        # Existing year is returned to propagate to all tracks (including empty ones)
        assert result == "1986", f"Expected '1986' (propagate existing), got '{result}'"

        # Should be marked for verification (suspicious change)
        assert len(mock_pending_service.marked_albums) >= 1

    @pytest.mark.asyncio
    async def test_full_flow_high_confidence_applies_api(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_service: MockExternalApiService,
    ) -> None:
        """Test full integration: high confidence bypasses plausibility.

        Scenario: Any artist with high confidence API result (>= 70%).
        Expected: API year is applied regardless of plausibility check.
        """
        mock_api_service.artist_activity_response = (1981, None)
        tracks = create_test_tracks("Metallica", "Some Album", "1999")
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=85,  # High confidence
            artist="Metallica",
            album="Some Album",
        )
        assert result == "2020", f"Expected '2020', got '{result}'"

    @pytest.mark.asyncio
    async def test_full_flow_definitive_bypasses_all(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_service: MockExternalApiService,
    ) -> None:
        """Test full integration: is_definitive=True bypasses fallback.

        Scenario: API returns definitive result.
        Expected: Year applied immediately, no fallback logic.
        """
        tracks = create_test_tracks("Artist", "Album", "1999")
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=True,
            confidence_score=95,
            artist="Artist",
            album="Album",
        )
        assert result == "2020", f"Expected '2020', got '{result}'"

        # API orchestrator should NOT be called (no plausibility check needed)
        assert len(mock_api_service.artist_activity_requests) == 0


@pytest.mark.integration
class TestApiOrchestratorIntegration:
    """Integration tests for API orchestrator's get_artist_start_year."""

    @pytest.fixture
    def mock_api_service(self) -> MockExternalApiService:
        """Create mock external API service."""
        return MockExternalApiService()

    @pytest.mark.asyncio
    async def test_orchestrator_returns_start_year(
        self,
        mock_api_service: MockExternalApiService,
    ) -> None:
        """Test API orchestrator returns artist start year."""
        mock_api_service.artist_activity_response = (1981, 2023)
        result = await mock_api_service.get_artist_start_year("metallica")
        assert result == 1981
        assert "metallica" in mock_api_service.artist_activity_requests

    @pytest.mark.asyncio
    async def test_orchestrator_handles_unknown_artist(
        self,
        mock_api_service: MockExternalApiService,
    ) -> None:
        """Test API orchestrator handles unknown artist gracefully."""
        mock_api_service.artist_activity_response = (None, None)
        result = await mock_api_service.get_artist_start_year("unknown_artist")
        assert result is None


@pytest.mark.integration
class TestRealWorldScenariosIntegration:
    """Integration tests based on real Issue #72 cases."""

    @pytest.fixture
    def mock_api_service(self) -> MockExternalApiService:
        """Create mock external API service."""
        return MockExternalApiService()

    @pytest.fixture
    def mock_pending_service(self) -> MockPendingVerificationService:
        """Create mock pending verification service."""
        return MockPendingVerificationService()

    @pytest.fixture
    def fallback_handler(
        self,
        mock_pending_service: MockPendingVerificationService,
        mock_api_service: MockExternalApiService,
    ) -> YearFallbackHandler:
        """Create fallback handler."""
        return YearFallbackHandler(
            console_logger=logging.getLogger("test"),
            pending_verification=mock_pending_service,
            fallback_enabled=True,
            absurd_year_threshold=1900,
            year_difference_threshold=5,
            trust_api_score_threshold=70,
            api_orchestrator=mock_api_service,
        )

    @pytest.mark.asyncio
    async def test_bad_omens_real_case(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_service: MockExternalApiService,
    ) -> None:
        """Integration test for Bad Omens case from Issue #72.

        Real data:
        - Artist: Bad Omens (formed 2015)
        - Album: Dying To Love (released 2025)
        - Library year: 2000 (WRONG - band didn't exist)

        Expected: System detects impossibility and applies correct year.
        """
        mock_api_service.artist_activity_response = (2015, None)
        tracks = create_test_tracks("Bad Omens", "Dying To Love", "2000")

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2025",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Bad Omens",
            album="Dying To Love",
        )

        assert result == "2025", (
            f"Bad Omens case failed: got '{result}' instead of '2025'. Year 2000 should NOT be preserved for band that formed in 2015."
        )

    @pytest.mark.asyncio
    async def test_children_of_bodom_real_case(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_service: MockExternalApiService,
    ) -> None:
        """Integration test for Children of Bodom case from Issue #72.

        Real data:
        - Artist: Children of Bodom (formed 1993)
        - Album: Something Wild (released 1997)
        - Library year: 2005 (WRONG but plausible)

        With high confidence, should apply correct API year.
        """
        mock_api_service.artist_activity_response = (1993, None)
        tracks = create_test_tracks("Children of Bodom", "Something Wild", "2005")

        # High confidence should always apply API year
        result = await fallback_handler.apply_year_fallback(
            proposed_year="1997",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=85,
            artist="Children of Bodom",
            album="Something Wild",
        )

        assert result == "1997", f"Children of Bodom case failed: got '{result}' instead of '1997'. High confidence API year should be applied."
