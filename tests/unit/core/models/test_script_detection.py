"""Tests for script detection utilities."""

from core.models.script_detection import (
    ScriptType,
    detect_primary_script,
    get_all_scripts,
    has_arabic,
    has_chinese,
    has_cyrillic,
    has_devanagari,
    has_greek,
    has_hebrew,
    has_japanese,
    has_korean,
    has_latin,
    has_thai,
    is_primarily_cyrillic,
    is_script_type,
)


class TestHasArabic:
    """Tests for Arabic script detection."""

    def test_arabic_text(self) -> None:
        """Test detection of Arabic text."""
        assert has_arabic("محمد عبده") is True

    def test_persian_text(self) -> None:
        """Test detection of Persian (uses Arabic script)."""
        assert has_arabic("سلام") is True

    def test_no_arabic(self) -> None:
        """Test non-Arabic text."""
        assert has_arabic("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_arabic("") is False

    def test_mixed_arabic_latin(self) -> None:
        """Test mixed Arabic and Latin."""
        assert has_arabic("محمد feat. John") is True


class TestHasChinese:
    """Tests for Chinese script detection."""

    def test_chinese_text(self) -> None:
        """Test detection of Chinese text."""
        assert has_chinese("周杰伦") is True

    def test_cjk_extension(self) -> None:
        """Test CJK Extension A characters."""
        assert has_chinese("㐀") is True  # U+3400

    def test_no_chinese(self) -> None:
        """Test non-Chinese text."""
        assert has_chinese("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_chinese("") is False


class TestHasCyrillic:
    """Tests for Cyrillic script detection."""

    def test_russian_text(self) -> None:
        """Test detection of Russian text."""
        assert has_cyrillic("Москва") is True

    def test_ukrainian_text(self) -> None:
        """Test detection of Ukrainian text."""
        assert has_cyrillic("діти інженерів") is True

    def test_serbian_text(self) -> None:
        """Test detection of Serbian Cyrillic."""
        assert has_cyrillic("Београд") is True

    def test_no_cyrillic(self) -> None:
        """Test non-Cyrillic text."""
        assert has_cyrillic("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_cyrillic("") is False

    def test_cyrillic_supplement(self) -> None:
        """Test Cyrillic Supplement range."""
        assert has_cyrillic("Ԁ") is True  # U+0500


class TestHasDevanagari:
    """Tests for Devanagari script detection."""

    def test_hindi_text(self) -> None:
        """Test detection of Hindi text."""
        assert has_devanagari("हिन्दी") is True

    def test_sanskrit_text(self) -> None:
        """Test detection of Sanskrit text."""
        assert has_devanagari("संगीत") is True

    def test_no_devanagari(self) -> None:
        """Test non-Devanagari text."""
        assert has_devanagari("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_devanagari("") is False


class TestHasGreek:
    """Tests for Greek script detection."""

    def test_greek_text(self) -> None:
        """Test detection of Greek text."""
        assert has_greek("Μουσική") is True

    def test_greek_alphabet(self) -> None:
        """Test Greek alphabet."""
        assert has_greek("αβγδ") is True

    def test_no_greek(self) -> None:
        """Test non-Greek text."""
        assert has_greek("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_greek("") is False


class TestHasHebrew:
    """Tests for Hebrew script detection."""

    def test_hebrew_text(self) -> None:
        """Test detection of Hebrew text."""
        assert has_hebrew("מוזיקה עברית") is True

    def test_hebrew_alphabet(self) -> None:
        """Test Hebrew alphabet."""
        assert has_hebrew("אבגד") is True

    def test_no_hebrew(self) -> None:
        """Test non-Hebrew text."""
        assert has_hebrew("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_hebrew("") is False


class TestHasJapanese:
    """Tests for Japanese script detection."""

    def test_kanji(self) -> None:
        """Test detection of Kanji."""
        assert has_japanese("音楽") is True

    def test_hiragana(self) -> None:
        """Test detection of Hiragana."""
        assert has_japanese("ひらがな") is True

    def test_katakana(self) -> None:
        """Test detection of Katakana."""
        assert has_japanese("カタカナ") is True

    def test_mixed_japanese(self) -> None:
        """Test mixed Japanese scripts."""
        assert has_japanese("音楽はすばらしい") is True

    def test_no_japanese(self) -> None:
        """Test non-Japanese text."""
        assert has_japanese("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_japanese("") is False


class TestHasKorean:
    """Tests for Korean script detection."""

    def test_hangul_syllables(self) -> None:
        """Test detection of Hangul syllables."""
        assert has_korean("한국 음악") is True

    def test_hangul_word(self) -> None:
        """Test Korean word."""
        assert has_korean("안녕하세요") is True

    def test_no_korean(self) -> None:
        """Test non-Korean text."""
        assert has_korean("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_korean("") is False


class TestHasLatin:
    """Tests for Latin script detection."""

    def test_english_text(self) -> None:
        """Test detection of English text."""
        assert has_latin("Pink Floyd") is True

    def test_accented_text(self) -> None:
        """Test detection of accented Latin."""
        assert has_latin("Café") is True

    def test_extended_latin(self) -> None:
        """Test Latin Extended characters."""
        assert has_latin("Łódź") is True  # Polish

    def test_numbers_only(self) -> None:
        """Test numbers only - not Latin."""
        assert has_latin("123") is False

    def test_punctuation_only(self) -> None:
        """Test punctuation only - not Latin."""
        assert has_latin("!!!") is False

    def test_cyrillic_not_latin(self) -> None:
        """Test Cyrillic is not detected as Latin."""
        assert has_latin("Москва") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_latin("") is False


class TestHasThai:
    """Tests for Thai script detection."""

    def test_thai_text(self) -> None:
        """Test detection of Thai text."""
        assert has_thai("เพลงไทย") is True

    def test_thai_greeting(self) -> None:
        """Test Thai greeting."""
        assert has_thai("สวัสดี") is True

    def test_no_thai(self) -> None:
        """Test non-Thai text."""
        assert has_thai("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert has_thai("") is False


class TestGetAllScripts:
    """Tests for get_all_scripts function."""

    def test_single_latin(self) -> None:
        """Test single Latin script detection."""
        scripts = get_all_scripts("Pink Floyd")
        assert scripts == [ScriptType.LATIN]

    def test_single_cyrillic(self) -> None:
        """Test single Cyrillic script detection."""
        scripts = get_all_scripts("Москва")
        assert scripts == [ScriptType.CYRILLIC]

    def test_mixed_cyrillic_latin(self) -> None:
        """Test mixed Cyrillic and Latin."""
        scripts = get_all_scripts("Москва feat. John")
        assert ScriptType.CYRILLIC in scripts
        assert ScriptType.LATIN in scripts

    def test_japanese_with_kanji(self) -> None:
        """Test Japanese detection (Kanji overlaps with Chinese)."""
        scripts = get_all_scripts("音楽")
        # Both Japanese and Chinese detect Kanji
        assert ScriptType.JAPANESE in scripts

    def test_mixed_japanese_latin(self) -> None:
        """Test mixed Japanese and Latin."""
        scripts = get_all_scripts("音楽 Music")
        assert ScriptType.JAPANESE in scripts
        assert ScriptType.LATIN in scripts

    def test_empty_string(self) -> None:
        """Test empty string returns empty list."""
        assert get_all_scripts("") == []

    def test_numbers_only(self) -> None:
        """Test numbers only returns empty list."""
        # Numbers are not alphabetic scripts
        scripts = get_all_scripts("12345")
        assert scripts == []


class TestDetectPrimaryScript:
    """Tests for detect_primary_script function."""

    def test_pure_latin(self) -> None:
        """Test pure Latin text."""
        assert detect_primary_script("Pink Floyd") == ScriptType.LATIN

    def test_pure_cyrillic(self) -> None:
        """Test pure Cyrillic text."""
        assert detect_primary_script("Москва") == ScriptType.CYRILLIC

    def test_pure_japanese_hiragana(self) -> None:
        """Test pure Japanese Hiragana."""
        assert detect_primary_script("ひらがな") == ScriptType.JAPANESE

    def test_pure_korean(self) -> None:
        """Test pure Korean text."""
        assert detect_primary_script("한국") == ScriptType.KOREAN

    def test_empty_string(self) -> None:
        """Test empty string returns UNKNOWN."""
        assert detect_primary_script("") == ScriptType.UNKNOWN

    def test_numbers_only(self) -> None:
        """Test numbers only returns UNKNOWN."""
        assert detect_primary_script("12345") == ScriptType.UNKNOWN

    def test_mixed_script_balanced(self) -> None:
        """Test balanced mixed script returns MIXED."""
        # Roughly equal Cyrillic and Latin
        result = detect_primary_script("Москва featuring John Smith")
        assert result == ScriptType.MIXED

    def test_predominantly_latin_with_minor_cyrillic(self) -> None:
        """Test predominantly Latin with minor other script."""
        # Mostly Latin with tiny bit of Cyrillic
        result = detect_primary_script("This is English text with Я")
        assert result == ScriptType.LATIN

    def test_predominantly_cyrillic_with_minor_latin(self) -> None:
        """Test predominantly Cyrillic with minor Latin."""
        # Mostly Cyrillic with tiny bit of Latin
        result = detect_primary_script("Это русский текст a")
        assert result == ScriptType.CYRILLIC

    def test_japanese_with_kanji_only(self) -> None:
        """Test Japanese Kanji only (shared with Chinese) defaults to Chinese."""
        # Pure Kanji without Hiragana/Katakana defaults to Chinese
        result = detect_primary_script("音楽")
        # This is an edge case - could be either
        assert result in (ScriptType.JAPANESE, ScriptType.CHINESE)

    def test_japanese_with_hiragana(self) -> None:
        """Test Japanese with Hiragana is detected as Japanese."""
        result = detect_primary_script("音楽はすばらしい")
        assert result == ScriptType.JAPANESE


class TestIsScriptType:
    """Tests for is_script_type function."""

    def test_is_cyrillic_true(self) -> None:
        """Test is_script_type for Cyrillic."""
        assert is_script_type("Москва", ScriptType.CYRILLIC) is True

    def test_is_latin_true(self) -> None:
        """Test is_script_type for Latin."""
        assert is_script_type("Pink Floyd", ScriptType.LATIN) is True

    def test_is_cyrillic_false(self) -> None:
        """Test is_script_type negative case."""
        assert is_script_type("Pink Floyd", ScriptType.CYRILLIC) is False

    def test_empty_string(self) -> None:
        """Test empty string returns False."""
        assert is_script_type("", ScriptType.LATIN) is False

    def test_invalid_script_type(self) -> None:
        """Test with script type not in detectors."""
        # MIXED and UNKNOWN are not in SCRIPT_DETECTORS
        assert is_script_type("text", ScriptType.MIXED) is False
        assert is_script_type("text", ScriptType.UNKNOWN) is False


class TestIsPrimarilyCyrillic:
    """Tests for is_primarily_cyrillic legacy function."""

    def test_pure_cyrillic(self) -> None:
        """Test pure Cyrillic text."""
        assert is_primarily_cyrillic("Москва") is True

    def test_mixed_with_cyrillic(self) -> None:
        """Test mixed text with Cyrillic."""
        assert is_primarily_cyrillic("Москва feat. John") is True

    def test_pure_latin(self) -> None:
        """Test pure Latin text."""
        assert is_primarily_cyrillic("Pink Floyd") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert is_primarily_cyrillic("") is False


class TestScriptTypeEnum:
    """Tests for ScriptType enum."""

    def test_enum_values(self) -> None:
        """Test enum has expected values."""
        assert ScriptType.LATIN.value == "latin"
        assert ScriptType.CYRILLIC.value == "cyrillic"
        assert ScriptType.JAPANESE.value == "japanese"
        assert ScriptType.MIXED.value == "mixed"
        assert ScriptType.UNKNOWN.value == "unknown"

    def test_enum_is_str(self) -> None:
        """Test ScriptType is str enum."""
        assert isinstance(ScriptType.LATIN, str)
        assert ScriptType.LATIN.value == "latin"


class TestEdgeCases:
    """Tests for edge cases."""

    def test_whitespace_only(self) -> None:
        """Test whitespace only returns UNKNOWN."""
        assert detect_primary_script("   ") == ScriptType.UNKNOWN

    def test_punctuation_only(self) -> None:
        """Test punctuation only returns UNKNOWN."""
        assert detect_primary_script("!@#$%") == ScriptType.UNKNOWN

    def test_emoji_only(self) -> None:
        """Test emoji only returns UNKNOWN."""
        assert detect_primary_script("🎵🎶") == ScriptType.UNKNOWN

    def test_mixed_with_numbers(self) -> None:
        """Test text with numbers."""
        assert detect_primary_script("Track 01") == ScriptType.LATIN

    def test_artist_name_with_special_chars(self) -> None:
        """Test artist name with special characters."""
        assert detect_primary_script("AC/DC") == ScriptType.LATIN

    def test_cyrillic_ukrainian_specific(self) -> None:
        """Test Ukrainian-specific Cyrillic characters."""
        assert has_cyrillic("ї") is True  # Ukrainian specific
        assert has_cyrillic("є") is True  # Ukrainian specific
        assert has_cyrillic("\u0456") is True  # Ukrainian i (U+0456)

    def test_arabic_numbers_not_arabic_script(self) -> None:
        """Test that Arabic numerals (0-9) are not detected as Arabic script."""
        # Arabic numerals are called "Arabic" but use Latin numeral glyphs
        assert has_arabic("0123456789") is False

    def test_greek_letters_in_math(self) -> None:
        """Test Greek letters commonly used in math."""
        assert has_greek("\u03b1") is True  # Greek alpha (U+03B1)
        assert has_greek("β") is True
        assert has_greek("π") is True
