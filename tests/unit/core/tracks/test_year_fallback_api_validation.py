"""Tests for Issue #93: FALLBACK validation against API results."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.track_models import TrackDict
from core.tracks.year_fallback import (
    DEFAULT_MIN_CONFIDENCE_FOR_NEW_YEAR,
    MAX_VERIFICATION_ATTEMPTS,
    YearFallbackHandler,
)

if TYPE_CHECKING:
    from core.models.protocols import PendingVerificationServiceProtocol


def make_track(artist: str, album: str, year: str | None = None) -> TrackDict:
    """Create a TrackDict for testing with minimal required fields."""
    return TrackDict(id="test-id", name="Test Track", artist=artist, album=album, year=year)


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test")


@pytest.fixture
def pending_verification() -> PendingVerificationServiceProtocol:
    """Create a mock pending verification service."""
    mock = MagicMock()
    mock.mark_for_verification = AsyncMock()
    return cast("PendingVerificationServiceProtocol", cast(object, mock))


@pytest.fixture
def api_orchestrator() -> MagicMock:
    """Create a mock API orchestrator."""
    mock = MagicMock()
    mock.get_artist_start_year = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def fallback_handler(
    logger: logging.Logger,
    pending_verification: PendingVerificationServiceProtocol,
    api_orchestrator: MagicMock,
) -> YearFallbackHandler:
    """Create a YearFallbackHandler for testing."""
    return YearFallbackHandler(
        console_logger=logger,
        pending_verification=pending_verification,
        fallback_enabled=True,
        absurd_year_threshold=1970,
        year_difference_threshold=5,
        api_orchestrator=api_orchestrator,
    )


class TestCheckExistingYearInApiResults:
    """Tests for _check_existing_year_in_api_results validation."""

    def test_returns_none_when_no_year_scores(self, fallback_handler: YearFallbackHandler) -> None:
        """Test returns None when year_scores is None."""
        result = fallback_handler._check_existing_year_in_api_results(
            existing_year="1998",
            year_scores=None,
            artist="Test",
            album="Album",
        )
        assert result is None

    def test_returns_none_when_empty_year_scores(self, fallback_handler: YearFallbackHandler) -> None:
        """Test returns None when year_scores is empty."""
        result = fallback_handler._check_existing_year_in_api_results(
            existing_year="1998",
            year_scores={},
            artist="Test",
            album="Album",
        )
        assert result is None

    def test_returns_true_when_existing_year_found(self, fallback_handler: YearFallbackHandler) -> None:
        """Test returns True when existing year is in API results."""
        result = fallback_handler._check_existing_year_in_api_results(
            existing_year="2018",
            year_scores={"2018": 85, "2020": 70},
            artist="Test",
            album="Album",
        )
        assert result is True

    def test_returns_false_when_existing_year_not_found(self, fallback_handler: YearFallbackHandler) -> None:
        """Test returns False when existing year NOT in API results."""
        result = fallback_handler._check_existing_year_in_api_results(
            existing_year="1998",
            year_scores={"2018": 85, "2019": 70},
            artist="Abney Park",
            album="Scallywag",
        )
        assert result is False


class TestIssue93FalsePositives:
    """Regression tests for Issue #93 false positives."""

    @pytest.mark.asyncio
    async def test_abney_park_scallywag_trusts_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Abney Park - Scallywag: existing=1998, API=2018, should return 2018."""
        # Mock artist period (formed 1997)
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1997)

        tracks = [make_track("Abney Park", "Scallywag", "1998")]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2018",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=60,
            artist="Abney Park",
            album="Scallywag",
            year_scores={"2018": 85},  # 1998 NOT in API results
        )

        assert result == "2018"

    @pytest.mark.asyncio
    async def test_korn_korn_trusts_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Korn - Korn: existing=2003, API=1994, should return 1994."""
        # Mock artist period (formed 1993)
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1993)

        tracks = [make_track("Korn", "Korn", "2003")]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="1994",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=60,
            artist="Korn",
            album="Korn",
            year_scores={"1994": 90},  # 2003 NOT in API results
        )

        assert result == "1994"

    @pytest.mark.asyncio
    async def test_abney_park_taxidermy_trusts_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Abney Park - Taxidermy: existing=1998, API=2019, should return 2019."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1997)

        tracks = [make_track("Abney Park", "Taxidermy", "1998")]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2019",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=55,
            artist="Abney Park",
            album="Taxidermy",
            year_scores={"2019": 80},  # 1998 NOT in API results
        )

        assert result == "2019"

    @pytest.mark.asyncio
    async def test_korn_life_is_peachy_trusts_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Korn - Life is Peachy: existing=2003, API=1996, should return 1996."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1993)

        tracks = [make_track("Korn", "Life is Peachy", "2003")]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="1996",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=65,
            artist="Korn",
            album="Life is Peachy",
            year_scores={"1996": 88},  # 2003 NOT in API results
        )

        assert result == "1996"


class TestPreservesValidExistingYear:
    """Tests for correct behavior when existing year IS in API results."""

    @pytest.mark.asyncio
    async def test_preserves_year_when_in_api_results(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Should preserve existing year if it appears in API results with dramatic change."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1990)

        tracks = [make_track("Test", "Album", "2000")]

        # Both years in API results - existing has lower score but is still valid
        # The method should NOT automatically reject 2000 just because 2020 has higher score
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Test",
            album="Album",
            year_scores={"2000": 70, "2020": 85},  # 2000 IS in API results
        )

        # With dramatic change + low confidence + both plausible + existing in API results
        # → should preserve existing (2000) per existing fallback logic
        assert result == "2000"

    @pytest.mark.asyncio
    async def test_applies_year_when_no_dramatic_change(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Should apply new year when change is not dramatic."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1990)

        tracks = [make_track("Test", "Album", "2018")]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Test",
            album="Album",
            year_scores={"2020": 85},
        )

        # No dramatic change (2 years difference <= 5 year threshold) → apply proposed
        assert result == "2020"


class TestBackwardCompatibility:
    """Tests for backward compatibility when year_scores is not provided."""

    @pytest.mark.asyncio
    async def test_works_without_year_scores(
        self,
        fallback_handler: YearFallbackHandler,
        api_orchestrator: MagicMock,
    ) -> None:
        """Should work correctly when year_scores is not provided (backward compatibility)."""
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1990)

        tracks = [make_track("Test", "Album", "2000")]

        # No year_scores provided - should fall through to existing logic
        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="Test",
            album="Album",
            # year_scores not provided - defaults to None
        )

        # Dramatic change + low confidence + both plausible + no year_scores
        # → existing logic preserves existing year
        assert result == "2000"


class TestPendingVerificationCalls:
    """Tests for pending_verification.mark_for_verification calls (Sourcery suggestion)."""

    @pytest.mark.asyncio
    async def test_marks_for_verification_when_existing_year_in_api_results(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
        api_orchestrator: MagicMock,
    ) -> None:
        """Should call mark_for_verification when existing year IS in API results.

        When:
        - Dramatic year change (>5 years)
        - Low confidence (<70%)
        - Existing year IS in API results (Issue #93 validation passes)
        - Both years plausible for artist
        → Preserve existing year AND mark for verification
        """
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1990)

        tracks = [make_track("Test", "Album", "2000")]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,  # Low confidence
            artist="Test",
            album="Album",
            year_scores={"2000": 70, "2020": 85},  # Existing year IS in API
        )

        # Should preserve existing year
        assert result == "2000"

        # Should have called mark_for_verification with "suspicious_year_change" reason
        pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["artist"] == "Test"
        assert call_kwargs["album"] == "Album"
        assert call_kwargs["reason"] == "suspicious_year_change"
        assert call_kwargs["metadata"]["existing_year"] == "2000"
        assert call_kwargs["metadata"]["proposed_year"] == "2020"

    @pytest.mark.asyncio
    async def test_no_verification_when_existing_year_not_in_api(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
        api_orchestrator: MagicMock,
    ) -> None:
        """Should NOT call mark_for_verification when existing year NOT in API.

        Issue #93 fix: When existing year has no API support, we trust API directly
        without marking for verification (we're confident existing was wrong).
        """
        api_orchestrator.get_artist_start_year = AsyncMock(return_value=1997)

        tracks = [make_track("Abney Park", "Scallywag", "1998")]

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2018",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=60,
            artist="Abney Park",
            album="Scallywag",
            year_scores={"2018": 85},  # 1998 NOT in API results
        )

        # Should apply API year
        assert result == "2018"

        # Should NOT have called mark_for_verification (confident existing was wrong)
        pending_verification.mark_for_verification.assert_not_called()


class TestEscalationLogic:
    """Tests for escalation logic after MAX_VERIFICATION_ATTEMPTS."""

    @pytest.mark.asyncio
    async def test_low_confidence_accepted_after_max_attempts(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
    ) -> None:
        """After MAX_VERIFICATION_ATTEMPTS, low-confidence year is accepted.

        When:
        - No existing year (new album)
        - Low confidence (below min_confidence_for_new_year threshold)
        - attempt_count >= MAX_VERIFICATION_ATTEMPTS
        -> Accept the year instead of blocking forever
        """
        # Mock attempt count at escalation threshold
        pending_verification.get_attempt_count = AsyncMock(return_value=MAX_VERIFICATION_ATTEMPTS)

        # Track with no existing year
        tracks = [make_track("Test Artist", "New Album")]

        # Use confidence below threshold to trigger escalation path
        low_confidence = DEFAULT_MIN_CONFIDENCE_FOR_NEW_YEAR - 10

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=low_confidence,
            artist="Test Artist",
            album="New Album",
        )

        # Should accept the year after escalation
        assert result == "2020"

        # Should NOT call mark_for_verification (escalation bypasses it)
        pending_verification.mark_for_verification.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_confidence_marked_for_verification_below_max(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
    ) -> None:
        """Below MAX_VERIFICATION_ATTEMPTS, low-confidence marks for verification.

        When:
        - No existing year (new album)
        - Low confidence (below min_confidence_for_new_year threshold)
        - attempt_count < MAX_VERIFICATION_ATTEMPTS
        -> Mark for verification and reject (return None)
        """
        # Mock attempt count below escalation threshold
        pending_verification.get_attempt_count = AsyncMock(return_value=1)

        # Track with no existing year
        tracks = [make_track("Test Artist", "New Album")]

        # Use confidence below threshold to trigger verification
        low_confidence = DEFAULT_MIN_CONFIDENCE_FOR_NEW_YEAR - 10

        result = await fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=low_confidence,
            artist="Test Artist",
            album="New Album",
        )

        # Should return None (not accepted)
        assert result is None

        # Should have called mark_for_verification
        pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["artist"] == "Test Artist"
        assert call_kwargs["album"] == "New Album"
        assert call_kwargs["reason"] == "very_low_confidence_no_existing"
        assert call_kwargs["metadata"]["proposed_year"] == "2020"
        assert call_kwargs["metadata"]["confidence_score"] == low_confidence
        assert call_kwargs["metadata"]["threshold"] == DEFAULT_MIN_CONFIDENCE_FOR_NEW_YEAR


class TestFreshAlbumDetection:
    """Tests for Rule 0.1: Fresh album detection with stale API data.

    When release_year equals the current year but API returns an older year,
    we trust Apple Music's release_year (read-only, more authoritative).
    """

    @pytest.mark.asyncio
    async def test_fresh_album_rejects_stale_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
    ) -> None:
        """API returns 2025 but release_year = 2026 (current year) → trust release_year.

        Case: Poppy "Empty Hands" - API hasn't updated yet for newly released album.
        """
        from datetime import UTC, datetime

        current_year = datetime.now(UTC).year
        stale_year = str(current_year - 1)  # API returns last year

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year=stale_year,
            existing_year=stale_year,
            confidence_score=50,
            artist="Poppy",
            album="Empty Hands",
            year_scores={stale_year: 85},
            release_year=str(current_year),  # Apple Music says current year
        )

        # Should return True (skip API year, trust release_year)
        assert result is True

        # Should mark for verification with reason "stale_api_data_for_fresh_album"
        pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["reason"] == "stale_api_data_for_fresh_album"
        assert call_kwargs["metadata"]["release_year"] == str(current_year)
        assert call_kwargs["metadata"]["proposed_year"] == stale_year

    @pytest.mark.asyncio
    async def test_fresh_album_no_rejection_when_years_match(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
    ) -> None:
        """When release_year and API year both equal current year → proceed normally."""
        from datetime import UTC, datetime

        current_year = datetime.now(UTC).year
        year_str = str(current_year)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year=year_str,
            existing_year=year_str,
            confidence_score=50,
            artist="Test",
            album="Album",
            year_scores={year_str: 85},
            release_year=year_str,  # Both match
        )

        # Should return False (years match, no dramatic change)
        assert result is False

        # Should NOT mark for verification (no issue detected)
        pending_verification.mark_for_verification.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_fresh_album_uses_normal_logic(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
    ) -> None:
        """When release_year != current year → use normal dramatic change logic."""
        from datetime import UTC, datetime

        current_year = datetime.now(UTC).year
        # Both release_year and proposed_year are old (not fresh)
        old_release = str(current_year - 5)
        old_proposed = str(current_year - 6)

        result = await fallback_handler._handle_dramatic_year_change(
            proposed_year=old_proposed,
            existing_year=old_release,
            confidence_score=50,
            artist="Test",
            album="Album",
            year_scores={old_proposed: 85},
            release_year=old_release,  # Not current year
        )

        # Should return False (not a fresh album, no special handling)
        # The 1-year difference is not dramatic (< 5 year threshold)
        assert result is False

        # Should NOT mark for verification (no dramatic change)
        pending_verification.mark_for_verification.assert_not_called()


class TestReRecordingDetection:
    """Tests for Rule 4 Enhancement: Re-recording year detection.

    When album is explicitly marked as "re-recorded" and API returns a year
    10+ years old, we reject the API year (it's likely the original album's year).
    """

    @pytest.mark.asyncio
    async def test_rerecording_rejects_old_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
    ) -> None:
        """Re-recording album, API returns 2010 (original) instead of 2026 → skip."""
        from datetime import UTC, datetime

        current_year = datetime.now(UTC).year
        original_year = str(current_year - 14)  # 14 years old (>= 10 threshold)

        result = await fallback_handler._handle_special_album_type(
            proposed_year=original_year,
            existing_year="",
            artist="Rotting Christ",
            album="Aealo (Re-Recorded)",
        )

        # Should return "" (signal to propagate existing, skip API year)
        assert result == ""

        # Should mark for verification (special album type always marks)
        pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["artist"] == "Rotting Christ"
        assert call_kwargs["album"] == "Aealo (Re-Recorded)"
        assert "reissue" in call_kwargs["reason"]
        assert call_kwargs["metadata"]["detected_pattern"] == "re-recorded"

    @pytest.mark.asyncio
    async def test_rerecording_accepts_recent_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
    ) -> None:
        """Re-recording album, API returns recent year (< 10 years) → accept."""
        from datetime import UTC, datetime

        current_year = datetime.now(UTC).year
        recent_year = str(current_year - 5)  # Only 5 years old (< 10 threshold)

        result = await fallback_handler._handle_special_album_type(
            proposed_year=recent_year,
            existing_year="",
            artist="Test Artist",
            album="Album (Re-Recorded)",
        )

        # Should return the proposed year (recent enough to trust)
        assert result == recent_year

        # Should still mark for verification (special album type always marks)
        pending_verification.mark_for_verification.assert_called_once()

    @pytest.mark.asyncio
    async def test_regular_remaster_uses_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
    ) -> None:
        """Regular remaster (not re-recording) → apply API year normally."""
        result = await fallback_handler._handle_special_album_type(
            proposed_year="2020",
            existing_year="2015",
            artist="Test Artist",
            album="Album (Remastered 2020)",
        )

        # Should return "2020" (normal remaster behavior)
        assert result == "2020"

        # Should mark for verification (all special album types get marked)
        pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = pending_verification.mark_for_verification.call_args[1]
        assert call_kwargs["metadata"]["detected_pattern"] == "remastered"

    @pytest.mark.asyncio
    async def test_anniversary_edition_uses_api_year(
        self,
        fallback_handler: YearFallbackHandler,
        pending_verification: Any,
    ) -> None:
        """Anniversary edition (not re-recording) → apply API year normally."""
        result = await fallback_handler._handle_special_album_type(
            proposed_year="2010",
            existing_year="2000",
            artist="Test Artist",
            album="Album (10th Anniversary Edition)",
        )

        # Should return "2010" (anniversary edition uses API year)
        assert result == "2010"

        # Should mark for verification (all special album types get marked)
        pending_verification.mark_for_verification.assert_called_once()
        call_kwargs = pending_verification.mark_for_verification.call_args[1]
        assert "anniversary" in call_kwargs["metadata"]["detected_pattern"]

    @pytest.mark.asyncio
    async def test_rerecording_unparseable_year_returns_empty(
        self,
        fallback_handler: YearFallbackHandler,
    ) -> None:
        """Re-recording with unparseable proposed year → skip update (return "")."""
        result = await fallback_handler._handle_special_album_type(
            proposed_year="not_a_year",
            existing_year="2015",
            artist="Rotting Christ",
            album="Aealo (Re-Recorded)",
        )

        # Unparseable year should skip the update
        assert result == ""
