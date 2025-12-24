"""Edge case tests for YearRetriever demonstrating known issues.

These tests document problematic behavior in the year retrieval system:
1. Low confidence API results overwriting valid existing years
2. Dramatic year changes without validation
3. Compilation/B-sides albums getting incorrect years

Note: Tests access private methods (prefixed with _) which is intentional
for unit testing internal behavior.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.models.track_models import TrackDict
from core.models.validators import is_empty_year
from core.retry_handler import DatabaseRetryHandler, RetryPolicy
from core.tracks.year_batch import YearBatchProcessor
from core.tracks.year_retriever import YearRetriever

# sourcery skip: dont-import-test-modules
from tests.mocks.csv_mock import MockAnalytics, MockLogger
from tests.mocks.protocol_mocks import MockCacheService, MockExternalApiService, MockPendingVerificationService
from tests.mocks.track_data import DummyTrackData


class TestYearRetrieverEdgeCases:
    """Edge case tests documenting known issues in year retrieval."""

    @staticmethod
    def _create_retry_handler() -> DatabaseRetryHandler:
        """Create a retry handler for testing."""
        import logging

        policy = RetryPolicy(
            max_retries=2,
            base_delay_seconds=0.01,
            max_delay_seconds=0.1,
            jitter_range=0.0,
            operation_timeout_seconds=30.0,
        )
        return DatabaseRetryHandler(logger=logging.getLogger("test"), default_policy=policy)

    @staticmethod
    def create_year_retriever(
        track_processor: Any = None,
        cache_service: Any = None,
        external_api: Any = None,
        pending_verification: Any = None,
        config: dict[str, Any] | None = None,
        dry_run: bool = False,
        retry_handler: DatabaseRetryHandler | None = None,
    ) -> YearRetriever:
        """Create a YearRetriever instance for testing."""
        if track_processor is None:
            track_processor = MagicMock()
            track_processor.update_track_async = AsyncMock(return_value=True)

        if cache_service is None:
            cache_service = MockCacheService()

        if external_api is None:
            external_api = MockExternalApiService()

        if pending_verification is None:
            pending_verification = MockPendingVerificationService()

        if retry_handler is None:
            retry_handler = TestYearRetrieverEdgeCases._create_retry_handler()

        test_config = config or {
            "year_retrieval": {
                "api_timeout": 30,
                "processing": {"batch_size": 50},
                "retry_attempts": 3,
            }
        }

        return YearRetriever(
            track_processor=track_processor,
            cache_service=cache_service,
            external_api=external_api,
            pending_verification=pending_verification,
            retry_handler=retry_handler,
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=MockAnalytics(),
            config=test_config,
            dry_run=dry_run,
        )

    @pytest.mark.asyncio
    async def test_low_confidence_api_overwrites_existing_year(self) -> None:
        """Test that low confidence API results for B-Sides albums are now blocked.

        ORIGINAL ISSUE: API year was applied regardless of confidence.
        FIX: B-Sides albums are detected and blocked from automatic updates.
        """
        mock_external_api = MockExternalApiService()
        # API returns 2013 with LOW confidence (is_definitive=False, score=40)
        mock_external_api.get_album_year_response = ("2013", False, 40, {"2013": 40})

        mock_pending = MockPendingVerificationService()
        retriever = self.create_year_retriever(
            external_api=mock_external_api,
            pending_verification=mock_pending,
        )
        # Using TrackDict directly - make years inconsistent to force API call
        # 4 tracks with 2011, 2 tracks with 2012 = no dominant year (60% threshold)
        album_tracks = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Blue Stahli",
                album="B-Sides and Other Things I Forgot",
                year="2011",
                track_status="subscription",
            ),
            TrackDict(
                id="2",
                name="Track 2",
                artist="Blue Stahli",
                album="B-Sides and Other Things I Forgot",
                year="2011",
                track_status="subscription",
            ),
            TrackDict(
                id="3",
                name="Track 3",
                artist="Blue Stahli",
                album="B-Sides and Other Things I Forgot",
                year="2012",
                track_status="subscription",
            ),
            TrackDict(
                id="4",
                name="Track 4",
                artist="Blue Stahli",
                album="B-Sides and Other Things I Forgot",
                year="2012",
                track_status="subscription",
            ),
        ]
        determined_year = await retriever._year_determinator.determine_album_year(
            "Blue Stahli",
            "B-Sides and Other Things I Forgot",
            album_tracks,
        )
        # The album should be marked because it's a B-Sides album
        assert mock_pending.marked_albums, "Album should be marked for verification (B-Sides detected)"
        # After the fix: B-Sides albums return existing year to propagate to all tracks
        assert determined_year == "2011", "FIXED: B-Sides albums now propagate existing year to all tracks"

    def test_track_needs_update_with_dramatic_difference(self) -> None:
        """Test that _track_needs_year_update doesn't consider year difference magnitude.

        NOTE: This is expected behavior for this low-level method.
        The magnitude check is implemented in _apply_year_fallback() which:
        - Detects dramatic year changes (>5 years threshold)
        - Marks for verification and preserves existing year

        See TestYearFallbackLogic for tests of the fix.
        """
        # Abney Park - Scallywag case: 2018 → 1998
        current_year = "2018"
        api_year = "1998"
        year_difference = abs(int(current_year) - int(api_year))

        needs_update = YearBatchProcessor._track_needs_year_update(current_year, api_year)
        # This is expected - the low-level method just checks if years differ
        assert needs_update is True, "_track_needs_year_update only checks if years differ"

    @pytest.mark.asyncio
    async def test_should_skip_album_with_inconsistent_years(self) -> None:
        """Test behavior when album has tracks with various years (compilation pattern).

        NOTE: This tests the low-level _should_skip_album_due_to_existing_years() method.
        Compilation detection is now implemented in _apply_year_fallback() which:
        - Detects 'Greatest Hits', 'Best Of', 'Compilation' patterns
        - Marks for verification and SKIPS the update

        See TestYearFallbackLogic for tests of the fix.
        """
        retriever = self.create_year_retriever()
        # HIM - And Love Said No - Greatest Hits 1997 - 2004
        album_tracks = [
            DummyTrackData.create(track_id="1", name="Join Me", artist="HIM", album="And Love Said No", year="1999"),
            DummyTrackData.create(track_id="2", name="Wicked Game", artist="HIM", album="And Love Said No", year="2001"),
            DummyTrackData.create(track_id="3", name="Funeral of Hearts", artist="HIM", album="And Love Said No", year="2003"),
            DummyTrackData.create(track_id="4", name="Buried Alive", artist="HIM", album="And Love Said No", year="2003"),
            DummyTrackData.create(track_id="5", name="Right Here", artist="HIM", album="And Love Said No", year="1997"),
        ]
        should_skip, _ = await retriever._year_determinator.should_skip_album(
            album_tracks,
            "HIM",
            "And Love Said No - Greatest Hits 1997 - 2004",
        )
        # Without cache, the method returns False to query API
        assert should_skip is False, "_should_skip_album returns False when no cache exists"

    def test_demo_vault_year_conflict(self) -> None:
        """Test Demo Vault album year determination at low-level method.

        NOTE: This tests the low-level _track_needs_year_update() method.
        Demo Vault detection is now implemented in _apply_year_fallback() which:
        - Detects 'demo', 'vault', 'archive', 'rarities' patterns
        - Marks for verification and SKIPS the update

        See TestYearFallbackLogic.test_fallback_blocks_demo_vault for tests of the fix.
        """
        original_year = "2003"  # When demos were recorded
        release_year = "2021"  # When Demo Vault was released

        # The _track_needs_year_update sees they're different and says "update"
        needs_update = YearBatchProcessor._track_needs_year_update(original_year, release_year)
        # This is expected - the low-level method just checks if years differ
        assert needs_update is True

    def test_bsides_album_year_handling(self) -> None:
        """Document B-Sides album year handling fix.

        NOTE: B-Sides detection is now implemented in _apply_year_fallback() which:
        - Detects 'b-sides', 'b-side' patterns
        - Marks for verification and SKIPS the update

        See TestYearFallbackLogic.test_fallback_blocks_bsides_album for tests of the fix.
        """

    def test_reissue_vs_original_year(self) -> None:
        """Document reissue vs original year handling fix.

        NOTE: Reissue detection is now implemented in _apply_year_fallback() which:
        - Detects 'remastered', 'anniversary', 'deluxe', 'expanded' patterns
        - Uses MARK_AND_UPDATE strategy (update but mark for verification)

        For dramatic year changes (>5 years like 2005→2012), the fallback also:
        - Blocks the update and preserves existing year
        - Marks for verification

        See TestYearFallbackLogic for tests of the fix.
        """


class TestTrackNeedsYearUpdate:
    """Tests for _track_needs_year_update edge cases."""

    @pytest.mark.parametrize(
        ("current_year", "expected"),
        [
            (None, True),
            ("", True),
            ("   ", True),
            ("0", True),  # "0" is treated as valid year but different from target
        ],
    )
    def test_empty_year_needs_update(self, current_year: Any, expected: bool) -> None:
        """Test that empty years correctly trigger updates."""
        target_year = "2020"
        result = YearBatchProcessor._track_needs_year_update(current_year, target_year)

        # For "0", the behavior is: str("0") != "2020" → True
        # But we should verify empty year check
        if current_year in (None, "", "   "):
            assert is_empty_year(current_year) is True

        assert result is expected

    def test_same_year_no_update(self) -> None:
        """Test that matching years don't trigger update."""
        current_year = "2020"
        target_year = "2020"

        result = YearBatchProcessor._track_needs_year_update(current_year, target_year)

        assert result is False

    @pytest.mark.parametrize(
        ("current_year", "target_year"),
        [
            ("2019", "2020"),  # 1 year difference
            ("2015", "2020"),  # 5 year difference
            ("2010", "2020"),  # 10 year difference
            ("1998", "2018"),  # 20 year difference (Abney Park case)
        ],
    )
    def test_different_year_triggers_update(self, current_year: str, target_year: str) -> None:
        """Test that different years trigger update regardless of difference magnitude."""
        result = YearBatchProcessor._track_needs_year_update(current_year, target_year)

        # Current behavior: ANY difference triggers update
        assert result is True


class TestDominantYearEdgeCases:
    """Tests for _get_dominant_year edge cases."""

    @staticmethod
    def create_retriever() -> YearRetriever:
        """Create a basic YearRetriever for testing."""
        return TestYearRetrieverEdgeCases.create_year_retriever()

    def test_dominant_year_threshold(self) -> None:
        """Test that dominant year requires 60% majority of ALL tracks."""
        retriever = self.create_retriever()

        # 10 tracks total
        # 5 with year 2020 (50%), 3 with 2019, 2 with empty
        album_tracks = (
            [DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="2020") for i in range(1, 6)]
            + [DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="2019") for i in range(6, 9)]
            + [DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="") for i in range(9, 11)]
        )

        dominant = retriever.year_consistency_checker.get_dominant_year(album_tracks)

        # 5/10 = 50% < 60% threshold
        assert dominant is None, "50% is below 60% threshold - no dominant year"

    def test_dominant_year_success(self) -> None:
        """Test successful dominant year detection."""
        retriever = self.create_retriever()

        # 10 tracks, 7 with 2020 (70%)
        album_tracks = [DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="2020") for i in range(1, 8)] + [
            DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="2019") for i in range(8, 11)
        ]

        dominant = retriever.year_consistency_checker.get_dominant_year(album_tracks)

        assert dominant == "2020", "70% majority should establish dominant year"


class TestYearFallbackLogic:
    """Tests for the new year fallback system that prevents incorrect year updates."""

    @staticmethod
    def create_retriever_with_fallback(
        external_api: Any = None,
        pending_verification: Any = None,
        fallback_enabled: bool = True,
        year_difference_threshold: int = 5,
        absurd_year_threshold: int = 1970,
    ) -> YearRetriever:
        """Create YearRetriever with fallback configuration."""
        config = {
            "year_retrieval": {
                "api_timeout": 30,
                "processing": {"batch_size": 50},
                "logic": {
                    "absurd_year_threshold": absurd_year_threshold,
                },
                "fallback": {
                    "enabled": fallback_enabled,
                    "year_difference_threshold": year_difference_threshold,
                },
            }
        }
        return TestYearRetrieverEdgeCases.create_year_retriever(
            external_api=external_api,
            pending_verification=pending_verification,
            config=config,
        )

    def test_fallback_config_loaded(self) -> None:
        """Test that fallback configuration is properly loaded from config."""
        retriever = self.create_retriever_with_fallback(
            year_difference_threshold=10,  # Non-default value to verify config loading
        )

        assert retriever.fallback_enabled is True  # Default value
        assert retriever.year_difference_threshold == 10  # Custom value

    @pytest.mark.parametrize(
        ("existing", "proposed", "threshold", "expected_dramatic"),
        [
            ("2018", "1998", 5, True),  # 20 years - dramatic
            ("2018", "2020", 5, False),  # 2 years - normal
            ("2005", "2012", 5, True),  # 7 years - dramatic
            ("2005", "2012", 10, False),  # 7 years with 10 threshold - normal
            ("2020", "2015", 5, False),  # 5 years exactly = not dramatic (> threshold)
            ("2020", "2014", 5, True),  # 6 years - dramatic
        ],
    )
    def test_is_year_change_dramatic(
        self,
        existing: str,
        proposed: str,
        threshold: int,
        expected_dramatic: bool,
    ) -> None:
        """Test detection of dramatic year changes."""
        retriever = self.create_retriever_with_fallback(
            year_difference_threshold=threshold,
        )

        is_dramatic = retriever.year_fallback_handler.is_year_change_dramatic(existing, proposed)

        assert is_dramatic == expected_dramatic, (
            f"Expected {existing}→{proposed} to be {'dramatic' if expected_dramatic else 'normal'} with threshold={threshold}"
        )

    def test_get_existing_year_from_tracks(self) -> None:
        """Test extraction of most common year from tracks."""
        retriever = self.create_retriever_with_fallback()

        # 3 tracks with 2011, 2 tracks with 2012
        tracks = [
            TrackDict(id="1", name="T1", artist="A", album="B", year="2011"),
            TrackDict(id="2", name="T2", artist="A", album="B", year="2011"),
            TrackDict(id="3", name="T3", artist="A", album="B", year="2011"),
            TrackDict(id="4", name="T4", artist="A", album="B", year="2012"),
            TrackDict(id="5", name="T5", artist="A", album="B", year="2012"),
        ]

        existing_year = retriever.year_fallback_handler.get_existing_year_from_tracks(tracks)
        assert existing_year == "2011"

    def test_get_existing_year_empty_tracks(self) -> None:
        """Test that empty years are handled correctly."""
        retriever = self.create_retriever_with_fallback()

        tracks = [
            TrackDict(id="1", name="T1", artist="A", album="B", year=""),
            TrackDict(id="2", name="T2", artist="A", album="B", year=None),
        ]

        existing_year = retriever.year_fallback_handler.get_existing_year_from_tracks(tracks)
        assert existing_year is None

    @pytest.mark.asyncio
    async def test_fallback_blocks_dramatic_change(self) -> None:
        """Test that dramatic year changes are blocked.

        This is the FIX for Abney Park case: 2018 → 1998.
        Uses default threshold of 5 years.
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        # Tracks with existing year 2018
        tracks = [
            TrackDict(id="1", name="T1", artist="Abney Park", album="Scallywag", year="2018"),
            TrackDict(id="2", name="T2", artist="Abney Park", album="Scallywag", year="2018"),
        ]
        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1998",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=40,  # Low confidence
            artist="Abney Park",
            album="Scallywag",
        )
        assert result == "2018", "Should return existing year to propagate to all tracks"
        assert len(mock_pending.marked_albums) == 1
        # marked_albums is list of tuples: (artist, album, reason, metadata, confidence)
        marked_artist, _album, reason, _metadata, _confidence = mock_pending.marked_albums[0]
        assert marked_artist == "Abney Park"
        assert reason == "suspicious_year_change"

    @pytest.mark.asyncio
    async def test_fallback_blocks_bsides_album(self) -> None:
        """Test that B-Sides albums are blocked and marked.

        This is the FIX for Blue Stahli case: 2011 → 2013.
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="T1", artist="Blue Stahli", album="B-Sides and Other Things I Forgot", year="2011"),
            TrackDict(id="2", name="T2", artist="Blue Stahli", album="B-Sides and Other Things I Forgot", year="2011"),
        ]
        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="2013",
            album_tracks=tracks,
            is_definitive=False,  # Low confidence
            confidence_score=40,  # Low confidence
            artist="Blue Stahli",
            album="B-Sides and Other Things I Forgot",
        )
        assert result == "2011", "Should return existing year to propagate to all tracks"
        assert len(mock_pending.marked_albums) == 1
        # marked_albums is list of tuples: (artist, album, reason, metadata, confidence)
        _artist, _album, reason, _metadata, _confidence = mock_pending.marked_albums[0]
        assert "special_album" in reason

    @pytest.mark.asyncio
    async def test_fallback_blocks_demo_vault(self) -> None:
        """Test that Demo Vault albums are blocked.

        This is the FIX for Celldweller case: 2003 → 2021.
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="T1", artist="Celldweller", album="Demo Vault: Wasteland", year="2003"),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="2021",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=40,  # Low confidence
            artist="Celldweller",
            album="Demo Vault: Wasteland",
        )

        assert result == "2003", "Demo Vault should propagate existing year to all tracks"
        assert len(mock_pending.marked_albums) == 1

    @pytest.mark.asyncio
    async def test_fallback_allows_high_confidence(self) -> None:
        """Test that high confidence (is_definitive=True) bypasses fallback checks."""
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="T1", artist="Artist", album="Album", year="2018"),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1998",  # Dramatic change!
            album_tracks=tracks,
            is_definitive=True,  # But high confidence
            confidence_score=90,  # High confidence
            artist="Artist",
            album="Album",
        )

        # High confidence = apply even if dramatic
        assert result == "1998"
        assert len(mock_pending.marked_albums) == 0

    @pytest.mark.asyncio
    async def test_fallback_allows_empty_year_update(self) -> None:
        """Test that tracks with empty years get updated."""
        retriever = self.create_retriever_with_fallback()

        tracks = [
            TrackDict(id="1", name="T1", artist="Artist", album="Album", year=""),
            TrackDict(id="2", name="T2", artist="Artist", album="Album", year=None),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,  # Medium confidence
            artist="Artist",
            album="Album",
        )

        # No existing year = nothing to preserve
        assert result == "2020"

    @pytest.mark.asyncio
    async def test_fallback_updates_reissue_with_marking(self) -> None:
        """Test that reissue albums are updated but marked for verification."""
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="T1", artist="Artist", album="Album (Remastered)", year="2000"),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,  # Medium confidence
            artist="Artist",
            album="Album (Remastered)",
        )

        # Reissues use MARK_AND_UPDATE strategy
        assert result == "2020", "Reissue should be updated"
        assert len(mock_pending.marked_albums) == 1, "But should be marked"

    @pytest.mark.asyncio
    async def test_fallback_disabled_original_behavior(self) -> None:
        """Test that disabling fallback restores original behavior."""
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
            fallback_enabled=False,  # Disabled!
        )

        tracks = [
            TrackDict(id="1", name="T1", artist="Abney Park", album="Scallywag", year="2018"),
        ]

        # With fallback disabled, dramatic changes are allowed
        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1998",
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=40,  # Low confidence
            artist="Abney Park",
            album="Scallywag",
        )

        assert result == "1998", "Original behavior: apply API year"
        # Still marks for verification (original behavior for low confidence)
        assert len(mock_pending.marked_albums) == 1

    @pytest.mark.asyncio
    async def test_fallback_skips_when_years_match(self) -> None:
        """Test that fallback returns early when existing year equals proposed year.

        This is the FIX for Issue #81: Redundant FALLBACK check when existing == proposed.
        No need to process special album types or log warnings when no change is needed.
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        # Special album type (compilation) - would normally log FALLBACK warning
        tracks = [
            TrackDict(id="1", name="T1", artist="My Dying Bride", album="34.788%... Complete", year="1998"),
            TrackDict(id="2", name="T2", artist="My Dying Bride", album="34.788%... Complete", year="1998"),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1998",  # Same as existing!
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,
            artist="My Dying Bride",
            album="34.788%... Complete",
        )

        # Should return the year (no change needed)
        assert result == "1998", "Should return proposed year when it matches existing"
        # Should NOT mark for verification (no change = no need to verify)
        assert len(mock_pending.marked_albums) == 0, "Should not mark when years match"


class TestAbsurdYearDetection:
    """Tests for absurd year detection (Rule 2 in fallback decision tree).

    Rule 2: IF proposed_year < absurd_threshold AND no existing year → MARK + SKIP

    This catches cases like:
    - Gorillaz → 1974 (band formed 1998)
    - HIM → 1980 (band formed 1991)

    When there's no existing year to compare against, we use a configurable
    threshold (default: 1970) to filter out absurd years.
    """

    @staticmethod
    def create_retriever_with_absurd_threshold(
        absurd_year_threshold: int = 1970,
        pending_verification: Any = None,
    ) -> YearRetriever:
        """Create YearRetriever with specific absurd year threshold."""
        return TestYearFallbackLogic.create_retriever_with_fallback(
            pending_verification=pending_verification,
            absurd_year_threshold=absurd_year_threshold,
        )

    def test_absurd_threshold_config_loaded(self) -> None:
        """Test that absurd_year_threshold is properly loaded from config."""
        retriever = self.create_retriever_with_absurd_threshold(
            absurd_year_threshold=1980,  # Non-default
        )

        assert retriever.absurd_year_threshold == 1980

    def test_default_absurd_threshold(self) -> None:
        """Test that default absurd_year_threshold is 1970."""
        # Create retriever without explicit threshold
        config = {"year_retrieval": {"api_timeout": 30}}
        retriever = TestYearRetrieverEdgeCases.create_year_retriever(config=config)

        assert retriever.absurd_year_threshold == 1970

    @pytest.mark.asyncio
    async def test_absurd_year_no_existing_skips(self) -> None:
        """Test that absurd year with no existing year is skipped.

        Case: Gorillaz - The Mountain → 1974 (no existing year)
        Expected: SKIP update, mark for verification
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_absurd_threshold(
            pending_verification=mock_pending,
        )

        # Track with NO existing year
        tracks = [
            TrackDict(id="1", name="The Mountain", artist="Gorillaz", album="The Mountain", year=""),  # No existing year
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1965",  # Before 1970 threshold
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=40,  # Low confidence
            artist="Gorillaz",
            album="The Mountain",
        )

        assert result is None, "Absurd year should be skipped"
        assert len(mock_pending.marked_albums) == 1, "Should be marked for verification"
        # marked_albums is a tuple: (artist, album, reason, metadata)
        assert mock_pending.marked_albums[0][2] == "absurd_year_no_existing"

    @pytest.mark.asyncio
    async def test_year_above_threshold_continues(self) -> None:
        """Test that year above threshold passes to next rule.

        Case: Album with year 1990 (> 1970 threshold)
        Expected: Continue to Rule 3 (no existing year → APPLY)
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_absurd_threshold(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", year=""),  # No existing year
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1990",  # Above 1970 threshold
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,  # Medium confidence
            artist="Artist",
            album="Album",
        )

        # Should apply (Rule 3: no existing year)
        assert result == "1990"
        assert len(mock_pending.marked_albums) == 0

    @pytest.mark.asyncio
    async def test_absurd_year_with_existing_continues(self) -> None:
        """Test that absurd year with existing year is caught by plausibility check.

        Case: Album with existing year 2005, proposed year 1965
        Expected: Skip Rule 2 (has existing), proceed to Rule 5 (dramatic change)
        → Plausibility check catches proposed_year < artist_start_year
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_absurd_threshold(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", year="2005"),  # HAS existing year
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1965",  # Absurd year, but existing year takes precedence
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=40,  # Low confidence
            artist="Artist",
            album="Album",
        )

        # Should be caught by plausibility check (proposed 1965 is before artist started)
        # Now returns existing year to propagate to all tracks
        assert result == "2005"
        assert len(mock_pending.marked_albums) == 1
        # Reason is now implausible_proposed_year (more specific than suspicious_year_change)
        # marked_albums is a tuple: (artist, album, reason, metadata)
        assert mock_pending.marked_albums[0][2] == "implausible_proposed_year"

    @pytest.mark.asyncio
    async def test_year_at_threshold_boundary(self) -> None:
        """Test boundary condition: year exactly at threshold.

        Year 1970 with threshold 1970 should NOT be skipped (< not <=)
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_absurd_threshold(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", year=""),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1970",  # Exactly at threshold
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,  # Medium confidence
            artist="Artist",
            album="Album",
        )

        # 1970 >= 1970, should NOT be absurd, should apply
        assert result == "1970"
        assert len(mock_pending.marked_albums) == 0

    @pytest.mark.asyncio
    async def test_high_confidence_bypasses_absurd_check(self) -> None:
        """Test that high confidence (is_definitive=True) bypasses Rule 2.

        When API is confident, even absurd years are applied (Rule 1).
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_absurd_threshold(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", year=""),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1960",  # Absurd year
            album_tracks=tracks,
            is_definitive=True,  # But high confidence!
            confidence_score=90,  # High confidence
            artist="Artist",
            album="Album",
        )

        # Rule 1: High confidence → apply
        assert result == "1960"
        assert len(mock_pending.marked_albums) == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("threshold", "proposed", "expected_result"),
        [
            (1970, "1969", None),  # Below default threshold
            (1970, "1971", "1971"),  # Above default threshold
            (1980, "1975", None),  # Below custom threshold
            (1980, "1985", "1985"),  # Above custom threshold
            (1950, "1960", "1960"),  # Lower threshold = more permissive
        ],
    )
    async def test_custom_threshold_levels(
        self,
        threshold: int,
        proposed: str,
        expected_result: str | None,
    ) -> None:
        """Test that custom threshold affects detection."""
        retriever = self.create_retriever_with_absurd_threshold(
            absurd_year_threshold=threshold,
        )

        tracks = [
            TrackDict(id="1", name="Track", artist="Artist", album="Album", year=""),  # No existing
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year=proposed,
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,  # Medium confidence
            artist="Artist",
            album="Album",
        )

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_real_case_gorillaz_1974(self) -> None:
        """Test real-world case: Gorillaz getting 1974 year (band formed 1998).

        Note: With threshold 1970, this would NOT be caught.
        This case is caught by scoring system's artist_period_context.
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_absurd_threshold(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="The Mountain", artist="Gorillaz", album="Plastic Beach", year=""),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1974",  # 1974 > 1970, so not "absurd" by this rule
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=50,  # Medium confidence
            artist="Gorillaz",
            album="Plastic Beach",
        )

        # 1974 > 1970 threshold, so it passes Rule 2
        # Rule 3: No existing year → apply
        # NOTE: This case should be caught by scoring system, not fallback
        assert result == "1974"

    @pytest.mark.asyncio
    async def test_pre_rock_era_years_blocked(self) -> None:
        """Test that pre-rock era years (< 1950) are blocked.

        Years like 1920, 1940 are clearly absurd for modern music libraries.
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_absurd_threshold(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="Track", artist="Modern Band", album="Album", year=""),
        ]

        # Test various clearly absurd years
        for absurd_year in ["1920", "1940", "1950", "1960", "1969"]:
            mock_pending.marked_albums.clear()

            result = await retriever.year_fallback_handler.apply_year_fallback(
                proposed_year=absurd_year,
                album_tracks=tracks,
                is_definitive=False,
                confidence_score=40,  # Low confidence
                artist="Modern Band",
                album="Album",
            )

            assert result is None, f"Year {absurd_year} should be blocked"
            assert len(mock_pending.marked_albums) == 1


class TestSuspiciousOldYearDetection:
    """Tests for _is_year_suspiciously_old method in YearConsistencyChecker.

    This feature catches cases where all tracks have the same wrong year
    (100% consensus on wrong data) by comparing album year to when tracks
    were added to the library.

    Real case: Equilibrium - Equinox
    - All 13 tracks have year 2001
    - But tracks were added in 2025
    - Year gap = 24 years >> 10 year threshold
    - Should trigger API verification instead of trusting local data
    """

    @staticmethod
    def create_retriever() -> YearRetriever:
        """Create YearRetriever with default suspicion threshold (10 years)."""
        config = {
            "year_retrieval": {
                "api_timeout": 30,
                "processing": {"batch_size": 50},
            }
        }
        return TestYearRetrieverEdgeCases.create_year_retriever(config=config)

    def test_equilibrium_equinox_case(self) -> None:
        """Test real-world case: Equilibrium - Equinox.

        All 13 tracks have year 2001, but were added to library in 2025.
        The 24-year gap should trigger API verification.
        """
        retriever = self.create_retriever()

        # Simulate Equilibrium - Equinox tracks
        album_tracks = [
            TrackDict(
                id=str(i),
                name=f"Track {i}",
                artist="Equilibrium",
                album="Equinox",
                year="2001",  # All have 2001
                date_added="2025-10-15 12:00:00",  # Added in 2025
            )
            for i in range(1, 14)  # 13 tracks
        ]

        # Check if year is suspicious
        is_suspicious = retriever.year_consistency_checker._is_year_suspiciously_old("2001", album_tracks)

        assert is_suspicious is True, "Year 2001 should be suspicious when tracks added in 2025 (24 year gap > 10 year threshold)"

    def test_dominant_year_returns_none_when_suspicious(self) -> None:
        """Test that get_dominant_year returns None for suspicious years.

        This triggers API verification instead of trusting wrong local data.
        """
        retriever = self.create_retriever()

        # 100% consensus on wrong year, but recently added
        album_tracks = [
            TrackDict(
                id=str(i),
                name=f"Track {i}",
                artist="Test Artist",
                album="Test Album",
                year="2001",
                date_added="2025-01-15 10:00:00",
            )
            for i in range(1, 11)  # 10 tracks
        ]

        dominant = retriever.year_consistency_checker.get_dominant_year(album_tracks)

        # Should return None to trigger API lookup
        assert dominant is None, "Should return None for suspiciously old year to trigger API verification"

    def test_non_suspicious_year_passes(self) -> None:
        """Test that recently released albums aren't flagged as suspicious."""
        retriever = self.create_retriever()

        # Album released 2023, added to library 2024 (1 year gap)
        album_tracks = [
            TrackDict(
                id=str(i),
                name=f"Track {i}",
                artist="Test Artist",
                album="Test Album",
                year="2023",
                date_added="2024-03-15 10:00:00",
            )
            for i in range(1, 11)
        ]

        is_suspicious = retriever.year_consistency_checker._is_year_suspiciously_old("2023", album_tracks)

        assert is_suspicious is False, "1 year gap should not be suspicious"

    def test_missing_date_added_not_suspicious(self) -> None:
        """Test that tracks without date_added aren't flagged."""
        retriever = self.create_retriever()

        album_tracks = [
            TrackDict(
                id=str(i),
                name=f"Track {i}",
                artist="Test Artist",
                album="Test Album",
                year="2001",
                # No date_added field
            )
            for i in range(1, 11)
        ]

        is_suspicious = retriever.year_consistency_checker._is_year_suspiciously_old("2001", album_tracks)

        assert is_suspicious is False, "Without date_added, can't determine suspicion"

    def test_boundary_exactly_at_threshold(self) -> None:
        """Test boundary: exactly 10 years difference should NOT be suspicious."""
        retriever = self.create_retriever()

        # Album released 2014, added to library 2024 (exactly 10 years)
        album_tracks = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Test Artist",
                album="Test Album",
                year="2014",
                date_added="2024-01-01 10:00:00",
            )
        ]

        is_suspicious = retriever.year_consistency_checker._is_year_suspiciously_old("2014", album_tracks)

        # threshold check is > not >=, so exactly 10 should NOT be suspicious
        assert is_suspicious is False, "Exactly 10 year gap should not be suspicious (> not >=)"

    def test_boundary_one_over_threshold(self) -> None:
        """Test boundary: 11 years difference should be suspicious."""
        retriever = self.create_retriever()

        # Album released 2013, added to library 2024 (11 years)
        album_tracks = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Test Artist",
                album="Test Album",
                year="2013",
                date_added="2024-01-01 10:00:00",
            )
        ]

        is_suspicious = retriever.year_consistency_checker._is_year_suspiciously_old("2013", album_tracks)

        assert is_suspicious is True, "11 year gap should be suspicious"

    def test_uses_earliest_date_added(self) -> None:
        """Test that the earliest date_added is used for comparison."""
        retriever = self.create_retriever()

        album_tracks = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Test Artist",
                album="Test Album",
                year="2015",
                date_added="2024-06-01 10:00:00",  # Later
            ),
            TrackDict(
                id="2",
                name="Track 2",
                artist="Test Artist",
                album="Test Album",
                year="2015",
                date_added="2020-01-15 10:00:00",  # Earlier - 5 years gap
            ),
        ]

        is_suspicious = retriever.year_consistency_checker._is_year_suspiciously_old("2015", album_tracks)

        # Earliest added: 2020, year: 2015 = 5 years gap (not suspicious)
        assert is_suspicious is False, "Should use earliest date_added (2020). 5 year gap is not suspicious."

    def test_invalid_year_string(self) -> None:
        """Test that invalid year strings are handled gracefully."""
        retriever = self.create_retriever()

        album_tracks = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Test Artist",
                album="Test Album",
                year="invalid",
                date_added="2024-01-01 10:00:00",
            )
        ]

        is_suspicious = retriever.year_consistency_checker._is_year_suspiciously_old("invalid", album_tracks)

        assert is_suspicious is False, "Invalid year should not be suspicious"

    def test_invalid_date_added_format(self) -> None:
        """Test that tracks with invalid date_added are skipped."""
        retriever = self.create_retriever()

        album_tracks = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Test Artist",
                album="Test Album",
                year="2001",
                date_added="not-a-date",  # Invalid format
            ),
            TrackDict(
                id="2",
                name="Track 2",
                artist="Test Artist",
                album="Test Album",
                year="2001",
                date_added="2025-01-01 10:00:00",  # Valid
            ),
        ]

        is_suspicious = retriever.year_consistency_checker._is_year_suspiciously_old("2001", album_tracks)

        # Uses valid date from track 2: 2025 - 2001 = 24 years gap
        assert is_suspicious is True, "Should use valid date_added and detect 24 year gap as suspicious"

    def test_collaboration_album_with_suspicious_year(self) -> None:
        """Test that collaboration album pattern also checks for suspicious years.

        When some tracks have empty years but the rest are consistent,
        we still check if the consistent year is suspiciously old.
        """
        retriever = self.create_retriever()

        album_tracks = [
            # 7 tracks with same year
            *[
                TrackDict(
                    id=str(i),
                    name=f"Track {i}",
                    artist="Test Artist",
                    album="Test Album",
                    year="2001",
                    date_added="2025-01-15 10:00:00",
                )
                for i in range(1, 8)
            ],
            # 3 tracks with empty year
            *[
                TrackDict(
                    id=str(i),
                    name=f"Track {i}",
                    artist="Test Artist",
                    album="Test Album",
                    year="",
                    date_added="2025-01-15 10:00:00",
                )
                for i in range(8, 11)
            ],
        ]

        dominant = retriever.year_consistency_checker.get_dominant_year(album_tracks)

        # 7/10 = 70% have 2001 (above 50% threshold)
        # But 2001 is suspicious (24 year gap from 2025)
        # Should NOT trust this year
        assert dominant is None, "Collaboration pattern should still check for suspicious years"


class TestAnomalousTracksLogging:
    """Tests for _log_anomalous_tracks debug logging."""

    @staticmethod
    def create_retriever() -> YearRetriever:
        """Create a basic YearRetriever for testing."""
        return TestYearRetrieverEdgeCases.create_year_retriever()

    @staticmethod
    def get_debug_logs(retriever: YearRetriever) -> list[str]:
        """Extract debug logs from retriever's consistency checker."""
        logs: list[str] = retriever.year_consistency_checker.console_logger.debug_messages
        return logs

    def test_logs_anomalous_tracks(self) -> None:
        """Test that tracks with non-dominant years are logged at DEBUG level."""
        retriever = self.create_retriever()
        checker = retriever.year_consistency_checker
        album_tracks = [
            {"id": "1", "name": "Track 1", "year": "2020"},
            {"id": "2", "name": "Track 2", "year": "2020"},
            {"id": "3", "name": "Track 3", "year": "2020"},
            {"id": "4", "name": "Track 4", "year": "2020"},
            {"id": "5", "name": "Track 5", "year": "2020"},
            {"id": "6", "name": "Track 6", "year": "2020"},
            {"id": "7", "name": "Bonus Track", "year": "2021"},  # Anomalous
        ]

        dominant = checker.get_dominant_year(album_tracks)
        debug_logs = self.get_debug_logs(retriever)

        assert dominant == "2020"
        assert any("Bonus Track" in msg for msg in debug_logs)
        assert any("2021" in msg for msg in debug_logs)
        assert any("2020" in msg for msg in debug_logs)

    def test_no_logging_when_all_same(self) -> None:
        """Test that no anomaly logging occurs when all tracks have the same year."""
        retriever = self.create_retriever()
        checker = retriever.year_consistency_checker
        album_tracks = [
            {"id": "1", "name": "Track 1", "year": "2020"},
            {"id": "2", "name": "Track 2", "year": "2020"},
            {"id": "3", "name": "Track 3", "year": "2020"},
        ]

        dominant = checker.get_dominant_year(album_tracks)
        debug_logs = self.get_debug_logs(retriever)

        assert dominant == "2020"
        assert all("differs from dominant" not in msg for msg in debug_logs)

    def test_ignores_empty_years(self) -> None:
        """Test that empty/zero years are not reported as anomalies."""
        retriever = self.create_retriever()
        checker = retriever.year_consistency_checker
        album_tracks = [
            {"id": "1", "name": "Track 1", "year": "2020"},
            {"id": "2", "name": "Track 2", "year": "2020"},
            {"id": "3", "name": "Track 3", "year": "2020"},
            {"id": "4", "name": "Track 4", "year": "2020"},
            {"id": "5", "name": "Empty Year", "year": ""},  # Should be ignored
            {"id": "6", "name": "Zero Year", "year": "0"},  # Should be ignored
        ]

        dominant = checker.get_dominant_year(album_tracks)
        debug_logs = self.get_debug_logs(retriever)

        assert dominant == "2020"
        assert all("Empty Year" not in msg for msg in debug_logs)
        assert all("Zero Year" not in msg for msg in debug_logs)


class TestYearFallbackConfidenceScoring:
    """Tests for the confidence score-based FALLBACK logic (Issue #72 fix).

    These tests verify that:
    1. High confidence API results (>=70%) override dramatic year changes
    2. Low confidence results preserve existing years for dramatic changes
    """

    @staticmethod
    def create_retriever_with_fallback(
        pending_verification: Any = None,
        trust_api_score_threshold: int = 70,
    ) -> YearRetriever:
        """Create YearRetriever with fallback configuration."""
        config = {
            "year_retrieval": {
                "api_timeout": 30,
                "processing": {"batch_size": 50},
                "logic": {
                    "absurd_year_threshold": 1970,
                },
                "fallback": {
                    "enabled": True,
                    "year_difference_threshold": 5,
                    "trust_api_score_threshold": trust_api_score_threshold,
                },
            }
        }
        return TestYearRetrieverEdgeCases.create_year_retriever(
            pending_verification=pending_verification or MockPendingVerificationService(),
            config=config,
        )

    @pytest.mark.asyncio
    async def test_high_confidence_overrides_dramatic_change(self) -> None:
        """Test that high confidence API results are applied despite dramatic year change.

        This is the FIX for Issue #72: Children of Bodom - Something Wild
        Library year: 2005, API year: 1997, API should win with high confidence.
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        # Tracks with WRONG library year (2005)
        tracks = [
            TrackDict(id="1", name="Deadnight Warrior", artist="Children of Bodom", album="Something Wild", year="2005"),
            TrackDict(id="2", name="In the Shadows", artist="Children of Bodom", album="Something Wild", year="2005"),
        ]
        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="1997",  # Correct year from API
            album_tracks=tracks,
            is_definitive=True,
            confidence_score=85,  # HIGH confidence (>= 70)
            artist="Children of Bodom",
            album="Something Wild",
        )
        assert result == "1997", "High confidence should apply API year"
        assert len(mock_pending.marked_albums) == 0, "Should not mark for verification"

    @pytest.mark.asyncio
    async def test_low_confidence_preserves_valid_existing(self) -> None:
        """Test that low confidence API results preserve valid existing years.

        When the existing year looks valid (not a placeholder, reasonable value)
        and API confidence is low, preserve the existing year.
        """
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        # Tracks with valid existing year (2018)
        tracks = [
            TrackDict(id="1", name="Track 1", artist="Blue Stahli", album="Obsidian", year="2018"),
            TrackDict(id="2", name="Track 2", artist="Blue Stahli", album="Obsidian", year="2018"),
        ]
        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="2010",  # 8 year difference
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=40,  # LOW confidence (< 70)
            artist="Blue Stahli",
            album="Obsidian",
        )
        assert result == "2018", "Low confidence should propagate existing year to all tracks"
        assert len(mock_pending.marked_albums) == 1
        # marked_albums is list of tuples: (artist, album, reason, metadata, confidence)
        marked_artist, _album, reason, _metadata, _confidence = mock_pending.marked_albums[0]
        assert marked_artist == "Blue Stahli"
        assert reason == "suspicious_year_change"

    @pytest.mark.asyncio
    async def test_confidence_threshold_boundary(self) -> None:
        """Test that exactly 70% confidence (threshold) allows update."""
        mock_pending = MockPendingVerificationService()
        # Using default trust_api_score_threshold=70
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="Track 1", artist="Test", album="Album", year="2015"),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="2005",  # 10 year dramatic change
            album_tracks=tracks,
            is_definitive=True,
            confidence_score=70,  # Exactly at threshold
            artist="Test",
            album="Album",
        )

        assert result == "2005", "Confidence at threshold (70) should allow update"

    @pytest.mark.asyncio
    async def test_confidence_below_threshold_blocks(self) -> None:
        """Test that 69% confidence (just below threshold) blocks update."""
        mock_pending = MockPendingVerificationService()
        # Using default trust_api_score_threshold=70
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="Track 1", artist="Test", album="Album", year="2015"),
        ]

        result = await retriever.year_fallback_handler.apply_year_fallback(
            proposed_year="2005",  # 10 year dramatic change
            album_tracks=tracks,
            is_definitive=False,
            confidence_score=69,  # Just below threshold
            artist="Test",
            album="Album",
        )

        assert result == "2015", "Confidence below threshold (69) should propagate existing year"
        assert len(mock_pending.marked_albums) == 1
