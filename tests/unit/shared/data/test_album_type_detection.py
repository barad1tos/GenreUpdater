"""Tests for album type detection module.

This module tests the pattern-based detection of special album types
(B-Sides, Demo Vault, Greatest Hits, etc.) used by the year fallback system.
"""

from __future__ import annotations

import allure
import pytest

from src.shared.data.album_type_detection import (
    AlbumType,
    YearHandlingStrategy,
    detect_album_type,
    get_year_handling_strategy,
    is_special_album,
)


@allure.epic("Music Genre Updater")
@allure.feature("Album Type Detection")
class TestAlbumTypeDetection:
    """Tests for album type detection logic."""

    @allure.story("Special Album Detection")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        "album_name",
        [
            # B-Sides patterns
            "Blue Stahli B-Sides",
            "B-Sides and Other Things I Forgot",
            # Demo patterns
            "Demo Vault: Wasteland",
            "Celldweller Demos",
            "Studio Demo Only",  # Use "Only" to avoid multi-pattern issues
            # Archive/Rarities patterns
            "Rare Tracks Archive",
            "Unreleased Material",
            "Outtakes Only",  # Use "Only" to ensure single pattern match
            "Lost Sessions 2005",
        ],
    )
    def test_special_album_detection(self, album_name: str) -> None:
        """Test detection of special albums (B-Sides, Demo, Vault, etc.)."""
        with allure.step(f"Detect type for: {album_name}"):
            info = detect_album_type(album_name)

        with allure.step("Verify detection"):
            assert info.album_type == AlbumType.SPECIAL
            assert info.detected_pattern is not None  # Some pattern detected
            assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP

            allure.attach(
                f"Album: {album_name}\n"
                f"Type: {info.album_type.value}\n"
                f"Pattern: {info.detected_pattern}\n"
                f"Strategy: {info.strategy.value}",
                "Detection Result",
                allure.attachment_type.TEXT,
            )

    @allure.story("Compilation Album Detection")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        "album_name",
        [
            "Greatest Hits",
            "Best of Artist",
            "The Collection",
            "Complete Anthology",
            "Gold: The Compilation",
            "The Essential Album",
            "Ultimate Collection",
        ],
    )
    def test_compilation_album_detection(self, album_name: str) -> None:
        """Test detection of compilation albums."""
        with allure.step(f"Detect type for: {album_name}"):
            info = detect_album_type(album_name)

        with allure.step("Verify compilation detection"):
            assert info.album_type == AlbumType.COMPILATION
            assert info.detected_pattern is not None  # Some pattern detected
            assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP

    @allure.story("Reissue Album Detection")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.parametrize(
        ("album_name", "expected_pattern"),
        [
            ("Album (Remastered)", "remastered"),
            ("Album - 20th Anniversary Edition", "anniversary"),
            ("Deluxe Edition", "deluxe"),
            ("Expanded Edition", "expanded"),
            ("Album Redux", "redux"),
            ("Album (Re-Issue)", "re-issue"),
        ],
    )
    def test_reissue_album_detection(
        self, album_name: str, expected_pattern: str
    ) -> None:
        """Test detection of reissue albums (MARK_AND_UPDATE strategy)."""
        with allure.step(f"Detect type for: {album_name}"):
            info = detect_album_type(album_name)

        with allure.step("Verify reissue detection"):
            assert info.album_type == AlbumType.REISSUE
            assert info.detected_pattern == expected_pattern
            # Reissues use MARK_AND_UPDATE (still update, but mark for verification)
            assert info.strategy == YearHandlingStrategy.MARK_AND_UPDATE

    @allure.story("Normal Album Detection")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.parametrize(
        "album_name",
        [
            "Normal Album",
            "The Dark Side of the Moon",
            "Abbey Road",
            "Nevermind",
            "Black Album",
            "Discovery",
            "Random Access Memories",
            "",  # Empty album name
        ],
    )
    def test_normal_album_detection(self, album_name: str) -> None:
        """Test that normal albums are correctly identified."""
        with allure.step(f"Detect type for: '{album_name}'"):
            info = detect_album_type(album_name)

        with allure.step("Verify normal album"):
            assert info.album_type == AlbumType.NORMAL
            assert info.detected_pattern is None
            assert info.strategy == YearHandlingStrategy.NORMAL

    @allure.story("Convenience Functions")
    @allure.severity(allure.severity_level.NORMAL)
    def test_is_special_album_function(self) -> None:
        """Test is_special_album convenience function."""
        # Special album
        is_special, pattern = is_special_album("Blue Stahli B-Sides")
        assert is_special is True
        assert pattern is not None  # Some pattern detected (b-sides)
        assert "b" in pattern.lower()  # Should contain 'b' from b-sides or b-side

        # Normal album
        is_special, pattern = is_special_album("Normal Album")
        assert is_special is False
        assert pattern is None

        # Reissue counts as "special"
        is_special, pattern = is_special_album("Album (Remastered)")
        assert is_special is True
        assert pattern == "remastered"

    @allure.story("Convenience Functions")
    @allure.severity(allure.severity_level.NORMAL)
    def test_get_year_handling_strategy_function(self) -> None:
        """Test get_year_handling_strategy convenience function."""
        # Special/Compilation albums use MARK_AND_SKIP strategy
        assert get_year_handling_strategy("B-Sides Collection") == YearHandlingStrategy.MARK_AND_SKIP
        assert get_year_handling_strategy("Greatest Hits") == YearHandlingStrategy.MARK_AND_SKIP

        # Reissue albums use MARK_AND_UPDATE strategy
        assert get_year_handling_strategy("Album (Remastered)") == YearHandlingStrategy.MARK_AND_UPDATE

        # Normal albums use NORMAL strategy
        assert get_year_handling_strategy("Normal Album") == YearHandlingStrategy.NORMAL


@allure.epic("Music Genre Updater")
@allure.feature("Album Type Detection")
@allure.story("Real World Cases")
class TestRealWorldAlbumCases:
    """Test detection with real album names from user's library."""

    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Blue Stahli - B-Sides and Other Things I Forgot")
    def test_blue_stahli_bsides(self) -> None:
        """Test detection for real case: Blue Stahli B-Sides album."""
        album = "B-Sides and Other Things I Forgot"
        info = detect_album_type(album)

        assert info.album_type == AlbumType.SPECIAL
        assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP
        allure.attach(
            "This album would have its year preserved (2011) instead of "
            "being updated to API result (2013).",
            "Fix Verification",
            allure.attachment_type.TEXT,
        )

    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Celldweller - Demo Vault: Wasteland")
    def test_celldweller_demo_vault(self) -> None:
        """Test detection for real case: Celldweller Demo Vault."""
        album = "Demo Vault: Wasteland"
        info = detect_album_type(album)

        assert info.album_type == AlbumType.SPECIAL
        # Both "demo" and "vault" are valid patterns, frozenset order is not guaranteed
        assert info.detected_pattern in {"demo", "vault"}
        assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP

    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("HIM - And Love Said No - Greatest Hits")
    def test_him_greatest_hits(self) -> None:
        """Test detection for real case: HIM Greatest Hits."""
        album = "And Love Said No - Greatest Hits 1997 - 2004"
        info = detect_album_type(album)

        # Note: "hits" is in COMPILATION_PATTERNS
        assert info.album_type == AlbumType.COMPILATION
        assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP
