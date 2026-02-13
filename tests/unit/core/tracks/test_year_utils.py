"""Comprehensive tests for year_utils pure functions (#219)."""

from __future__ import annotations

import pytest

from core.tracks.year_utils import (
    normalize_collaboration_artist,
    resolve_non_negative_float,
    resolve_non_negative_int,
    resolve_positive_int,
)


# resolve_non_negative_int


class TestResolveNonNegativeInt:
    @pytest.mark.parametrize(
        ("value", "default", "expected"),
        [
            (5, 0, 5),
            (0, 99, 0),
            ("10", 0, 10),
            ("0", 7, 0),
            (3.9, 0, 3),  # float truncates
            (True, 0, 1),  # bool is int subclass
            (False, 0, 0),
        ],
        ids=["int", "zero", "str-int", "str-zero", "float-truncates", "bool-true", "bool-false"],
    )
    def test_valid_values(self, value: object, default: int, expected: int) -> None:
        assert resolve_non_negative_int(value, default) == expected

    @pytest.mark.parametrize(
        ("value", "default"),
        [
            (-3, 0),
            (-1, 42),
            ("-5", 7),
        ],
        ids=["negative-int", "negative-with-nonzero-default", "negative-str"],
    )
    def test_negative_returns_default(self, value: object, default: int) -> None:
        assert resolve_non_negative_int(value, default) == default

    @pytest.mark.parametrize(
        "value",
        [None, "abc", "", "12.5x", object(), [], {}],
        ids=["none", "alpha", "empty-str", "partial-number", "object", "list", "dict"],
    )
    def test_unconvertible_returns_default(self, value: object) -> None:
        assert resolve_non_negative_int(value, 42) == 42


# resolve_positive_int


class TestResolvePositiveInt:
    @pytest.mark.parametrize(
        ("value", "default", "expected"),
        [
            (5, 1, 5),
            (1, 99, 1),
            ("10", 1, 10),
        ],
        ids=["int", "boundary-one", "str-int"],
    )
    def test_positive_values(self, value: object, default: int, expected: int) -> None:
        assert resolve_positive_int(value, default) == expected

    @pytest.mark.parametrize(
        ("value", "default"),
        [
            (0, 1),
            (-3, 1),
            (None, 5),
            ("0", 1),
        ],
        ids=["zero", "negative", "none", "str-zero"],
    )
    def test_non_positive_returns_default(self, value: object, default: int) -> None:
        assert resolve_positive_int(value, default) == default


# resolve_non_negative_float


class TestResolveNonNegativeFloat:
    @pytest.mark.parametrize(
        ("value", "default", "expected"),
        [
            (3.14, 0.0, 3.14),
            (0.0, 99.0, 0.0),
            ("2.5", 0.0, 2.5),
            (7, 0.0, 7.0),  # int converts to float
            ("0", 0.0, 0.0),
        ],
        ids=["float", "zero", "str-float", "int-to-float", "str-zero"],
    )
    def test_valid_values(self, value: object, default: float, expected: float) -> None:
        assert resolve_non_negative_float(value, default) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("value", "default"),
        [
            (-1.0, 0.0),
            ("-0.001", 5.0),
        ],
        ids=["negative-float", "negative-str"],
    )
    def test_negative_returns_default(self, value: object, default: float) -> None:
        assert resolve_non_negative_float(value, default) == pytest.approx(default)

    @pytest.mark.parametrize(
        "value",
        [None, "abc", "", object()],
        ids=["none", "alpha", "empty-str", "object"],
    )
    def test_unconvertible_returns_default(self, value: object) -> None:
        assert resolve_non_negative_float(value, 42.0) == pytest.approx(42.0)


# normalize_collaboration_artist


class TestNormalizeCollaborationArtist:
    @pytest.mark.parametrize(
        ("artist", "expected"),
        [
            ("Drake feat. Rihanna", "Drake"),
            ("Drake feat Rihanna", "Drake"),
            ("Drake ft. Rihanna", "Drake"),
            ("Drake ft Rihanna", "Drake"),
            ("Daft Punk & Pharrell", "Daft Punk"),
            ("A vs. B", "A"),
            ("A vs B", "A"),
            ("A with B", "A"),
            ("A and B", "A"),
            ("A x B", "A"),
            ("A X B", "A"),
        ],
        ids=[
            "feat-dot",
            "feat",
            "ft-dot",
            "ft",
            "ampersand",
            "vs-dot",
            "vs",
            "with",
            "and",
            "x-lower",
            "x-upper",
        ],
    )
    def test_collaboration_separators(self, artist: str, expected: str) -> None:
        assert normalize_collaboration_artist(artist) == expected

    def test_solo_artist_unchanged(self) -> None:
        assert normalize_collaboration_artist("Solo Artist") == "Solo Artist"

    def test_empty_string(self) -> None:
        assert normalize_collaboration_artist("") == ""

    def test_separator_list_order_wins(self) -> None:
        """Separator priority follows list order, not string position.

        " & " precedes " feat. " in the separator list, so even though
        " feat. " appears earlier in the input string, " & " is checked first.
        """
        # " & " is before " feat. " in separator list → splits on " & "
        assert normalize_collaboration_artist("A feat. B & C") == "A feat. B"
        # When first-priority separator appears first, result is clean
        assert normalize_collaboration_artist("A & B feat. C") == "A"

    def test_separator_must_have_spaces(self) -> None:
        """Separator 'x' requires spaces — 'Axel Rose' should not split."""
        assert normalize_collaboration_artist("Axel Rose") == "Axel Rose"

    def test_whitespace_stripped(self) -> None:
        assert normalize_collaboration_artist("  Main Artist  feat. Other") == "Main Artist"
