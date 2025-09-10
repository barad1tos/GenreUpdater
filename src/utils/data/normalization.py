"""Text normalization utilities for name matching and comparison.

This module provides text normalization functions for artist and album names
to improve matching accuracy across different data sources.
"""

import re
import unicodedata


def normalize_name(name: str) -> str:
    """Normalize an artist or album name for matching.

    Converts to lowercase, removes special characters, normalizes whitespace,
    and handles common variations like '&' vs 'and'.

    Args:
        name: The name to normalize

    Returns:
        Normalized name string

    Examples:
        >>> normalize_name("The Beatles & Co.")
        "the beatles and co"
        >>> normalize_name("Bjork")
        "bjork"
        >>> normalize_name("  AC/DC  ")
        "ac dc"

    """
    if not name or not isinstance(name, str):
        return ""

    # Unicode normalization - decompose accented characters
    name = unicodedata.normalize("NFD", name)

    # Remove diacritics/accents
    name = "".join(char for char in name if unicodedata.category(char) != "Mn")

    # Convert to lowercase
    name = name.lower()

    # Replace common variations
    replacements = {
        "&": "and",
        "+": "and",
        "/": " ",
        "-": " ",
        "_": " ",
        ".": " ",
        "(": " ",
        ")": " ",
        "[": " ",
        "]": " ",
        "{": " ",
        "}": " ",
        '"': "",
        "'": "",
        "`": "",
        ",": " ",
        ";": " ",
        ":": " ",
        "!": "",
        "?": "",
        "#": "",
        "*": "",
        "@": "",
        "%": "",
        "$": "",
        "^": "",
        "~": "",
        "|": " ",
        "\\": " ",
        "<": " ",
        ">": " ",
        "=": " ",
    }

    for old, new in replacements.items():
        name = name.replace(old, new)

    # Remove "the" prefix (common for band names)
    if name.startswith("the "):
        name = name[4:]

    # Normalize whitespace - collapse multiple spaces into single space
    name = re.sub(r"\s+", " ", name)

    # Strip leading/trailing whitespace
    return name.strip()


def simple_normalize(name: str) -> str:
    """Apply simple normalization - lowercase and strip whitespace.

    Args:
        name: The name to normalize

    Returns:
        Simply normalized name string

    """
    return "" if not name or not isinstance(name, str) else name.strip().lower()


def normalize_for_comparison(name: str) -> str:
    """Normalize name specifically for comparison operations.

    More aggressive normalization that removes more characters
    for loose matching scenarios.

    Args:
        name: The name to normalize

    Returns:
        Aggressively normalized name string

    """
    if not name or not isinstance(name, str):
        return ""

    # Start with standard normalization
    normalized = normalize_name(name)

    # Remove even more characters for comparison
    normalized = re.sub(r"[^a-z0-9\s]", "", normalized)

    # Remove common words that might interfere with matching
    stop_words = ["and", "or", "with", "vs", "versus", "feat", "ft", "featuring"]
    words = normalized.split()
    words = [word for word in words if word not in stop_words]

    return " ".join(words)


def generate_ukrainian_variations(name: str) -> list[str]:
    """Generate common transliteration variations for Ukrainian names.

    Ukrainian names can be transliterated differently across systems.
    This function generates common variations to improve API matching.

    Args:
        name: The original name to generate variations for

    Returns:
        List of name variations including the original

    """
    if not name or not isinstance(name, str):
        return [name] if name else []

    variations = [name]
    name_lower = name.lower()

    # Common Ukrainian transliteration patterns
    transliterations = {
        # Cyrillic to Latin variations
        "і"': "i"',"y"'],  # і can be i or y
        "ї"': "yi"',"i"'],  # ї can be yi or i
        "є"': "ye"',"e"'],  # є can be ye or e
        "ю"': "yu"',"u"'],  # ю can be yu or u
        "я"': "ya"',"a"'],  # я can be ya or a
        "ч"': "ch"',"c"'],  # ч can be ch or c
        "ш"': "sh"',"s"'],  # ш can be sh or s
        "щ"': "sch"',"sh"',"shch"'],  # щ variations
        "х"': "kh"',"h"',"x"'],  # х can be kh, h, or x
        "ц"': "ts"',"c"'],  # ц can be ts or c
        "ж"': "zh"',"z"'],  # ж can be zh or z
        "г"': "h"',"g"'],  # г can be h or g
        # Common name endings
        "enko": ["enko", "enco"],  # surname ending variations
        "ovich": ["ovich", "ovych"],  # patronymic variations
        "yuk": ["yuk", "iuk"],  # surname variations
    }

    # Apply transliteration variations
    for original, alternatives in transliterations.items():
        if original in name_lower:
            for alt in alternatives:
                if alt != original:
                    variation = name_lower.replace(original, alt)
                    variations.append(variation)

    # Remove duplicates while preserving order
    seen = set()
    unique_variations = []
    for var in variations:
        if var not in seen:
            seen.add(var)
            unique_variations.append(var)

    return unique_variations
