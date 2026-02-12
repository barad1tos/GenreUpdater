"""Tests for album type detection module.

This module tests the pattern-based detection of special album types
(B-Sides, Demo Vault, Greatest Hits, etc.) used by the year fallback system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.models.album_type import (
    AlbumType,
    AlbumTypePatterns,
    YearHandlingStrategy,
    configure_patterns,
    detect_album_type,
    get_patterns,
    get_year_handling_strategy,
    is_special_album,
    reset_patterns,
)
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from collections.abc import Generator


class TestAlbumTypeDetection:
    """Tests for album type detection logic."""

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
        info = detect_album_type(album_name)
        assert info.album_type == AlbumType.SPECIAL
        assert info.detected_pattern is not None  # Some pattern detected
        assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP

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
        info = detect_album_type(album_name)
        assert info.album_type == AlbumType.COMPILATION
        assert info.detected_pattern is not None  # Some pattern detected
        assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP

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
    def test_reissue_album_detection(self, album_name: str, expected_pattern: str) -> None:
        """Test detection of reissue albums (MARK_AND_UPDATE strategy)."""
        info = detect_album_type(album_name)
        assert info.album_type == AlbumType.REISSUE
        assert info.detected_pattern == expected_pattern
        # Reissues use MARK_AND_UPDATE (still update, but mark for verification)
        assert info.strategy == YearHandlingStrategy.MARK_AND_UPDATE

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
        info = detect_album_type(album_name)
        assert info.album_type == AlbumType.NORMAL
        assert info.detected_pattern is None
        assert info.strategy == YearHandlingStrategy.NORMAL

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

    def test_get_year_handling_strategy_function(self) -> None:
        """Test get_year_handling_strategy convenience function."""
        # Special/Compilation albums use MARK_AND_SKIP strategy
        assert get_year_handling_strategy("B-Sides Collection") == YearHandlingStrategy.MARK_AND_SKIP
        assert get_year_handling_strategy("Greatest Hits") == YearHandlingStrategy.MARK_AND_SKIP

        # Reissue albums use MARK_AND_UPDATE strategy
        assert get_year_handling_strategy("Album (Remastered)") == YearHandlingStrategy.MARK_AND_UPDATE

        # Normal albums use NORMAL strategy
        assert get_year_handling_strategy("Normal Album") == YearHandlingStrategy.NORMAL


class TestRealWorldAlbumCases:
    """Test detection with real album names from user's library."""

    def test_blue_stahli_bsides(self) -> None:
        """Test detection for real case: Blue Stahli B-Sides album."""
        album = "B-Sides and Other Things I Forgot"
        info = detect_album_type(album)

        assert info.album_type == AlbumType.SPECIAL
        assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP

    def test_celldweller_demo_vault(self) -> None:
        """Test detection for real case: Celldweller Demo Vault."""
        album = "Demo Vault: Wasteland"
        info = detect_album_type(album)

        assert info.album_type == AlbumType.SPECIAL
        # Both "demo" and "vault" are valid patterns, frozenset order is not guaranteed
        assert info.detected_pattern in {"demo", "vault"}
        assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP

    def test_him_greatest_hits(self) -> None:
        """Test detection for real case: HIM Greatest Hits."""
        album = "And Love Said No - Greatest Hits 1997 - 2004"
        info = detect_album_type(album)

        # Note: "hits" is in COMPILATION_PATTERNS
        assert info.album_type == AlbumType.COMPILATION
        assert info.strategy == YearHandlingStrategy.MARK_AND_SKIP


class TestAlbumTypePatternConfiguration:
    """Tests for configurable album type patterns.

    These tests verify that album type patterns can be loaded from YAML config
    and properly override defaults.
    """

    @pytest.fixture(autouse=True)
    def reset_after_test(self) -> Generator[None]:
        """Reset patterns after each test to avoid state pollution."""
        yield
        reset_patterns()

    def test_from_defaults_returns_defaults(self) -> None:
        """Test that from_defaults() returns hardcoded default patterns."""
        patterns = AlbumTypePatterns.from_defaults()

        # Verify special patterns include key defaults
        assert "b-sides" in patterns.special
        assert "demo" in patterns.special
        assert "vault" in patterns.special
        assert "remix" in patterns.special

        # Verify compilation patterns
        assert "greatest hits" in patterns.compilation
        assert "best of" in patterns.compilation
        assert "хіти" in patterns.compilation  # Ukrainian

        # Verify reissue patterns
        assert "remaster" in patterns.reissue
        assert "anniversary" in patterns.reissue
        assert "deluxe" in patterns.reissue

    def test_from_config_loads_custom_patterns(self) -> None:
        """Test that from_config() loads patterns from YAML config."""
        config = create_test_app_config(
            album_type_detection={
                "special_patterns": ["custom-special", "my-bsides"],
                "compilation_patterns": ["my-hits", "my-collection"],
                "reissue_patterns": ["custom-remaster"],
            }
        )

        patterns = AlbumTypePatterns.from_config(config)

        # Custom patterns should be loaded
        assert "custom-special" in patterns.special
        assert "my-bsides" in patterns.special
        assert "my-hits" in patterns.compilation
        assert "custom-remaster" in patterns.reissue

        # Defaults should NOT be present (full override)
        assert "b-sides" not in patterns.special
        assert "greatest hits" not in patterns.compilation

    def test_from_config_falls_back_to_defaults(self) -> None:
        """Test that missing config sections fall back to defaults."""
        # Default config - all defaults (no custom album_type_detection)
        patterns = AlbumTypePatterns.from_config(create_test_app_config())
        assert "b-sides" in patterns.special
        assert "greatest hits" in patterns.compilation
        assert "remaster" in patterns.reissue

        # Partial config - only special_patterns overridden
        partial_config = create_test_app_config(
            album_type_detection={
                "special_patterns": ["only-special"],
                # compilation_patterns and reissue_patterns missing -> None -> defaults
            }
        )
        patterns = AlbumTypePatterns.from_config(partial_config)
        assert "only-special" in patterns.special
        assert "b-sides" not in patterns.special  # Overridden
        assert "greatest hits" in patterns.compilation  # Default
        assert "remaster" in patterns.reissue  # Default

    def test_configure_patterns_sets_singleton(self) -> None:
        """Test that configure_patterns() configures the module singleton."""
        config = create_test_app_config(
            album_type_detection={
                "special_patterns": ["configured-pattern"],
                "compilation_patterns": ["configured-compilation"],
                "reissue_patterns": ["configured-reissue"],
            }
        )

        # Before configuration - defaults
        reset_patterns()
        patterns_before = get_patterns()
        assert "b-sides" in patterns_before.special

        # Configure
        configure_patterns(config)

        # After configuration - custom patterns
        patterns_after = get_patterns()
        assert "configured-pattern" in patterns_after.special
        assert "b-sides" not in patterns_after.special

    def test_reset_patterns_clears_config(self) -> None:
        """Test that reset_patterns() clears the singleton and returns to defaults."""
        config = create_test_app_config(
            album_type_detection={
                "special_patterns": ["test-pattern"],
            }
        )

        configure_patterns(config)
        assert "test-pattern" in get_patterns().special

        reset_patterns()

        # Back to defaults
        patterns = get_patterns()
        assert "test-pattern" not in patterns.special
        assert "b-sides" in patterns.special

    def test_detect_uses_configured_patterns(self) -> None:
        """Test that detection uses configured patterns instead of defaults."""
        # Configure custom patterns
        config = create_test_app_config(
            album_type_detection={
                "special_patterns": ["my-custom-special"],
                "compilation_patterns": ["my-custom-compilation"],
                "reissue_patterns": ["my-custom-reissue"],
            }
        )
        configure_patterns(config)

        # Default pattern should NOT be detected
        info_default = detect_album_type("B-Sides Album")
        assert info_default.album_type == AlbumType.NORMAL
        assert info_default.detected_pattern is None

        # Custom pattern SHOULD be detected
        info_custom = detect_album_type("My-Custom-Special Album")
        assert info_custom.album_type == AlbumType.SPECIAL
        assert info_custom.detected_pattern == "my-custom-special"

    def test_pattern_normalization(self) -> None:
        """Test that patterns with hyphens match text with spaces and vice versa."""
        # Reset to defaults (which include hyphenated patterns like "b-sides")
        reset_patterns()

        # "b-sides" pattern should match "B Sides" (hyphen -> space)
        info = detect_album_type("My B Sides Collection")
        assert info.album_type == AlbumType.SPECIAL
        assert info.detected_pattern == "b-sides"

        # Also match exact hyphenated form
        info2 = detect_album_type("My B-Sides Collection")
        assert info2.album_type == AlbumType.SPECIAL
        assert info2.detected_pattern == "b-sides"

    def test_ukrainian_patterns(self) -> None:
        """Test detection with Ukrainian patterns (хіти, хіт)."""
        reset_patterns()

        # "хіти" should be detected as compilation
        info = detect_album_type("Найкращі Хіти")
        assert info.album_type == AlbumType.COMPILATION
        assert info.detected_pattern == "хіти"

        # "хіт" should also match
        info2 = detect_album_type("Хіт Сезону")
        assert info2.album_type == AlbumType.COMPILATION
        assert info2.detected_pattern == "хіт"

    def test_new_patterns_from_issue_381(self) -> None:
        """Test patterns added based on 381 year update cases analysis.

        Covers:
        - D-Sides (Gorillaz case)
        - Remixes (Depeche Mode case)
        - Remanufacture (Fear Factory case)
        - Rerelease (generic)
        """
        reset_patterns()

        # Test D-Sides pattern
        info = detect_album_type("D-Sides")
        assert info.album_type == AlbumType.SPECIAL
        assert info.detected_pattern == "d-sides"

        # Test Remixes pattern
        info = detect_album_type("Remixes 2: 81-11")
        assert info.album_type == AlbumType.SPECIAL
        assert info.detected_pattern == "remixes"

        # Test Remanufacture pattern
        info = detect_album_type("Remanufacture")
        assert info.album_type == AlbumType.REISSUE
        assert info.detected_pattern == "remanufacture"

        # Test Rerelease pattern
        info = detect_album_type("Album (Rerelease)")
        assert info.album_type == AlbumType.REISSUE
        assert info.detected_pattern == "rerelease"


class TestAlbumTypeDetectionEdgeCases:
    """Edge case tests for album type detection."""

    @pytest.fixture(autouse=True)
    def reset_after_test(self) -> Generator[None]:
        """Reset patterns after each test."""
        yield
        reset_patterns()

    def test_disclaimer_ii_is_normal(self) -> None:
        """Test that album suffixes like 'II' don't trigger reissue detection."""
        info = detect_album_type("Disclaimer II")
        assert info.album_type == AlbumType.NORMAL
        assert info.detected_pattern is None

    def test_case_insensitive_matching(self) -> None:
        """Test that pattern matching is case-insensitive."""
        # Uppercase
        info = detect_album_type("GREATEST HITS")
        assert info.album_type == AlbumType.COMPILATION

        # Mixed case
        info = detect_album_type("Greatest HITS")
        assert info.album_type == AlbumType.COMPILATION

        # Lowercase
        info = detect_album_type("greatest hits")
        assert info.album_type == AlbumType.COMPILATION

    def test_patterns_in_parentheses(self) -> None:
        """Test that patterns inside parentheses are detected."""
        info = detect_album_type("Album (Remastered)")
        assert info.album_type == AlbumType.REISSUE
        assert info.detected_pattern == "remastered"

        info = detect_album_type("Album [Deluxe Edition]")
        assert info.album_type == AlbumType.REISSUE
        assert info.detected_pattern == "deluxe"

    def test_word_boundary_matching(self) -> None:
        """Test that patterns only match at word boundaries."""
        # "demo" should match "Demo" but not "Demonstration"
        info = detect_album_type("Demo Album")
        assert info.album_type == AlbumType.SPECIAL
        assert info.detected_pattern == "demo"

        # "demonstrations" should NOT match "demo"
        info = detect_album_type("Demonstrations Album")
        assert info.album_type == AlbumType.NORMAL
        assert info.detected_pattern is None
