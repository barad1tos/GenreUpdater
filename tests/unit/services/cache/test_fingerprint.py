"""Tests for FingerprintGenerator - track fingerprinting for cache invalidation."""

import logging
from typing import Any

import pytest

from services.cache.fingerprint import (
    FingerprintGenerationError,
    FingerprintGenerator,
)


@pytest.fixture
def generator() -> FingerprintGenerator:
    """Create a FingerprintGenerator instance."""
    logger = logging.getLogger("test.fingerprint")
    return FingerprintGenerator(logger)


@pytest.fixture
def valid_track_data() -> dict[str, Any]:
    """Create valid track data for testing."""
    return {
        "persistent_id": "ABC123DEF456",
        "location": "/Users/user/Music/song.mp3",
        "file_size": 5242880,
        "duration": 240.5,
        "date_modified": "2025-09-11 10:30:00",
        "date_added": "2025-09-10 15:00:00",
    }


class TestFingerprintGeneration:
    """Tests for fingerprint generation."""

    def test_generate_fingerprint_with_valid_data(self, generator: FingerprintGenerator, valid_track_data: dict[str, Any]) -> None:
        """Test generating fingerprint with valid track data."""
        fingerprint = generator.generate_track_fingerprint(valid_track_data)

        assert isinstance(fingerprint, str)
        assert len(fingerprint) == 64  # SHA-256 produces 64 hex characters
        assert all(c in "0123456789abcdef" for c in fingerprint)

    def test_fingerprint_deterministic(self, generator: FingerprintGenerator, valid_track_data: dict[str, Any]) -> None:
        """Test that same data always produces same fingerprint."""
        fp1 = generator.generate_track_fingerprint(valid_track_data)
        fp2 = generator.generate_track_fingerprint(valid_track_data)

        assert fp1 == fp2

    def test_fingerprint_changes_with_different_data(self, generator: FingerprintGenerator, valid_track_data: dict[str, Any]) -> None:
        """Test that different data produces different fingerprint."""
        self._assert_fingerprint_changes(generator, valid_track_data, "file_size", 9999999)

    def test_fingerprint_changes_with_location(self, generator: FingerprintGenerator, valid_track_data: dict[str, Any]) -> None:
        """Test that changed location produces different fingerprint."""
        self._assert_fingerprint_changes(generator, valid_track_data, "location", "/different/path/song.mp3")

    def test_fingerprint_changes_with_persistent_id(self, generator: FingerprintGenerator, valid_track_data: dict[str, Any]) -> None:
        """Test that changed persistent_id produces different fingerprint."""
        self._assert_fingerprint_changes(generator, valid_track_data, "persistent_id", "DIFFERENT_ID")

    @staticmethod
    def _assert_fingerprint_changes(
        generator: FingerprintGenerator,
        track_data: dict[str, Any],
        field: str,
        new_value: Any,
    ) -> None:
        """Assert that modifying a field changes the fingerprint."""
        fp1 = generator.generate_track_fingerprint(track_data)
        modified_data = track_data.copy()
        modified_data[field] = new_value
        fp2 = generator.generate_track_fingerprint(modified_data)
        assert fp1 != fp2

    def test_fingerprint_with_minimal_data(self, generator: FingerprintGenerator) -> None:
        """Test fingerprint generation with only required fields."""
        minimal_data = {
            "persistent_id": "MINIMAL123",
            "location": "/path/to/file.mp3",
        }
        fingerprint = generator.generate_track_fingerprint(minimal_data)

        assert isinstance(fingerprint, str)
        assert len(fingerprint) == 64


class TestFingerprintValidation:
    """Tests for input validation."""

    def test_missing_persistent_id_raises_error(self, generator: FingerprintGenerator) -> None:
        """Test that missing persistent_id raises error."""
        invalid_data = {
            "location": "/path/to/file.mp3",
        }
        with pytest.raises(FingerprintGenerationError, match="Missing required properties"):
            generator.generate_track_fingerprint(invalid_data)

    def test_missing_location_raises_error(self, generator: FingerprintGenerator) -> None:
        """Test that missing location raises error."""
        invalid_data = {
            "persistent_id": "ABC123",
        }
        with pytest.raises(FingerprintGenerationError, match="Missing required properties"):
            generator.generate_track_fingerprint(invalid_data)

    def test_empty_persistent_id_raises_error(self, generator: FingerprintGenerator) -> None:
        """Test that empty persistent_id raises error."""
        invalid_data = {
            "persistent_id": "",
            "location": "/path/to/file.mp3",
        }
        with pytest.raises(FingerprintGenerationError, match="persistent_id cannot be empty"):
            generator.generate_track_fingerprint(invalid_data)

    def test_whitespace_persistent_id_raises_error(self, generator: FingerprintGenerator) -> None:
        """Test that whitespace-only persistent_id raises error."""
        invalid_data = {
            "persistent_id": "   ",
            "location": "/path/to/file.mp3",
        }
        with pytest.raises(FingerprintGenerationError, match="persistent_id cannot be empty"):
            generator.generate_track_fingerprint(invalid_data)

    def test_non_dict_raises_error(self, generator: FingerprintGenerator) -> None:
        """Test that non-dict input raises error."""
        with pytest.raises(FingerprintGenerationError, match="must be a dictionary"):
            generator.generate_track_fingerprint("not a dict")  # type: ignore[arg-type]

    def test_none_input_raises_error(self, generator: FingerprintGenerator) -> None:
        """Test that None input raises error."""
        with pytest.raises(FingerprintGenerationError, match="must be a dictionary"):
            generator.generate_track_fingerprint(None)  # type: ignore[arg-type]


class TestFingerprintValidateFormat:
    """Tests for fingerprint format validation."""

    def test_validate_valid_fingerprint(self) -> None:
        """Test validation of a valid fingerprint."""
        valid_fp = "a" * 64
        assert FingerprintGenerator.validate_fingerprint(valid_fp) is True

    def test_validate_mixed_hex_fingerprint(self) -> None:
        """Test validation of mixed hex fingerprint."""
        valid_fp = "0123456789abcdef" * 4
        assert FingerprintGenerator.validate_fingerprint(valid_fp) is True

    def test_validate_short_fingerprint_fails(self) -> None:
        """Test that short fingerprint fails validation."""
        short_fp = "a" * 63
        assert FingerprintGenerator.validate_fingerprint(short_fp) is False

    def test_validate_long_fingerprint_fails(self) -> None:
        """Test that long fingerprint fails validation."""
        long_fp = "a" * 65
        assert FingerprintGenerator.validate_fingerprint(long_fp) is False

    def test_validate_non_hex_fingerprint_fails(self) -> None:
        """Test that non-hex characters fail validation."""
        invalid_fp = "g" * 64  # 'g' is not a hex character
        assert FingerprintGenerator.validate_fingerprint(invalid_fp) is False

    def test_validate_non_string_fails(self) -> None:
        """Test that non-string input fails validation."""
        assert FingerprintGenerator.validate_fingerprint(123) is False  # type: ignore[arg-type]
        assert FingerprintGenerator.validate_fingerprint(None) is False  # type: ignore[arg-type]
        assert FingerprintGenerator.validate_fingerprint([]) is False  # type: ignore[arg-type]


class TestFingerprintComparison:
    """Tests for fingerprint comparison."""

    def test_fingerprints_match_identical(self, generator: FingerprintGenerator) -> None:
        """Test matching identical fingerprints."""
        fp = "a" * 64
        assert generator.fingerprints_match(fp, fp) is True

    def test_fingerprints_match_equal_values(self, generator: FingerprintGenerator) -> None:
        """Test matching equal fingerprint values."""
        fp1 = "0123456789abcdef" * 4
        fp2 = "0123456789abcdef" * 4
        assert generator.fingerprints_match(fp1, fp2) is True

    def test_fingerprints_dont_match_different(self, generator: FingerprintGenerator) -> None:
        """Test non-matching different fingerprints."""
        fp1 = "a" * 64
        fp2 = "b" * 64
        assert generator.fingerprints_match(fp1, fp2) is False

    def test_fingerprints_match_invalid_first_fails(self, generator: FingerprintGenerator) -> None:
        """Test that invalid first fingerprint fails."""
        assert generator.fingerprints_match("invalid", "a" * 64) is False

    def test_fingerprints_match_invalid_second_fails(self, generator: FingerprintGenerator) -> None:
        """Test that invalid second fingerprint fails."""
        assert generator.fingerprints_match("a" * 64, "invalid") is False


class TestFingerprintSummary:
    """Tests for fingerprint summary."""

    def test_get_summary_contains_fingerprint(self, generator: FingerprintGenerator, valid_track_data: dict[str, Any]) -> None:
        """Test that summary contains fingerprint."""
        summary = generator.get_fingerprint_summary(valid_track_data)

        assert "fingerprint" in summary
        assert len(summary["fingerprint"]) == 64

    def test_get_summary_contains_properties(self, generator: FingerprintGenerator, valid_track_data: dict[str, Any]) -> None:
        """Test that summary contains properties used."""
        summary = generator.get_fingerprint_summary(valid_track_data)

        assert "properties_used" in summary
        assert "persistent_id" in summary["properties_used"]
        assert "location" in summary["properties_used"]

    def test_get_summary_contains_track_id(self, generator: FingerprintGenerator, valid_track_data: dict[str, Any]) -> None:
        """Test that summary contains track_id."""
        summary = generator.get_fingerprint_summary(valid_track_data)

        assert summary["track_id"] == valid_track_data["persistent_id"]

    def test_get_summary_property_count(self, generator: FingerprintGenerator, valid_track_data: dict[str, Any]) -> None:
        """Test that summary has correct property count."""
        summary = generator.get_fingerprint_summary(valid_track_data)

        # 2 required + 4 optional properties
        assert summary["property_count"] == 6


class TestNormalization:
    """Tests for property normalization."""

    def test_normalize_numeric_valid_int(self) -> None:
        """Test normalizing valid integer."""
        assert FingerprintGenerator._normalize_numeric_value(100) == 100.0

    def test_normalize_numeric_valid_float(self) -> None:
        """Test normalizing valid float."""
        assert FingerprintGenerator._normalize_numeric_value(100.5) == 100.5

    def test_normalize_numeric_string_number(self) -> None:
        """Test normalizing string number."""
        assert FingerprintGenerator._normalize_numeric_value("100") == 100.0

    def test_normalize_numeric_none(self) -> None:
        """Test normalizing None returns 0."""
        assert FingerprintGenerator._normalize_numeric_value(None) == 0.0

    def test_normalize_numeric_empty_string(self) -> None:
        """Test normalizing empty string returns 0."""
        assert FingerprintGenerator._normalize_numeric_value("") == 0.0

    def test_normalize_numeric_invalid_string(self) -> None:
        """Test normalizing invalid string returns 0."""
        assert FingerprintGenerator._normalize_numeric_value("not a number") == 0.0

    def test_normalize_string_valid(self) -> None:
        """Test normalizing valid string."""
        assert FingerprintGenerator._normalize_string_value("hello") == "hello"

    def test_normalize_string_with_whitespace(self) -> None:
        """Test normalizing string strips whitespace."""
        assert FingerprintGenerator._normalize_string_value("  hello  ") == "hello"

    def test_normalize_string_none(self) -> None:
        """Test normalizing None returns empty string."""
        assert FingerprintGenerator._normalize_string_value(None) == ""

    def test_normalize_string_number(self) -> None:
        """Test normalizing number converts to string."""
        assert FingerprintGenerator._normalize_string_value(123) == "123"


class TestDefaultValues:
    """Tests for default value handling."""

    def test_missing_optional_fields_use_defaults(self, generator: FingerprintGenerator) -> None:
        """Test that missing optional fields use defaults."""
        minimal_data = {
            "persistent_id": "TEST123",
            "location": "/path/to/file.mp3",
        }
        summary = generator.get_fingerprint_summary(minimal_data)

        props = summary["properties_used"]
        assert props["file_size"] == 0.0
        assert props["duration"] == 0.0
        assert props["date_modified"] == ""
        assert props["date_added"] == ""

    def test_partial_optional_fields(self, generator: FingerprintGenerator) -> None:
        """Test with some optional fields present."""
        partial_data = {
            "persistent_id": "TEST123",
            "location": "/path/to/file.mp3",
            "file_size": 1024,
            # duration, date_modified, date_added missing
        }
        summary = generator.get_fingerprint_summary(partial_data)

        props = summary["properties_used"]
        assert props["file_size"] == 1024.0
        assert props["duration"] == 0.0


class TestEdgeCases:
    """Tests for edge cases."""

    def test_unicode_in_location(self, generator: FingerprintGenerator) -> None:
        """Test fingerprint with unicode in location."""
        data = {
            "persistent_id": "TEST123",
            "location": "/Users/音楽/файл.mp3",  # Japanese and Cyrillic
        }
        fingerprint = generator.generate_track_fingerprint(data)
        assert len(fingerprint) == 64

    def test_very_long_location(self, generator: FingerprintGenerator) -> None:
        """Test fingerprint with very long location."""
        data = {
            "persistent_id": "TEST123",
            "location": "/very/long/" + "path/" * 100 + "file.mp3",
        }
        fingerprint = generator.generate_track_fingerprint(data)
        assert len(fingerprint) == 64

    def test_special_characters_in_location(self, generator: FingerprintGenerator) -> None:
        """Test fingerprint with special characters."""
        data = {
            "persistent_id": "TEST123",
            "location": "/path/to/file with spaces & (special) [chars].mp3",
        }
        fingerprint = generator.generate_track_fingerprint(data)
        assert len(fingerprint) == 64

    def test_zero_values(self, generator: FingerprintGenerator) -> None:
        """Test fingerprint with zero values."""
        data = {
            "persistent_id": "TEST123",
            "location": "/path/to/file.mp3",
            "file_size": 0,
            "duration": 0,
        }
        fingerprint = generator.generate_track_fingerprint(data)
        assert len(fingerprint) == 64

    def test_large_file_size(self, generator: FingerprintGenerator) -> None:
        """Test fingerprint with large file size."""
        data = {
            "persistent_id": "TEST123",
            "location": "/path/to/file.mp3",
            "file_size": 10_000_000_000,  # 10GB
        }
        fingerprint = generator.generate_track_fingerprint(data)
        assert len(fingerprint) == 64


class TestCanonicalRepresentation:
    """Tests for canonical JSON representation."""

    def test_key_order_consistent(self, generator: FingerprintGenerator) -> None:
        """Test that key order doesn't affect fingerprint."""
        data1 = {
            "persistent_id": "TEST",
            "location": "/path",
            "file_size": 100,
            "duration": 200,
        }
        # Same data, different insertion order
        data2: dict[str, Any] = {
            "duration": 200,
            "file_size": 100,
            "location": "/path",
            "persistent_id": "TEST",
        }
        fp1 = generator.generate_track_fingerprint(data1)
        fp2 = generator.generate_track_fingerprint(data2)

        assert fp1 == fp2
