"""Property-based tests for validator functions using Hypothesis."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

from core.models.validators import (
    SecurityValidationError,
    SecurityValidator,
    is_valid_year,
    CONTROL_CHARS,
    MAX_STRING_LENGTH,
)


@pytest.mark.unit
class TestIsValidYearProperties:
    """Property-based tests for is_valid_year."""

    @given(year=st.integers(min_value=1900, max_value=2026))
    @settings(max_examples=100)
    def test_valid_range_always_accepted(self, year: int) -> None:
        """Any integer in [1900, 2026] is a valid year."""
        assert is_valid_year(year, current_year=2026)

    @given(year=st.integers(min_value=1900, max_value=2026))
    @settings(max_examples=100)
    def test_string_form_matches_int_form(self, year: int) -> None:
        """String '2020' and int 2020 give same result."""
        assert is_valid_year(str(year), current_year=2026) == is_valid_year(year, current_year=2026)

    @given(year=st.integers(min_value=1900, max_value=2026))
    @settings(max_examples=50)
    def test_whitespace_does_not_affect_result(self, year: int) -> None:
        """Leading/trailing whitespace doesn't change validity."""
        assert is_valid_year(f"  {year}  ", current_year=2026) == is_valid_year(str(year), current_year=2026)

    @given(year=st.one_of(
        st.none(),
        st.text(min_size=0, max_size=20).filter(lambda s: not s.strip().lstrip("-").isdigit()),
        st.floats(allow_nan=True, allow_infinity=True),
    ))
    @settings(max_examples=100)
    def test_never_raises_for_any_input(self, year: str | float | None) -> None:
        """is_valid_year never raises — always returns bool."""
        result = is_valid_year(year)
        assert isinstance(result, bool)

    @given(year=st.integers(max_value=1899))
    @settings(max_examples=50)
    def test_years_below_min_rejected(self, year: int) -> None:
        """Years before 1900 are rejected."""
        assert not is_valid_year(year)

    @given(year=st.integers(min_value=2100, max_value=9999))
    @settings(max_examples=50)
    def test_future_years_rejected(self, year: int) -> None:
        """Years far in the future are rejected."""
        assert not is_valid_year(year)


@pytest.mark.unit
class TestSanitizeStringProperties:
    """Property-based tests for SecurityValidator.sanitize_string."""

    @given(s=st.text(max_size=500))
    @settings(max_examples=100)
    def test_idempotence(self, s: str) -> None:
        """sanitize(sanitize(s)) == sanitize(s)."""
        first = SecurityValidator.sanitize_string(s)
        second = SecurityValidator.sanitize_string(first)
        assert first == second

    @given(s=st.text(max_size=500))
    @settings(max_examples=100)
    def test_output_length_lte_input(self, s: str) -> None:
        """Output is never longer than input."""
        result = SecurityValidator.sanitize_string(s)
        assert len(result) <= len(s)

    @given(s=st.text(max_size=500))
    @settings(max_examples=100)
    def test_no_control_chars_in_output(self, s: str) -> None:
        """Output never contains defined control characters."""
        result = SecurityValidator.sanitize_string(s)
        for char in CONTROL_CHARS:
            assert char not in result

    @given(s=st.text(max_size=500))
    @settings(max_examples=100)
    def test_output_is_stripped(self, s: str) -> None:
        """Output is always stripped of whitespace."""
        result = SecurityValidator.sanitize_string(s)
        assert result == result.strip()

    def test_over_max_length_raises(self) -> None:
        """Strings over MAX_STRING_LENGTH raise SecurityValidationError."""
        long_string = "A" * (MAX_STRING_LENGTH + 1)
        with pytest.raises(SecurityValidationError):
            SecurityValidator.sanitize_string(long_string)

    def test_at_max_length_accepted(self) -> None:
        """String at exactly MAX_STRING_LENGTH is accepted."""
        exact_string = "A" * MAX_STRING_LENGTH
        result = SecurityValidator.sanitize_string(exact_string)
        assert result == exact_string
