"""Script detection utilities for text analysis."""

from __future__ import annotations

from enum import Enum

# Constants for script detection thresholds
MINIMUM_SCRIPT_RATIO = 0.25  # Minimum ratio for script to be considered significant
MINIMUM_SCRIPT_COUNT = 2  # Minimum number of scripts for mixed case detection


class ScriptType(str, Enum):
    """Script type enumeration for text analysis."""

    ARABIC = "arabic"
    CHINESE = "chinese"
    CYRILLIC = "cyrillic"
    DEVANAGARI = "devanagari"
    GREEK = "greek"
    HEBREW = "hebrew"
    JAPANESE = "japanese"
    KOREAN = "korean"
    LATIN = "latin"
    THAI = "thai"
    MIXED = "mixed"
    UNKNOWN = "unknown"


def has_arabic(text: str) -> bool:
    """Check if the text contains Arabic characters.

    Covers:
    - Arabic (U+0600-U+06FF) - Arabic, Persian, Urdu
    - Arabic Supplement (U+0750-U+077F) - Additional Arabic letters

    Args:
        text: Text to analyze

    Returns:
        True if Arabic characters are found, False otherwise

    Examples:
        >>> has_arabic("محمد عبده")
        True
        >>> has_arabic("Pink Floyd")
        False

    """
    if not text:
        return False
    return any(
        "\u0600" <= c <= "\u06ff"  # Arabic
        or "\u0750" <= c <= "\u077f"  # Arabic Supplement
        for c in text
    )


def has_chinese(text: str) -> bool:
    """Check if the text contains Chinese characters.

    Covers:
    - CJK Unified Ideographs (U+4E00-U+9FFF) - Han characters
    - CJK Extension A (U+3400-U+4DBF) - Additional Han characters

    Args:
        text: Text to analyze

    Returns:
        True if Chinese characters are found, False otherwise

    Examples:
        >>> has_chinese("周杰伦")
        True
        >>> has_chinese("Pink Floyd")
        False

    """
    if not text:
        return False
    return any(
        "\u4e00" <= c <= "\u9fff"  # CJK Unified Ideographs
        or "\u3400" <= c <= "\u4dbf"  # CJK Extension A
        for c in text
    )


def has_cyrillic(text: str) -> bool:
    """Check if the text contains Cyrillic characters.

    Covers:
    - Basic Cyrillic (U+0400-U+04FF) - Russian, Ukrainian, Serbian, Bulgarian, etc.
    - Cyrillic Supplement (U+0500-U+052F) - Additional characters
    - Cyrillic Extended-A (U+2DE0-U+2DFF) - Historic letters
    - Cyrillic Extended-B (U+A640-U+A69F) - Additional historic

    Args:
        text: Text to analyze

    Returns:
        True if Cyrillic characters are found, False otherwise

    Examples:
        >>> has_cyrillic("МУР")
        True
        >>> has_cyrillic("Pink Floyd")
        False
        >>> has_cyrillic("діти інженерів")
        True

    """
    if not text:
        return False
    return any(
        "\u0400" <= c <= "\u04ff"  # Basic Cyrillic
        or "\u0500" <= c <= "\u052f"  # Cyrillic Supplement
        or "\u2de0" <= c <= "\u2dff"  # Cyrillic Extended-A
        or "\ua640" <= c <= "\ua69f"  # Cyrillic Extended-B
        for c in text
    )


def has_devanagari(text: str) -> bool:
    """Check if the text contains Devanagari characters.

    Covers:
    - Devanagari (U+0900-U+097F) - Hindi, Marathi, Sanskrit, Nepali

    Args:
        text: Text to analyze

    Returns:
        True if Devanagari characters are found, False otherwise

    Examples:
        >>> has_devanagari("हिन्दी संगीत")
        True
        >>> has_devanagari("Pink Floyd")
        False

    """
    return any("\u0900" <= c <= "\u097f" for c in text) if text else False


def has_greek(text: str) -> bool:
    """Check if the text contains Greek characters.

    Covers:
    - Greek and Coptic (U+0370-U+03FF) - Modern and ancient Greek

    Args:
        text: Text to analyze

    Returns:
        True if Greek characters are found, False otherwise

    Examples:
        >>> has_greek("Μουσική")
        True
        >>> has_greek("Pink Floyd")
        False

    """
    return any("\u0370" <= c <= "\u03ff" for c in text) if text else False


def has_hebrew(text: str) -> bool:
    """Check if the text contains Hebrew characters.

    Covers:
    - Hebrew (U+0590-U+05FF) - Hebrew alphabet

    Args:
        text: Text to analyze

    Returns:
        True if Hebrew characters are found, False otherwise

    Examples:
        >>> has_hebrew("מוזיקה עברית")
        True
        >>> has_hebrew("Pink Floyd")
        False

    """
    return any("\u0590" <= c <= "\u05ff" for c in text) if text else False


def has_japanese(text: str) -> bool:
    """Check if the text contains Japanese characters.

    Covers:
    - Hiragana (U+3040-U+309F) - Japanese syllabary
    - Katakana (U+30A0-U+30FF) - Japanese syllabary
    - CJK Unified Ideographs (U+4E00-U+9FFF) - Kanji (shared with Chinese)

    Args:
        text: Text to analyze

    Returns:
        True if Japanese characters are found, False otherwise

    Examples:
        >>> has_japanese("音楽")
        True
        >>> has_japanese("ひらがな")
        True
        >>> has_japanese("カタカナ")
        True
        >>> has_japanese("Pink Floyd")
        False

    """
    if not text:
        return False
    return any(
        "\u3040" <= c <= "\u309f"  # Hiragana
        or "\u30a0" <= c <= "\u30ff"  # Katakana
        or "\u4e00" <= c <= "\u9fff"  # CJK Unified Ideographs (Kanji)
        for c in text
    )


def has_korean(text: str) -> bool:
    """Check if the text contains Korean characters.

    Covers:
    - Hangul Syllables (U+AC00-U+D7AF) - Korean alphabet
    - Hangul Jamo (U+1100-U+11FF) - Korean alphabet components

    Args:
        text: Text to analyze

    Returns:
        True if Korean characters are found, False otherwise

    Examples:
        >>> has_korean("한국 음악")
        True
        >>> has_korean("Pink Floyd")
        False

    """
    if not text:
        return False
    return any(
        "\uac00" <= c <= "\ud7af"  # Hangul Syllables
        or "\u1100" <= c <= "\u11ff"  # Hangul Jamo
        for c in text
    )


def has_latin(text: str) -> bool:
    """Check if the text contains Latin characters.

    Covers:
    - Basic Latin (U+0000-U+007F) - ASCII
    - Latin-1 Supplement (U+0080-U+00FF) - Accented letters
    - Latin Extended-A (U+0100-U+017F) - Eastern European
    - Latin Extended-B (U+0180-U+024F) - African languages, phonetic

    Args:
        text: Text to analyze

    Returns:
        True if Latin characters are found, False otherwise

    Examples:
        >>> has_latin("Pink Floyd")
        True
        >>> has_latin("Café")
        True
        >>> has_latin("МУР")  # noqa: RUF002
        False

    """
    if not text:
        return False
    return any(
        "\u0000" <= c <= "\u007f"  # Basic Latin (ASCII)
        or "\u0080" <= c <= "\u00ff"  # Latin-1 Supplement
        or "\u0100" <= c <= "\u017f"  # Latin Extended-A
        or "\u0180" <= c <= "\u024f"  # Latin Extended-B
        for c in text
    )


def has_thai(text: str) -> bool:
    """Check if the text contains Thai characters.

    Covers:
    - Thai (U+0E00-U+0E7F) - Thai alphabet

    Args:
        text: Text to analyze

    Returns:
        True if Thai characters are found, False otherwise

    Examples:
        >>> has_thai("เพลงไทย")
        True
        >>> has_thai("Pink Floyd")
        False

    """
    return any("\u0e00" <= c <= "\u0e7f" for c in text) if text else False


# Script detection function mapping
SCRIPT_DETECTORS = {
    ScriptType.ARABIC: has_arabic,
    ScriptType.CHINESE: has_chinese,
    ScriptType.CYRILLIC: has_cyrillic,
    ScriptType.DEVANAGARI: has_devanagari,
    ScriptType.GREEK: has_greek,
    ScriptType.HEBREW: has_hebrew,
    ScriptType.JAPANESE: has_japanese,
    ScriptType.KOREAN: has_korean,
    ScriptType.LATIN: has_latin,
    ScriptType.THAI: has_thai,
}


def get_all_scripts(text: str) -> list[ScriptType]:
    """Get all scripts detected in text.

    Args:
        text: Text to analyze

    Returns:
        List of detected script types

    Examples:
        >>> get_all_scripts("МУР feat. John")  # noqa: RUF002
        [ScriptType.CYRILLIC, ScriptType.LATIN]
        >>> get_all_scripts("Pink Floyd")
        [ScriptType.LATIN]
        >>> get_all_scripts("音楽 Music")
        [ScriptType.JAPANESE, ScriptType.LATIN]

    """
    if not text:
        return []

    return [
        script_type
        for script_type, detector in SCRIPT_DETECTORS.items()
        if detector(text)
    ]


def _handle_cjk_detection(text: str) -> ScriptType | None:
    """Handle special case for CJK script detection.

    Args:
        text: Text to analyze

    Returns:
        ScriptType if CJK case applies, None otherwise

    """
    if not (has_japanese(text) and has_chinese(text)):
        return None

    # Check for Hiragana or Katakana which are unique to Japanese
    has_hiragana_katakana = any(
        "\u3040" <= char <= "\u309f" or "\u30a0" <= char <= "\u30ff"
        for char in text
    )
    # If only Kanji (shared), default to Chinese as it's more common
    return ScriptType.JAPANESE if has_hiragana_katakana else ScriptType.CHINESE


def _count_script_characters(text: str) -> tuple[dict[ScriptType, int], int]:
    """Count characters by script type, excluding punctuation and spaces.

    Args:
        text: Text to analyze

    Returns:
        Tuple of (script_counts, total_chars)

    """
    script_counts: dict[ScriptType, int] = {}
    total_chars = 0

    for char in text:
        # Skip spaces, punctuation, and common symbols
        if char.isspace() or not char.isalpha():
            continue

        total_chars += 1
        for script_type, detector in SCRIPT_DETECTORS.items():
            if detector(char):
                script_counts[script_type] = script_counts.get(script_type, 0) + 1

    return script_counts, total_chars


def _handle_latin_mixed_case(script_counts: dict[ScriptType, int], total_chars: int) -> ScriptType | None:
    """Handle Latin + one other script case based on proportions.

    Args:
        script_counts: Character counts by script type
        total_chars: Total alphabetic characters

    Returns:
        ScriptType if Latin mixed case applies, None otherwise

    """
    if len(script_counts) != MINIMUM_SCRIPT_COUNT or ScriptType.LATIN not in script_counts:
        return None

    non_latin = next(s for s in script_counts if s != ScriptType.LATIN)
    latin_ratio = script_counts[ScriptType.LATIN] / total_chars
    non_latin_ratio = script_counts[non_latin] / total_chars

    # If Latin is less than the minimum ratio, use the non-Latin script
    if latin_ratio < MINIMUM_SCRIPT_RATIO:
        return non_latin
    # If non-Latin is less than the minimum ratio, it's primarily Latin
    if non_latin_ratio < MINIMUM_SCRIPT_RATIO:
        return ScriptType.LATIN
    # If both scripts have a significant presence, it's mixed
    return ScriptType.MIXED


def detect_primary_script(text: str) -> ScriptType:
    """Detect the primary script used in text.

    Returns the most dominant script type found in the text.
    Uses character counting to determine dominance when multiple scripts are present.

    Args:
        text: Text to analyze

    Returns:
        Primary script type

    Examples:
        >>> detect_primary_script("МУР")  # noqa: RUF002
        ScriptType.CYRILLIC
        >>> detect_primary_script("Pink Floyd")
        ScriptType.LATIN
        >>> detect_primary_script("МУР featuring John")  # noqa: RUF002
        ScriptType.MIXED
        >>> detect_primary_script("音楽")
        ScriptType.JAPANESE

    """
    if not text:
        return ScriptType.UNKNOWN

    # Special handling for CJK scripts
    cjk_result = _handle_cjk_detection(text)
    if cjk_result is not None:
        return cjk_result

    # Count characters by script type
    script_counts, total_chars = _count_script_characters(text)

    if total_chars == 0 or not script_counts:
        return ScriptType.UNKNOWN

    # Special case: Latin + one other script
    latin_mixed_result = _handle_latin_mixed_case(script_counts, total_chars)
    if latin_mixed_result is not None:
        return latin_mixed_result

    # Find the script with the most characters
    max_count = max(script_counts.values())
    dominant_scripts = [script for script, count in script_counts.items() if count == max_count]

    return dominant_scripts[0] if len(dominant_scripts) == 1 else ScriptType.MIXED


def is_script_type(text: str, script_type: ScriptType) -> bool:
    """Check if text contains a specific script type.

    Args:
        text: Text to analyze
        script_type: Script type to check for

    Returns:
        True if the script type is detected in the text

    Examples:
        >>> is_script_type("МУР", ScriptType.CYRILLIC)  # noqa: RUF002
        True
        >>> is_script_type("Pink Floyd", ScriptType.LATIN)
        True
        >>> is_script_type("音楽", ScriptType.JAPANESE)
        True

    """
    if not text or script_type not in SCRIPT_DETECTORS:
        return False

    return SCRIPT_DETECTORS[script_type](text)


# Legacy compatibility functions
def is_primarily_cyrillic(text: str) -> bool:
    """Check if text is primarily in Cyrillic script.

    Legacy compatibility function for existing API prioritization logic.

    Args:
        text: Text to analyze

    Returns:
        True if text is primarily Cyrillic (pure or mixed with Cyrillic dominance)

    Examples:
        >>> is_primarily_cyrillic("МУР")  # noqa: RUF002
        True
        >>> is_primarily_cyrillic("МУР feat. John")  # noqa: RUF002
        True
        >>> is_primarily_cyrillic("Pink Floyd")
        False

    """
    script = detect_primary_script(text)
    return script in (ScriptType.CYRILLIC, ScriptType.MIXED) and has_cyrillic(text)
