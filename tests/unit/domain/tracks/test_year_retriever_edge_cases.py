"""Edge case tests for YearRetriever demonstrating known issues.

These tests document problematic behavior in the year retrieval system:
1. Low confidence API results overwriting valid existing years
2. Dramatic year changes without validation
3. Compilation/B-sides albums getting incorrect years

Note: Tests access private methods (prefixed with _) which is intentional
for unit testing internal behavior.
"""

# ruff: noqa: SLF001
# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import allure
import pytest

from src.domain.tracks.year_retriever import YearRetriever
from src.domain.tracks.year_retriever import is_empty_year
from src.shared.data.models import TrackDict
# sourcery skip: dont-import-test-modules
from tests.mocks.csv_mock import MockAnalytics
from tests.mocks.csv_mock import MockLogger
from tests.mocks.protocol_mocks import MockCacheService
from tests.mocks.protocol_mocks import MockExternalApiService
from tests.mocks.protocol_mocks import MockPendingVerificationService
from tests.mocks.track_data import DummyTrackData


@allure.epic("Music Genre Updater")
@allure.feature("Year Retrieval Edge Cases")
class TestYearRetrieverEdgeCases:
    """Edge case tests documenting known issues in year retrieval."""

    @staticmethod
    def create_year_retriever(
        track_processor: Any = None,
        cache_service: Any = None,
        external_api: Any = None,
        pending_verification: Any = None,
        config: dict[str, Any] | None = None,
        dry_run: bool = False,
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
            console_logger=MockLogger(),
            error_logger=MockLogger(),
            analytics=MockAnalytics(),
            config=test_config,
            dry_run=dry_run,
        )

    @allure.story("Low Confidence API Results")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("FIXED: Low confidence API result no longer overwrites B-Sides album")
    @allure.description(
        "This test verifies the FIX for the issue where is_definitive=False would still "
        "overwrite existing years. Real case: Blue Stahli - B-Sides: 2011 → 2013 (now blocked)"
    )
    @pytest.mark.asyncio
    async def test_low_confidence_api_overwrites_existing_year(self) -> None:
        """Test that low confidence API results for B-Sides albums are now blocked.

        ORIGINAL ISSUE: API year was applied regardless of confidence.
        FIX: B-Sides albums are detected and blocked from automatic updates.
        """
        with allure.step("Setup: Mock API returns year with low confidence"):
            mock_external_api = MockExternalApiService()
            # API returns 2013 with LOW confidence (is_definitive=False)
            mock_external_api.get_album_year_response = ("2013", False)

            mock_pending = MockPendingVerificationService()
            retriever = self.create_year_retriever(
                external_api=mock_external_api,
                pending_verification=mock_pending,
            )

        with allure.step("Create B-Sides album with inconsistent years"):
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

        with allure.step("Determine album year"):
            determined_year = await retriever._determine_album_year(
                "Blue Stahli",
                "B-Sides and Other Things I Forgot",
                album_tracks,
            )

        with allure.step("Verify: Album was marked for verification"):
            # The album should be marked because it's a B-Sides album
            assert mock_pending.marked_albums, (
                "Album should be marked for verification (B-Sides detected)"
            )

            allure.attach(
                str(mock_pending.marked_albums),
                "Marked Albums",
                allure.attachment_type.TEXT,
            )

        with allure.step("FIXED: B-Sides album update is now blocked"):
            # After the fix: B-Sides albums return None to skip update
            assert determined_year is None, (
                "FIXED: B-Sides albums now return None to preserve existing year"
            )

            allure.attach(
                f"Existing year: 2011\n"
                f"API returned: 2013 (is_definitive=False)\n"
                f"Determined year: {determined_year}\n"
                f"FIXED: Year update blocked, existing year preserved",
                "Fix Verification",
                allure.attachment_type.TEXT,
            )

    @allure.story("Dramatic Year Changes")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Low-level: _track_needs_year_update returns True for any difference")
    @allure.description(
        "Documents that _track_needs_year_update() doesn't consider magnitude. "
        "The dramatic change protection is implemented in _apply_year_fallback(). "
        "See TestYearFallbackLogic.test_fallback_blocks_dramatic_change for the fix."
    )
    def test_track_needs_update_with_dramatic_difference(self) -> None:
        """Test that _track_needs_year_update doesn't consider year difference magnitude.

        NOTE: This is expected behavior for this low-level method.
        The magnitude check is implemented in _apply_year_fallback() which:
        - Detects dramatic year changes (>5 years threshold)
        - Marks for verification and preserves existing year

        See TestYearFallbackLogic for tests of the fix.
        """
        with allure.step("Test: Dramatic 20-year difference"):
            # Abney Park - Scallywag case: 2018 → 1998
            current_year = "2018"
            api_year = "1998"
            year_difference = abs(int(current_year) - int(api_year))

            needs_update = YearRetriever._track_needs_year_update(current_year, api_year)

            allure.attach(
                f"Current year: {current_year}\n"
                f"API year: {api_year}\n"
                f"Year difference: {year_difference} years\n"
                f"needs_update: {needs_update}",
                "Test Data",
                allure.attachment_type.TEXT,
            )

        with allure.step("Low-level method returns True (fix is in fallback layer)"):
            # This is expected - the low-level method just checks if years differ
            assert needs_update is True, (
                "_track_needs_year_update only checks if years differ"
            )

            allure.attach(
                "NOTE: _track_needs_year_update() only checks if years differ.\n"
                "The dramatic change protection is in _apply_year_fallback():\n"
                "- Threshold: 5 years (configurable)\n"
                "- Action: Mark for verification, preserve existing year\n"
                "See: TestYearFallbackLogic.test_fallback_blocks_dramatic_change",
                "Implementation Note",
                allure.attachment_type.TEXT,
            )

    @allure.story("Compilation Albums")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Low-level: _should_skip_album doesn't detect Greatest Hits pattern")
    @allure.description(
        "Documents that _should_skip_album_due_to_existing_years() doesn't detect compilation albums. "
        "The 'Greatest Hits' detection is implemented in _apply_year_fallback() via detect_album_type(). "
        "See TestYearFallbackLogic for tests of the fix."
    )
    def test_should_skip_album_with_inconsistent_years(self) -> None:
        """Test behavior when album has tracks with various years (compilation pattern).

        NOTE: This tests the low-level _should_skip_album_due_to_existing_years() method.
        Compilation detection is now implemented in _apply_year_fallback() which:
        - Detects 'Greatest Hits', 'Best Of', 'Compilation' patterns
        - Marks for verification and SKIPS the update

        See TestYearFallbackLogic for tests of the fix.
        """
        retriever = self.create_year_retriever()

        with allure.step("Create Greatest Hits album with tracks from different years"):
            # HIM - And Love Said No - Greatest Hits 1997 - 2004
            album_tracks = [
                DummyTrackData.create(track_id="1", name="Join Me", artist="HIM", album="And Love Said No", year="1999"),
                DummyTrackData.create(track_id="2", name="Wicked Game", artist="HIM", album="And Love Said No", year="2001"),
                DummyTrackData.create(track_id="3", name="Funeral of Hearts", artist="HIM", album="And Love Said No", year="2003"),
                DummyTrackData.create(track_id="4", name="Buried Alive", artist="HIM", album="And Love Said No", year="2003"),
                DummyTrackData.create(track_id="5", name="Right Here", artist="HIM", album="And Love Said No", year="1997"),
            ]

        with allure.step("Check if album should be skipped by low-level method"):
            should_skip = retriever._should_skip_album_due_to_existing_years(
                album_tracks,
                "HIM",
                "And Love Said No - Greatest Hits 1997 - 2004",
            )

        with allure.step("Low-level method returns False (fix is in fallback layer)"):
            # This is expected - the low-level method checks for consistent years
            assert should_skip is False, (
                "_should_skip_album_due_to_existing_years checks year consistency, not album type"
            )

            allure.attach(
                "NOTE: _should_skip_album_due_to_existing_years() only checks year consistency.\n"
                "Compilation detection is in _apply_year_fallback() via detect_album_type():\n"
                "- Patterns: 'greatest hits', 'best of', 'compilation', 'anthology'\n"
                "- Strategy: MARK_AND_SKIP (preserve existing years)\n"
                "See: TestYearFallbackLogic and TestAlbumTypeDetection",
                "Implementation Note",
                allure.attachment_type.TEXT,
            )

    @allure.story("Demo Vault Albums")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Low-level: _track_needs_year_update returns True for Demo Vault")
    @allure.description(
        "Documents that _track_needs_year_update() doesn't detect Demo Vault albums. "
        "The 'Demo Vault' detection is implemented in _apply_year_fallback() via detect_album_type(). "
        "See TestYearFallbackLogic.test_fallback_blocks_demo_vault for the fix."
    )
    def test_demo_vault_year_conflict(self) -> None:
        """Test Demo Vault album year determination at low-level method.

        NOTE: This tests the low-level _track_needs_year_update() method.
        Demo Vault detection is now implemented in _apply_year_fallback() which:
        - Detects 'demo', 'vault', 'archive', 'rarities' patterns
        - Marks for verification and SKIPS the update

        See TestYearFallbackLogic.test_fallback_blocks_demo_vault for tests of the fix.
        """
        with allure.step("Analyze Demo Vault scenario"):
            original_year = "2003"  # When demos were recorded
            release_year = "2021"   # When Demo Vault was released

            # The _track_needs_year_update sees they're different and says "update"
            needs_update = YearRetriever._track_needs_year_update(original_year, release_year)

        with allure.step("Low-level method returns True (fix is in fallback layer)"):
            # This is expected - the low-level method just checks if years differ
            assert needs_update is True

            allure.attach(
                "NOTE: _track_needs_year_update() only checks if years differ.\n"
                "Demo Vault detection is in _apply_year_fallback() via detect_album_type():\n"
                "- Patterns: 'demo', 'vault', 'archive', 'rarities', 'unreleased'\n"
                "- Strategy: MARK_AND_SKIP (preserve existing years)\n"
                "See: TestYearFallbackLogic.test_fallback_blocks_demo_vault",
                "Implementation Note",
                allure.attachment_type.TEXT,
            )

    @allure.story("B-Sides Albums")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Documentation: B-Sides album detection now implemented")
    @allure.description(
        "Documents that B-Sides detection is now implemented in _apply_year_fallback(). "
        "See TestYearFallbackLogic.test_fallback_blocks_bsides_album for the fix."
    )
    def test_bsides_album_year_handling(self) -> None:
        """Document B-Sides album year handling fix.

        NOTE: B-Sides detection is now implemented in _apply_year_fallback() which:
        - Detects 'b-sides', 'b-side' patterns
        - Marks for verification and SKIPS the update

        See TestYearFallbackLogic.test_fallback_blocks_bsides_album for tests of the fix.
        """
        with allure.step("B-Sides detection implemented"):
            allure.attach(
                "B-Sides album detection is now implemented:\n"
                "- Pattern: 'b-sides', 'b-side' (SPECIAL_ALBUM_PATTERNS)\n"
                "- Strategy: MARK_AND_SKIP (preserve existing years)\n"
                "- Location: _apply_year_fallback() via detect_album_type()\n\n"
                "Real case: Blue Stahli - B-Sides and Other Things I Forgot\n"
                "- User had 2011 (recording/creation year)\n"
                "- API returns 2013 (official release year)\n"
                "- NEW BEHAVIOR: Detects 'B-Sides', marks for verification, preserves 2011\n\n"
                "See: TestYearFallbackLogic.test_fallback_blocks_bsides_album",
                "Implementation Note",
                allure.attachment_type.TEXT,
            )

    @allure.story("Reissue Detection")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Documentation: Reissue detection now implemented")
    @allure.description(
        "Documents that reissue detection is now implemented in _apply_year_fallback(). "
        "Reissues use MARK_AND_UPDATE strategy (update but mark for verification). "
        "See TestYearFallbackLogic.test_fallback_updates_reissue_with_marking for the fix."
    )
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
        with allure.step("Reissue detection implemented"):
            allure.attach(
                "Reissue album detection is now implemented:\n"
                "- Patterns: 'remastered', 'anniversary', 'deluxe', 'expanded', 'redux'\n"
                "- Strategy: MARK_AND_UPDATE (update but mark for verification)\n"
                "- Location: _apply_year_fallback() via detect_album_type()\n\n"
                "Real case: Darkseed - Astral Darkness Awaits\n"
                "- Original release: 2005\n"
                "- Reissue: 2012 (7 year difference)\n"
                "- NEW BEHAVIOR: Dramatic change (>5 years) blocks update, preserves 2005\n\n"
                "Additionally, if album name contains 'Remastered'/'Anniversary':\n"
                "- Detected as REISSUE type\n"
                "- Uses MARK_AND_UPDATE strategy\n\n"
                "See: TestYearFallbackLogic.test_fallback_updates_reissue_with_marking",
                "Implementation Note",
                allure.attachment_type.TEXT,
            )


@allure.epic("Music Genre Updater")
@allure.feature("Year Retrieval")
@allure.story("Track Needs Update Logic")
class TestTrackNeedsYearUpdate:
    """Tests for _track_needs_year_update edge cases."""

    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Track with empty year needs update")
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
        result = YearRetriever._track_needs_year_update(current_year, target_year)

        # For "0", the behavior is: str("0") != "2020" → True
        # But we should verify empty year check
        if current_year in (None, "", "   "):
            assert is_empty_year(current_year) is True

        assert result is expected

    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Track with same year doesn't need update")
    def test_same_year_no_update(self) -> None:
        """Test that matching years don't trigger update."""
        current_year = "2020"
        target_year = "2020"

        result = YearRetriever._track_needs_year_update(current_year, target_year)

        assert result is False

    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Track with different year triggers update (current behavior)")
    @pytest.mark.parametrize(
        ("current_year", "target_year", "year_diff"),
        [
            ("2019", "2020", 1),    # Small difference
            ("2015", "2020", 5),    # Medium difference
            ("2010", "2020", 10),   # Large difference
            ("1998", "2018", 20),   # Very large difference (Abney Park case)
        ],
    )
    def test_different_year_triggers_update(
        self, current_year: str, target_year: str, year_diff: int
    ) -> None:
        """Test that different years trigger update regardless of difference magnitude."""
        result = YearRetriever._track_needs_year_update(current_year, target_year)

        # Current behavior: ANY difference triggers update
        assert result is True

        allure.attach(
            f"Year difference: {year_diff} years\n"
            f"Result: update triggered\n"
            f"NOTE: No threshold check for suspicious large differences",
            "Test Data",
            allure.attachment_type.TEXT,
        )


@allure.epic("Music Genre Updater")
@allure.feature("Year Retrieval")
@allure.story("Dominant Year Logic")
class TestDominantYearEdgeCases:
    """Tests for _get_dominant_year edge cases."""

    @staticmethod
    def create_retriever() -> YearRetriever:
        """Create a basic YearRetriever for testing."""
        return TestYearRetrieverEdgeCases.create_year_retriever()

    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Dominant year requires >60% of ALL album tracks")
    def test_dominant_year_threshold(self) -> None:
        """Test that dominant year requires 60% majority of ALL tracks."""
        retriever = self.create_retriever()

        # 10 tracks total
        # 5 with year 2020 (50%), 3 with 2019, 2 with empty
        album_tracks = [
            DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="2020")
            for i in range(1, 6)
        ] + [
            DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="2019")
            for i in range(6, 9)
        ] + [
            DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="")
            for i in range(9, 11)
        ]

        dominant = retriever._get_dominant_year(album_tracks)

        # 5/10 = 50% < 60% threshold
        assert dominant is None, (
            "50% is below 60% threshold - no dominant year"
        )

    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("60%+ majority establishes dominant year")
    def test_dominant_year_success(self) -> None:
        """Test successful dominant year detection."""
        retriever = self.create_retriever()

        # 10 tracks, 7 with 2020 (70%)
        album_tracks = [
            DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="2020")
            for i in range(1, 8)
        ] + [
            DummyTrackData.create(track_id=str(i), name=f"Track {i}", year="2019")
            for i in range(8, 11)
        ]

        dominant = retriever._get_dominant_year(album_tracks)

        assert dominant == "2020", "70% majority should establish dominant year"


@allure.epic("Music Genre Updater")
@allure.feature("Year Retrieval Fallback")
class TestYearFallbackLogic:
    """Tests for the new year fallback system that prevents incorrect year updates."""

    @staticmethod
    def create_retriever_with_fallback(
        external_api: Any = None,
        pending_verification: Any = None,
        fallback_enabled: bool = True,
        year_difference_threshold: int = 5,
    ) -> YearRetriever:
        """Create YearRetriever with fallback configuration."""
        config = {
            "year_retrieval": {
                "api_timeout": 30,
                "processing": {"batch_size": 50},
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

    @allure.story("Fallback Configuration")
    @allure.severity(allure.severity_level.NORMAL)
    def test_fallback_config_loaded(self) -> None:
        """Test that fallback configuration is properly loaded from config."""
        retriever = self.create_retriever_with_fallback(
            year_difference_threshold=10,  # Non-default value to verify config loading
        )

        assert retriever.fallback_enabled is True  # Default value
        assert retriever.year_difference_threshold == 10  # Custom value

    @allure.story("Year Difference Detection")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        ("existing", "proposed", "threshold", "expected_dramatic"),
        [
            ("2018", "1998", 5, True),   # 20 years - dramatic
            ("2018", "2020", 5, False),  # 2 years - normal
            ("2005", "2012", 5, True),   # 7 years - dramatic
            ("2005", "2012", 10, False), # 7 years with 10 threshold - normal
            ("2020", "2015", 5, False),  # 5 years exactly = not dramatic (> threshold)
            ("2020", "2014", 5, True),   # 6 years - dramatic
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

        is_dramatic = retriever._is_year_change_dramatic(existing, proposed)

        assert is_dramatic == expected_dramatic, (
            f"Expected {existing}→{proposed} to be "
            f"{'dramatic' if expected_dramatic else 'normal'} with threshold={threshold}"
        )

    @allure.story("Existing Year Extraction")
    @allure.severity(allure.severity_level.NORMAL)
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

        existing_year = retriever._get_existing_year_from_tracks(tracks)
        assert existing_year == "2011"

    @allure.story("Existing Year Extraction")
    @allure.severity(allure.severity_level.NORMAL)
    def test_get_existing_year_empty_tracks(self) -> None:
        """Test that empty years are handled correctly."""
        retriever = self.create_retriever_with_fallback()

        tracks = [
            TrackDict(id="1", name="T1", artist="A", album="B", year=""),
            TrackDict(id="2", name="T2", artist="A", album="B", year=None),
        ]

        existing_year = retriever._get_existing_year_from_tracks(tracks)
        assert existing_year is None

    @allure.story("Fallback Decision")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("FIX: Dramatic year change blocked and marked for verification")
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

        with allure.step("Apply fallback with dramatic year change"):
            result = await retriever._apply_year_fallback(
                proposed_year="1998",
                album_tracks=tracks,
                is_definitive=False,
                artist="Abney Park",
                album="Scallywag",
            )

        with allure.step("Verify: Update skipped"):
            assert result is None, "Should return None to skip update"

        with allure.step("Verify: Album marked for verification"):
            assert len(mock_pending.marked_albums) == 1
            # marked_albums is list of tuples: (artist, album, reason, metadata)
            marked_artist, _album, reason, _metadata = mock_pending.marked_albums[0]
            assert marked_artist == "Abney Park"
            assert reason == "suspicious_year_change"

            allure.attach(
                f"Existing year: 2018\n"
                f"Proposed year: 1998\n"
                f"Difference: 20 years\n"
                f"Result: Update BLOCKED (None returned)\n"
                f"Album marked for verification: Yes\n"
                f"Reason: {reason}",
                "Fix Verification",
                allure.attachment_type.TEXT,
            )

    @allure.story("Fallback Decision")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("FIX: B-Sides album update blocked")
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
            TrackDict(id="1", name="T1", artist="Blue Stahli",
                      album="B-Sides and Other Things I Forgot", year="2011"),
            TrackDict(id="2", name="T2", artist="Blue Stahli",
                      album="B-Sides and Other Things I Forgot", year="2011"),
        ]

        with allure.step("Apply fallback for B-Sides album"):
            result = await retriever._apply_year_fallback(
                proposed_year="2013",
                album_tracks=tracks,
                is_definitive=False,  # Low confidence
                artist="Blue Stahli",
                album="B-Sides and Other Things I Forgot",
            )

        with allure.step("Verify: Update skipped due to special album type"):
            assert result is None, "Should return None to skip update"

        with allure.step("Verify: Album marked with special type"):
            assert len(mock_pending.marked_albums) == 1
            # marked_albums is list of tuples: (artist, album, reason, metadata)
            _artist, _album, reason, _metadata = mock_pending.marked_albums[0]
            assert "special_album" in reason

            allure.attach(
                f"Album type: B-Sides (special)\n"
                f"Existing year: 2011\n"
                f"Proposed year: 2013\n"
                f"Result: Update BLOCKED\n"
                f"Reason: {reason}",
                "Fix Verification",
                allure.attachment_type.TEXT,
            )

    @allure.story("Fallback Decision")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("FIX: Demo Vault album update blocked")
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
            TrackDict(id="1", name="T1", artist="Celldweller",
                      album="Demo Vault: Wasteland", year="2003"),
        ]

        result = await retriever._apply_year_fallback(
            proposed_year="2021",
            album_tracks=tracks,
            is_definitive=False,
            artist="Celldweller",
            album="Demo Vault: Wasteland",
        )

        assert result is None, "Demo Vault update should be blocked"
        assert len(mock_pending.marked_albums) == 1

    @allure.story("Fallback Decision")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("High confidence API result applied directly")
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

        result = await retriever._apply_year_fallback(
            proposed_year="1998",  # Dramatic change!
            album_tracks=tracks,
            is_definitive=True,  # But high confidence
            artist="Artist",
            album="Album",
        )

        # High confidence = apply even if dramatic
        assert result == "1998"
        assert len(mock_pending.marked_albums) == 0

    @allure.story("Fallback Decision")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Empty existing year allows update")
    @pytest.mark.asyncio
    async def test_fallback_allows_empty_year_update(self) -> None:
        """Test that tracks with empty years get updated."""
        retriever = self.create_retriever_with_fallback()

        tracks = [
            TrackDict(id="1", name="T1", artist="Artist", album="Album", year=""),
            TrackDict(id="2", name="T2", artist="Artist", album="Album", year=None),
        ]

        result = await retriever._apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            artist="Artist",
            album="Album",
        )

        # No existing year = nothing to preserve
        assert result == "2020"

    @allure.story("Fallback Decision")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Reissue albums get updated but marked")
    @pytest.mark.asyncio
    async def test_fallback_updates_reissue_with_marking(self) -> None:
        """Test that reissue albums are updated but marked for verification."""
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
        )

        tracks = [
            TrackDict(id="1", name="T1", artist="Artist",
                      album="Album (Remastered)", year="2000"),
        ]

        result = await retriever._apply_year_fallback(
            proposed_year="2020",
            album_tracks=tracks,
            is_definitive=False,
            artist="Artist",
            album="Album (Remastered)",
        )

        # Reissues use MARK_AND_UPDATE strategy
        assert result == "2020", "Reissue should be updated"
        assert len(mock_pending.marked_albums) == 1, "But should be marked"

    @allure.story("Fallback Disabled")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Fallback disabled = original behavior")
    @pytest.mark.asyncio
    async def test_fallback_disabled_original_behavior(self) -> None:
        """Test that disabling fallback restores original behavior."""
        mock_pending = MockPendingVerificationService()
        retriever = self.create_retriever_with_fallback(
            pending_verification=mock_pending,
            fallback_enabled=False,  # Disabled!
        )

        tracks = [
            TrackDict(id="1", name="T1", artist="Abney Park",
                      album="Scallywag", year="2018"),
        ]

        # With fallback disabled, dramatic changes are allowed
        result = await retriever._apply_year_fallback(
            proposed_year="1998",
            album_tracks=tracks,
            is_definitive=False,
            artist="Abney Park",
            album="Scallywag",
        )

        assert result == "1998", "Original behavior: apply API year"
        # Still marks for verification (original behavior for low confidence)
        assert len(mock_pending.marked_albums) == 1
