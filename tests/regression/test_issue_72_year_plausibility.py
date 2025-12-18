"""Regression tests for Issue #72: Year Plausibility Check.

Issue: https://github.com/barad1tos/GenreUpdater/issues/72

Problem: FALLBACK logic preserves WRONG years. If existing year differs >5 years
from API and confidence <70%, it keeps existing. But existing is often WRONG.

Example cases:
- Bad Omens "Dying To Love": Library year 2000 (band formed 2015) = IMPOSSIBLE
- Children of Bodom "Something Wild": Library year 2005 (actual 1997) = WRONG

Solution: Check if existing year is even POSSIBLE for the artist before preserving.
"""

from __future__ import annotations

import logging

import pytest

from core.models.track_models import TrackDict
from core.tracks.year_fallback import YearFallbackHandler
from tests.mocks.protocol_mocks import (
    MockExternalApiService,
    MockPendingVerificationService,
)


# Known Issue #72 cases: (artist, album, wrong_year, correct_year, artist_start_year)
ISSUE_72_CASES: list[tuple[str, str, str, str, int]] = [
    # Band formed in 2015, year 2000 is IMPOSSIBLE
    ("Bad Omens", "Dying To Love", "2000", "2025", 2015),
    # Album released 1997, year 2005 is WRONG (but plausible - band formed 1993)
    ("Children of Bodom", "Something Wild", "2005", "1997", 1993),
]


@pytest.fixture
def mock_api_orchestrator() -> MockExternalApiService:
    """Create mock API orchestrator."""
    return MockExternalApiService()


@pytest.fixture
def mock_pending_verification() -> MockPendingVerificationService:
    """Create mock pending verification service."""
    return MockPendingVerificationService()


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create console logger for tests."""
    return logging.getLogger("regression.issue72")


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


@pytest.mark.regression
class TestIssue72YearPlausibility:
    """Regression tests for Issue #72 - Year Plausibility Check.

    These tests ensure that IMPOSSIBLE years (before artist existed)
    are NOT preserved by the fallback logic.
    """

    @pytest.mark.asyncio
    async def test_bad_omens_impossible_year_not_preserved(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
        mock_pending_verification: MockPendingVerificationService,
    ) -> None:
        """Issue #72: Bad Omens year 2000 should NOT be preserved.

        Bad Omens formed in 2015. Year 2000 is IMPOSSIBLE.
        Previous bug: FALLBACK preserved 2000 because confidence was <70%.
        Fix: Plausibility check detects 2000 < 2015 = IMPOSSIBLE, applies API year.
        """
        mock_api_orchestrator.artist_activity_response = (2015, None)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="2025",
            existing_year="2000",
            confidence_score=50,  # Low confidence - would trigger old FALLBACK
            artist="Bad Omens",
            album="Dying To Love",
        )

        # Should NOT skip (False = apply API year)
        assert result is False, (
            "Issue #72 regression: Bad Omens year 2000 was preserved despite being "
            "impossible (band formed 2015). Plausibility check should apply API year."
        )

        # Should be marked with implausible_existing_year reason
        assert len(mock_pending_verification.marked_albums) == 1
        marked = mock_pending_verification.marked_albums[0]
        assert marked[2] == "implausible_existing_year"

    @pytest.mark.asyncio
    async def test_children_of_bodom_wrong_year_with_high_confidence(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Issue #72: Children of Bodom wrong year should be fixed with high confidence.

        Album: Something Wild (1997)
        Library year: 2005 (WRONG but plausible - band formed 1993)
        With high confidence API, should apply correct year regardless of plausibility.
        """
        mock_api_orchestrator.artist_activity_response = (1993, None)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="1997",
            existing_year="2005",
            confidence_score=85,  # High confidence
            artist="Children of Bodom",
            album="Something Wild",
        )

        # High confidence = always apply API year
        assert result is False, (
            "Issue #72 regression: High confidence API year not applied. With confidence >= 70%, API year should always be applied."
        )

    @pytest.mark.asyncio
    async def test_plausible_year_preserved_with_low_confidence(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
        mock_pending_verification: MockPendingVerificationService,
    ) -> None:
        """Verify plausible years ARE preserved with low confidence (correct behavior).

        Metallica formed in 1981. Year 1986 is plausible.
        With low confidence, should preserve existing year.
        This is the CORRECT behavior that Issue #72 fix should NOT break.
        """
        mock_api_orchestrator.artist_activity_response = (1981, None)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year="2020",
            existing_year="1986",
            confidence_score=50,  # Low confidence
            artist="Metallica",
            album="Master of Puppets",
        )

        # Should skip (True = preserve existing plausible year)
        assert result is True, (
            "Plausibility check incorrectly rejected a valid year. 1986 is plausible for Metallica (formed 1981) and should be preserved."
        )

        # Should be marked for verification (suspicious change)
        assert len(mock_pending_verification.marked_albums) == 1
        marked = mock_pending_verification.marked_albums[0]
        assert marked[2] == "suspicious_year_change"


@pytest.mark.regression
class TestIssue72FullFlow:
    """Full flow regression tests for Issue #72."""

    @pytest.fixture
    def bad_omens_tracks(self) -> list[TrackDict]:
        """Create Bad Omens tracks with wrong year 2000."""
        return [
            TrackDict(
                id="bo1",
                name="Like A Villain",
                artist="Bad Omens",
                album="Dying To Love",
                genre="Metalcore",
                year="2000",
                date_added="2024-01-15",
            ),
            TrackDict(
                id="bo2",
                name="Nowhere To Go",
                artist="Bad Omens",
                album="Dying To Love",
                genre="Metalcore",
                year="2000",
                date_added="2024-01-15",
            ),
        ]

    @pytest.mark.asyncio
    async def test_full_fallback_flow_with_impossible_year(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
        bad_omens_tracks: list[TrackDict],
    ) -> None:
        """Test complete apply_year_fallback flow with impossible existing year."""
        mock_api_orchestrator.artist_activity_response = (2015, None)

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2025",
            album_tracks=bad_omens_tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Bad Omens",
            album="Dying To Love",
        )

        # Should return proposed year (2025), not preserve impossible year (2000)
        assert result == "2025", (
            f"Issue #72 regression: apply_year_fallback returned '{result}' instead of '2025'. Impossible year 2000 should NOT be preserved."
        )


@pytest.mark.regression
class TestIssue72EdgeCases:
    """Edge cases for Issue #72 plausibility check."""

    @pytest.mark.asyncio
    async def test_year_equal_to_artist_start_is_plausible(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Year equal to artist start year should be considered plausible."""
        mock_api_orchestrator.artist_activity_response = (1983, None)

        result = await fallback_handler._check_year_plausibility(
            existing_year="1983",
            proposed_year="2020",
            artist="Metallica",
        )

        # None = plausible, continue to next rule
        assert result is None, "Year equal to artist start should be plausible"

    @pytest.mark.asyncio
    async def test_year_one_before_artist_start_is_implausible(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Year one year before artist start should be implausible."""
        mock_api_orchestrator.artist_activity_response = (2000, None)

        result = await fallback_handler._check_year_plausibility(
            existing_year="1999",
            proposed_year="2020",
            artist="Test Band",
        )

        # False = implausible, apply API year
        assert result is False, "Year before artist start should be implausible"

    @pytest.mark.asyncio
    async def test_unknown_artist_continues_to_next_rule(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """When artist start year is unknown, should continue to next rule (safer).

        Without artist data, we can't verify plausibility. Rather than blindly
        applying API year, we continue to the next rule which may preserve
        existing year if the change is dramatic and confidence is low.
        """
        mock_api_orchestrator.artist_activity_response = (None, None)

        result = await fallback_handler._check_year_plausibility(
            existing_year="2000",
            proposed_year="2020",
            artist="Unknown Artist",
        )

        # None = no data, continue to next rule (safer than blindly applying)
        assert result is None, "Unknown artist should continue to next rule"

    @pytest.mark.asyncio
    async def test_invalid_existing_year_applies_api(
        self,
        fallback_handler: YearFallbackHandler,
        mock_api_orchestrator: MockExternalApiService,
    ) -> None:
        """Invalid existing year format should apply API year."""
        mock_api_orchestrator.artist_activity_response = (2000, None)

        result = await fallback_handler._check_year_plausibility(
            existing_year="not-a-year",
            proposed_year="2020",
            artist="Artist",
        )

        # False = invalid year, apply API
        assert result is False, "Invalid year format should apply API year"
