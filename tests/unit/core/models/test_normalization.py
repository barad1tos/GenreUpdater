"""Tests for unified text normalization utilities."""

from __future__ import annotations


from core.models.normalization import are_names_equal, normalize_for_matching


class TestNormalizeForMatching:
    """Tests for normalize_for_matching function."""

    def test_strips_whitespace(self) -> None:
        """Test that leading and trailing whitespace is removed."""
        assert normalize_for_matching("  Vildhjarta  ") == "vildhjarta"
        assert normalize_for_matching("\t\nMetallica\n\t") == "metallica"

    def test_lowercases_text(self) -> None:
        """Test that text is lowercased."""
        assert normalize_for_matching("AC/DC") == "ac/dc"
        assert normalize_for_matching("2CELLOS") == "2cellos"
        assert normalize_for_matching("TANOK NA MAIDANI KONGO") == "tanok na maidani kongo"

    def test_preserves_special_characters(self) -> None:
        """Test that special characters are preserved."""
        assert normalize_for_matching("AC/DC") == "ac/dc"
        assert normalize_for_matching("Guns N' Roses") == "guns n' roses"
        assert normalize_for_matching("P!nk") == "p!nk"

    def test_handles_empty_string(self) -> None:
        """Test that empty strings are handled correctly."""
        assert normalize_for_matching("") == ""
        assert normalize_for_matching("   ") == ""

    def test_handles_unicode(self) -> None:
        """Test that Unicode characters are preserved."""
        # Using Japanese which doesn't trigger RUF001 ambiguous character warnings
        assert normalize_for_matching("東京事変") == "東京事変"
        assert normalize_for_matching("  Björk  ") == "björk"

    def test_handles_numbers(self) -> None:
        """Test that numbers are preserved."""
        assert normalize_for_matching("21 Savage") == "21 savage"
        assert normalize_for_matching("Blink-182") == "blink-182"


class TestAreNamesEqual:
    """Tests for are_names_equal function."""

    def test_equal_after_normalization(self) -> None:
        """Test that normalized equal names return True."""
        assert are_names_equal("Metallica", "metallica") is True
        assert are_names_equal("  AC/DC  ", "ac/dc") is True
        assert are_names_equal("THE BEATLES", "the beatles") is True

    def test_different_names(self) -> None:
        """Test that different names return False."""
        assert are_names_equal("Metallica", "Iron Maiden") is False
        assert are_names_equal("AC/DC", "ACDC") is False

    def test_empty_strings(self) -> None:
        """Test that empty strings are handled correctly."""
        assert are_names_equal("", "") is True
        assert are_names_equal("   ", "") is True
        assert are_names_equal("", "Metallica") is False

    def test_unicode_equality(self) -> None:
        """Test that Unicode names are compared correctly."""
        # Japanese doesn't trigger RUF001 ambiguous character warnings
        assert are_names_equal("東京事変", "東京事変") is True
        assert are_names_equal("BJÖRK", "björk") is True
