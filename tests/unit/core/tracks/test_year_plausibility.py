"""Tests for Year Plausibility Check functionality (Issue #72).

This module tests the year plausibility logic that prevents WRONG library years
from being preserved when they are impossible for the artist.
"""

from __future__ import annotations

import logging

import pytest

from core.models.track_models import TrackDict
from core.tracks.year_fallback import YearFallbackHandler
from tests.mocks.protocol_mocks import (  # sourcery skip: dont-import-test-modules
    MockExternalApiService,
    MockPendingVerificationService,
)


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create a test console logger."""
    return logging.getLogger("test.plausibility.console")


@pytest.fixture
def mock_pending_verification() -> MockPendingVerificationService:
    """Create mock pending verification service."""
    return MockPendingVerificationService()


@pytest.fixture
def mock_api_orchestrator() -> MockExternalApiService:
    """Create mock API orchestrator with get_artist_start_year."""
    mock = MockExternalApiService()
    mock.artist_activity_response = (None, None)  # Default: no artist data
    return mock


@pytest.fixture
def fallback_handler(
    console_logger: logging.Logger,
    mock_pending_verification: MockPendingVerificationService,
    mock_api_orchestrator: MockExternalApiService,
) -> YearFallbackHandler:
    """Create YearFallbackHandler with mocked dependencies."""
    return YearFallbackHandler(
        console_logger=console_logger,
        pending_verification=mock_pending_verification,
        fallback_enabled=True,
        absurd_year_threshold=1900,
        year_difference_threshold=5,
        trust_api_score_threshold=70,
        api_orchestrator=mock_api_orchestrator,
    )


@pytest.fixture
def fallback_handler_no_orchestrator(
    console_logger: logging.Logger,
    mock_pending_verification: MockPendingVerificationService,
) -> YearFallbackHandler:
    """Create YearFallbackHandler without api_orchestrator."""
    return YearFallbackHandler(
        console_logger=console_logger,
        pending_verification=mock_pending_verification,
        fallback_enabled=True,
        absurd_year_threshold=1900,
        year_difference_threshold=5,
        trust_api_score_threshold=70,
    )


class TestLowConfidenceNoExistingYear:
    """Tests for MIN_CONFIDENCE_FOR_NEW_YEAR threshold (Issue #105).

    When no existing year is present to validate against, we require a minimum
    confidence score (30%) before applying the proposed year. This prevents
    low-confidence API results from polluting the library.
    """

    @pytest.fixture
    def sample_tracks_no_year(self) -> list[TrackDict]:
        """Create sample tracks with no year."""
        return [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Artist",
                album="Album",
                genre="Rock",
                year="",
                date_added="2024-01-01",
            ),
        ]

    @pytest.mark.asyncio
    async def test_very_low_confidence_no_existing_year_skips(
        self,
        fallback_handler: YearFallbackHandler,
        mock_pending_verification: MockPendingVerificationService,
        sample_tracks_no_year: list[TrackDict],
    ) -> None:
        """Test that very low confidence (<30%) with no existing year skips update."""
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=sample_tracks_no_year,  # No year
            is_definitive=False,
            confidence_score=15,  # Below 30% threshold
            artist="Artist",
            album="Album",
        )

        assert result is None  # Skipped
        assert len(mock_pending_verification.marked_albums) == 1
        marked = mock_pending_verification.marked_albums[0]
        assert marked[2] == "very_low_confidence_no_existing"
        assert marked[3] is not None  # Metadata should be present
        assert marked[3]["confidence_score"] == 15
        assert marked[3]["threshold"] == 30

    @pytest.mark.asyncio
    async def test_low_confidence_at_threshold_applies(
        self,
        fallback_handler: YearFallbackHandler,
        sample_tracks_no_year: list[TrackDict],
    ) -> None:
        """Test that confidence exactly at threshold (30%) applies the year."""
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=sample_tracks_no_year,
            is_definitive=False,
            confidence_score=30,  # Exactly at threshold
            artist="Artist",
            album="Album",
        )

        assert result == "2020"  # Applied

    @pytest.mark.asyncio
    async def test_moderate_confidence_no_existing_year_applies(
        self,
        fallback_handler: YearFallbackHandler,
        sample_tracks_no_year: list[TrackDict],
    ) -> None:
        """Test that moderate confidence (>30%) with no existing year applies."""
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=sample_tracks_no_year,
            is_definitive=False,
            confidence_score=45,  # Above threshold
            artist="Artist",
            album="Album",
        )

        assert result == "2020"

    @pytest.mark.asyncio
    async def test_very_low_confidence_with_existing_year_still_applies(
        self,
        fallback_handler: YearFallbackHandler,
    ) -> None:
        """Test that very low confidence with existing year follows other rules.

        The MIN_CONFIDENCE_FOR_NEW_YEAR threshold only applies when there's NO
        existing year. When there IS an existing year, other rules decide.
        """
        tracks_with_year = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Artist",
                album="Album",
                genre="Rock",
                year="2018",  # Has existing year
                date_added="2024-01-01",
            ),
        ]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks_with_year,
            is_definitive=False,
            confidence_score=15,  # Very low
            artist="Artist",
            album="Album",
        )

        # Should apply since change is small (2018→2020, within 5 year threshold)
        assert result == "2020"

    @pytest.mark.asyncio
    async def test_definitive_bypasses_low_confidence_check(
        self,
        fallback_handler: YearFallbackHandler,
        sample_tracks_no_year: list[TrackDict],
    ) -> None:
        """Test that is_definitive=True bypasses the low confidence check."""
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=sample_tracks_no_year,
            is_definitive=True,  # High confidence
            confidence_score=15,  # Score is irrelevant when definitive
            artist="Artist",
            album="Album",
        )

        assert result == "2020"

    @pytest.mark.asyncio
    async def test_absurd_year_check_runs_before_confidence_check(
        self,
        fallback_handler: YearFallbackHandler,
        mock_pending_verification: MockPendingVerificationService,
        sample_tracks_no_year: list[TrackDict],
    ) -> None:
        """Test that absurd year detection runs before low confidence check.

        Order matters: absurd year (Rule 2) should catch impossible years
        before the confidence check (Rule 2.5).
        """
        result = await fallback_handler.apply_year_fallback(
            proposed_year="1850",  # Absurd year (< 1900 threshold)
            album_tracks=sample_tracks_no_year,
            is_definitive=False,
            confidence_score=15,  # Would also fail confidence check
            artist="Artist",
            album="Album",
        )

        assert result is None
        marked = mock_pending_verification.marked_albums[0]
        # Should be marked for absurd year, not low confidence
        assert marked[2] == "absurd_year_no_existing"


class TestCheckYearPlausibility:
    """Tests for _check_year_plausibility method."""

    @pytest.mark.asyncio
    async def test_returns_false_when_existing_before_artist_start(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test returns False (apply API) when existing year < artist start."""
        # Artist started in 2015, existing year is 2000 = IMPOSSIBLE
        mock_api_orchestrator.artist_activity_response = (2015, None)

        result = await fallback_handler._check_year_plausibility(
            existing_year="2000",
            proposed_year="2020",
            artist="Bad Omens",
        )

        assert result is False  # Apply API year

    @pytest.mark.asyncio
    async def test_returns_none_when_existing_is_plausible(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test returns None (continue to next rule) when existing year >= artist start."""
        # Artist started in 1981, existing year is 1986 = PLAUSIBLE
        mock_api_orchestrator.artist_activity_response = (1981, None)

        result = await fallback_handler._check_year_plausibility(
            existing_year="1986",
            proposed_year="2020",
            artist="Metallica",
        )

        assert result is None  # Continue to next rule

    @pytest.mark.asyncio
    async def test_returns_none_when_no_artist_data(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test returns None (continue to next rule) when artist data not found."""
        mock_api_orchestrator.artist_activity_response = (None, None)

        result = await fallback_handler._check_year_plausibility(
            existing_year="2000",
            proposed_year="2020",
            artist="Unknown Artist",
        )

        assert result is None  # Can't verify, continue to next rule (safer than blindly applying)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_orchestrator(
        self,
        fallback_handler_no_orchestrator: YearFallbackHandler,
    ) -> None:
        """Test returns None when api_orchestrator is not available."""
        result = await fallback_handler_no_orchestrator._check_year_plausibility(
            existing_year="2000",
            proposed_year="2020",
            artist="Artist",
        )

        assert result is None  # Can't check, continue to next rule

    @pytest.mark.asyncio
    async def test_returns_false_for_invalid_existing_year(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test returns False when existing year is invalid."""
        mock_api_orchestrator.artist_activity_response = (2000, None)

        result = await fallback_handler._check_year_plausibility(
            existing_year="invalid",
            proposed_year="2020",
            artist="Artist",
        )

        assert result is False  # Apply API year

    @pytest.mark.asyncio
    async def test_existing_year_equals_artist_start_is_plausible(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test existing year equal to artist start is plausible."""
        mock_api_orchestrator.artist_activity_response = (1983, None)

        result = await fallback_handler._check_year_plausibility(
            existing_year="1983",
            proposed_year="2020",
            artist="Metallica",
        )

        assert result is None  # Plausible, continue to next rule


class TestHandleDramaticYearChangeWithPlausibility:
    """Tests for _handle_dramatic_year_change with plausibility check."""

    @pytest.mark.asyncio
    async def test_high_confidence_bypasses_plausibility(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test high confidence API (>=70%) bypasses plausibility check."""
        # Artist started in 1981, existing is 1986, proposed is 2020
        # Even though plausible, high confidence should apply API
        mock_api_orchestrator.artist_activity_response = (1981, None)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="2020",
            existing_year="1986",
            confidence_score=85,  # High confidence
            artist="Metallica",
            album="Test Album",
        )

        assert result is False  # Don't skip - apply API year
        # High confidence bypasses plausibility check - no API call
        assert len(mock_api_orchestrator.artist_activity_requests) == 0

    @pytest.mark.asyncio
    async def test_low_confidence_implausible_year_applies_api(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
        mock_pending_verification: MockPendingVerificationService,
    ) -> None:
        """Test low confidence + implausible existing year applies API."""
        # Artist started in 2015, existing is 2000 = IMPOSSIBLE
        mock_api_orchestrator.artist_activity_response = (2015, None)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="2025",
            existing_year="2000",
            confidence_score=50,  # Low confidence
            artist="Bad Omens",
            album="Dying To Love",
        )

        assert result is False  # Don't skip - apply API year
        assert len(mock_pending_verification.marked_albums) == 1
        marked = mock_pending_verification.marked_albums[0]
        assert marked[2] == "implausible_existing_year"

    @pytest.mark.asyncio
    async def test_low_confidence_plausible_year_preserves_existing(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
        mock_pending_verification: MockPendingVerificationService,
    ) -> None:
        """Test low confidence + plausible existing year preserves existing."""
        # Artist started in 1981, existing is 1986 = PLAUSIBLE
        mock_api_orchestrator.artist_activity_response = (1981, None)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="2020",
            existing_year="1986",
            confidence_score=50,  # Low confidence
            artist="Metallica",
            album="Master of Puppets",
        )

        assert result is True  # Skip - preserve existing year
        assert len(mock_pending_verification.marked_albums) == 1
        marked = mock_pending_verification.marked_albums[0]
        assert marked[2] == "suspicious_year_change"

    @pytest.mark.asyncio
    async def test_no_artist_data_preserves_existing_year(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
        mock_pending_verification: MockPendingVerificationService,
    ) -> None:
        """Test when artist data not found, preserves existing year (safer than blindly applying)."""
        mock_api_orchestrator.artist_activity_response = (None, None)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="2020",
            existing_year="2000",
            confidence_score=50,
            artist="Unknown Artist",
            album="Unknown Album",
        )

        # Can't verify plausibility, so preserve existing year and mark for verification
        assert result is True  # Preserve existing year (safer)
        assert len(mock_pending_verification.marked_albums) == 1
        marked = mock_pending_verification.marked_albums[0]
        assert marked[2] == "suspicious_year_change"

    @pytest.mark.asyncio
    async def test_non_dramatic_change_applies_immediately(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test non-dramatic change (<= threshold) applies immediately."""
        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="2022",
            existing_year="2020",  # Only 2 year difference
            confidence_score=50,
            artist="Artist",
            album="Album",
        )

        assert result is False  # Don't skip - apply year
        # No API call for non-dramatic changes
        assert len(mock_api_orchestrator.artist_activity_requests) == 0


class TestApplyYearFallbackIntegration:
    """Integration tests for apply_year_fallback with plausibility check."""

    @pytest.fixture
    def sample_tracks_2000(self) -> list[TrackDict]:
        """Create sample tracks with year 2000."""
        return [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Bad Omens",
                album="Dying To Love",
                genre="Metal",
                year="2000",
                date_added="2024-01-01",
            ),
            TrackDict(
                id="2",
                name="Track 2",
                artist="Bad Omens",
                album="Dying To Love",
                genre="Metal",
                year="2000",
                date_added="2024-01-01",
            ),
        ]

    @pytest.fixture
    def sample_tracks_empty(self) -> list[TrackDict]:
        """Create sample tracks with no year."""
        return [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Artist",
                album="Album",
                genre="Rock",
                year="",
                date_added="2024-01-01",
            ),
        ]

    @pytest.mark.asyncio
    async def test_full_flow_implausible_existing_year(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
        sample_tracks_2000: list[TrackDict],
    ) -> None:
        """Test full flow when existing year is implausible."""
        mock_api_orchestrator.artist_activity_response = (2015, None)

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2025",
            album_tracks=sample_tracks_2000,  # Year 2000 in tracks
            is_definitive=False,
            confidence_score=50,
            artist="Bad Omens",
            album="Dying To Love",
        )

        # Should return proposed year since existing is implausible
        assert result == "2025"

    @pytest.mark.asyncio
    async def test_full_flow_definitive_bypasses_all_checks(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
        sample_tracks_2000: list[TrackDict],
    ) -> None:
        """Test is_definitive=True bypasses all fallback checks."""
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=sample_tracks_2000,
            is_definitive=True,  # High confidence from API
            confidence_score=95,
            artist="Artist",
            album="Album",
        )

        assert result == "2020"  # Apply year immediately
        # No API call when definitive
        assert len(mock_api_orchestrator.artist_activity_requests) == 0


class TestOrchestratorGetArtistStartYear:
    """Tests for ExternalApiOrchestrator.get_artist_start_year method.

    Note: These tests are in this file for completeness, but could be
    moved to test_orchestrator_comprehensive.py if preferred.
    """

    @pytest.mark.asyncio
    async def test_returns_start_year_from_activity_response(
        self,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test returns start year from artist activity response."""
        mock_api_orchestrator.artist_activity_response = (1981, None)

        result = await mock_api_orchestrator.get_artist_start_year("metallica")

        assert result == 1981
        assert "metallica" in mock_api_orchestrator.artist_activity_requests

    @pytest.mark.asyncio
    async def test_returns_none_when_no_data(
        self,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test returns None when artist data not found."""
        mock_api_orchestrator.artist_activity_response = (None, None)

        result = await mock_api_orchestrator.get_artist_start_year("unknown")

        assert result is None


class TestRealWorldScenarios:
    """Tests based on real-world failure cases from Issue #72."""

    @pytest.mark.asyncio
    async def test_children_of_bodom_wrong_year_fixed(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Test Children of Bodom - Something Wild case.

        Library year: 2005 (WRONG)
        Correct year: 1997
        Artist started: 1993

        Previous behavior: FALLBACK preserved 2005 (WRONG)
        New behavior: Should apply API year 1997 because 2005 > 1997 is plausible,
                      but low confidence should still preserve existing.
                      However, if we had high confidence, it would apply.
        """
        mock_api_orchestrator.artist_activity_response = (1993, None)

        # With high confidence, should apply API year
        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="1997",
            existing_year="2005",
            confidence_score=85,  # High confidence
            artist="Children of Bodom",
            album="Something Wild",
        )

        assert result is False  # Apply API year

    @pytest.mark.asyncio
    async def test_bad_omens_wrong_year_fixed(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
        mock_pending_verification: MockPendingVerificationService,
    ) -> None:
        """Test Bad Omens - Dying To Love case.

        Library year: 2000 (WRONG - band didn't exist)
        Correct year: 2025
        Artist started: 2015

        Previous behavior: FALLBACK preserved 2000 (WRONG)
        New behavior: Should apply 2025 because 2000 < 2015 = IMPOSSIBLE
        """
        mock_api_orchestrator.artist_activity_response = (2015, None)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="2025",
            existing_year="2000",  # Before band existed
            confidence_score=50,  # Low confidence
            artist="Bad Omens",
            album="Dying To Love",
        )

        assert result is False  # Apply API year (existing is impossible)
        marked = mock_pending_verification.marked_albums[0]
        assert marked[2] == "implausible_existing_year"
        assert marked[3] is not None  # metadata exists
        assert marked[3]["plausibility"] == "existing_year_impossible"
