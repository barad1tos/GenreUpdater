"""Tests for validators module."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.models.track_models import TrackDict
from src.core.models.validators import (
    SecurityValidationError,
    SecurityValidator,
    is_empty_year,
    is_valid_track_item,
    is_valid_year,
    validate_album_name,
    validate_artist_name,
    validate_track_ids,
)


class TestIsValidYear:
    """Tests for is_valid_year function."""

    def test_valid_year_string(self) -> None:
        """Should return True for valid year string."""
        assert is_valid_year("2020") is True

    def test_valid_year_int(self) -> None:
        """Should return True for valid year int."""
        assert is_valid_year(2020) is True

    def test_valid_year_float(self) -> None:
        """Should return True for valid year float."""
        assert is_valid_year(2020.0) is True

    def test_none_returns_false(self) -> None:
        """Should return False for None."""
        assert is_valid_year(None) is False

    def test_empty_string_returns_false(self) -> None:
        """Should return False for empty string."""
        assert is_valid_year("") is False

    def test_invalid_string_returns_false(self) -> None:
        """Should return False for non-numeric string."""
        assert is_valid_year("abc") is False

    def test_year_before_min_returns_false(self) -> None:
        """Should return False for year before min_year."""
        assert is_valid_year("1800", min_year=1900) is False

    def test_year_after_current_returns_false(self) -> None:
        """Should return False for year after current_year."""
        assert is_valid_year("2099", current_year=2025) is False

    def test_custom_min_year(self) -> None:
        """Should use custom min_year."""
        assert is_valid_year("1950", min_year=1960) is False
        assert is_valid_year("1970", min_year=1960) is True

    def test_custom_current_year(self) -> None:
        """Should use custom current_year."""
        assert is_valid_year("2030", current_year=2025) is False
        assert is_valid_year("2020", current_year=2025) is True

    def test_whitespace_stripped(self) -> None:
        """Should strip whitespace from string."""
        assert is_valid_year("  2020  ") is True

    def test_overflow_returns_false(self) -> None:
        """Should return False for overflow year."""
        assert is_valid_year("999999999999") is False

    def test_negative_year_returns_false(self) -> None:
        """Should return False for negative year."""
        assert is_valid_year("-2020") is False


class TestIsEmptyYear:
    """Tests for is_empty_year function."""

    def test_none_is_empty(self) -> None:
        """Should return True for None."""
        assert is_empty_year(None) is True

    def test_empty_string_is_empty(self) -> None:
        """Should return True for empty string."""
        assert is_empty_year("") is True

    def test_whitespace_is_empty(self) -> None:
        """Should return True for whitespace-only string."""
        assert is_empty_year("   ") is True

    def test_zero_is_empty(self) -> None:
        """Should return True for zero."""
        assert is_empty_year(0) is True

    def test_valid_year_not_empty(self) -> None:
        """Should return False for valid year."""
        assert is_empty_year("2020") is False

    def test_int_year_not_empty(self) -> None:
        """Should return False for int year."""
        assert is_empty_year(2020) is False


class TestConvertToTrackDict:
    """Tests for _convert_to_track_dict via is_valid_track_item."""

    def test_valid_dict(self) -> None:
        """Should accept valid dict."""
        track = {"id": "1", "artist": "Artist", "name": "Track", "album": "Album"}
        assert is_valid_track_item(track) is True

    def test_dict_with_non_string_keys(self) -> None:
        """Should reject dict with non-string keys."""
        track = {1: "value", "artist": "Artist", "name": "Track", "album": "Album", "id": "1"}
        assert is_valid_track_item(track) is False

    def test_pydantic_model(self) -> None:
        """Should accept Pydantic model with model_dump."""
        track = TrackDict(id="1", artist="Artist", name="Track", album="Album", genre="Rock", year="2020")
        assert is_valid_track_item(track) is True

    def test_object_without_model_dump(self) -> None:
        """Should reject object without model_dump."""
        assert is_valid_track_item("not a track") is False

    def test_model_dump_raises_error(self) -> None:
        """Should return False when model_dump raises error."""
        mock_obj = MagicMock()
        mock_obj.model_dump.side_effect = TypeError("Cannot dump")
        assert is_valid_track_item(mock_obj) is False


class TestValidateTrackFields:
    """Tests for _validate_track_fields via is_valid_track_item."""

    def test_missing_required_field(self) -> None:
        """Should reject track missing required field."""
        track = {"id": "1", "artist": "Artist", "name": "Track"}  # missing album
        assert is_valid_track_item(track) is False

    def test_non_string_required_field(self) -> None:
        """Should reject track with non-string required field."""
        track = {"id": 1, "artist": "Artist", "name": "Track", "album": "Album"}
        assert is_valid_track_item(track) is False

    def test_invalid_optional_field_type(self) -> None:
        """Should reject track with non-string optional field."""
        track = {"id": "1", "artist": "Artist", "name": "Track", "album": "Album", "genre": 123}
        assert is_valid_track_item(track) is False

    def test_none_optional_field_valid(self) -> None:
        """Should accept track with None optional field."""
        track = {"id": "1", "artist": "Artist", "name": "Track", "album": "Album", "genre": None}
        assert is_valid_track_item(track) is True


class TestValidateTrackIds:
    """Tests for validate_track_ids function."""

    def test_filters_numeric_ids(self) -> None:
        """Should keep only numeric IDs."""
        result = validate_track_ids(["1", "2", "abc", "3"], "2020")
        assert result == ["1", "2", "3"]

    def test_excludes_year_value(self) -> None:
        """Should exclude IDs matching year value."""
        result = validate_track_ids(["1", "2020", "3"], "2020")
        assert result == ["1", "3"]

    def test_empty_list(self) -> None:
        """Should return empty list for empty input."""
        result = validate_track_ids([], "2020")
        assert result == []

    def test_all_invalid(self) -> None:
        """Should return empty list when all invalid."""
        result = validate_track_ids(["abc", "def"], "2020")
        assert result == []


class TestValidateArtistName:
    """Tests for validate_artist_name function."""

    def test_valid_artist(self) -> None:
        """Should return True for valid artist."""
        assert validate_artist_name("Pink Floyd") is True

    def test_none_returns_false(self) -> None:
        """Should return False for None."""
        assert validate_artist_name(None) is False

    def test_empty_string_returns_false(self) -> None:
        """Should return False for empty string."""
        assert validate_artist_name("") is False

    def test_whitespace_only_returns_false(self) -> None:
        """Should return False for whitespace-only."""
        assert validate_artist_name("   ") is False

    def test_strips_whitespace(self) -> None:
        """Should strip whitespace and validate."""
        assert validate_artist_name("  Artist  ") is True


class TestValidateAlbumName:
    """Tests for validate_album_name function."""

    def test_valid_album(self) -> None:
        """Should return True for valid album."""
        assert validate_album_name("The Dark Side of the Moon") is True

    def test_none_returns_false(self) -> None:
        """Should return False for None."""
        assert validate_album_name(None) is False

    def test_empty_string_returns_false(self) -> None:
        """Should return False for empty string."""
        assert validate_album_name("") is False

    def test_whitespace_only_returns_false(self) -> None:
        """Should return False for whitespace-only."""
        assert validate_album_name("   ") is False

    def test_strips_whitespace(self) -> None:
        """Should strip whitespace and validate."""
        assert validate_album_name("  Album  ") is True


class TestSecurityValidationError:
    """Tests for SecurityValidationError exception."""

    def test_basic_message(self) -> None:
        """Should store message."""
        error = SecurityValidationError("Test error")
        assert str(error) == "Test error"

    def test_field_attribute(self) -> None:
        """Should store field attribute."""
        error = SecurityValidationError("Error", field="artist")
        assert error.field == "artist"

    def test_dangerous_pattern_attribute(self) -> None:
        """Should store dangerous_pattern attribute."""
        error = SecurityValidationError("Error", dangerous_pattern="<script>")
        assert error.dangerous_pattern == "<script>"


class TestSecurityValidator:
    """Tests for SecurityValidator class."""

    @pytest.fixture
    def validator(self) -> SecurityValidator:
        """Create validator instance."""
        return SecurityValidator()

    @pytest.fixture
    def validator_with_logger(self) -> SecurityValidator:
        """Create validator with custom logger."""
        logger = logging.getLogger("test.security")
        return SecurityValidator(logger=logger)

    def test_init_default_logger(self, validator: SecurityValidator) -> None:
        """Should use default logger when not provided."""
        assert validator.logger is not None

    def test_init_custom_logger(self, validator_with_logger: SecurityValidator) -> None:
        """Should use custom logger when provided."""
        assert validator_with_logger.logger.name == "test.security"


class TestSecurityValidatorSanitizeString:
    """Tests for SecurityValidator.sanitize_string method."""

    def test_removes_control_characters(self) -> None:
        """Should remove control characters."""
        result = SecurityValidator.sanitize_string("hello\x00world")
        assert result == "helloworld"

    def test_strips_whitespace(self) -> None:
        """Should strip leading/trailing whitespace."""
        result = SecurityValidator.sanitize_string("  hello  ")
        assert result == "hello"

    def test_raises_on_too_long_string(self) -> None:
        """Should raise error for string exceeding max length."""
        long_string = "a" * 1001
        with pytest.raises(SecurityValidationError, match="String too long"):
            SecurityValidator.sanitize_string(long_string)

    def test_preserves_valid_characters(self) -> None:
        """Should preserve valid special characters."""
        result = SecurityValidator.sanitize_string("AC/DC - Don't Stop")
        assert result == "AC/DC - Don't Stop"


class TestSecurityValidatorValidateTrackData:
    """Tests for SecurityValidator.validate_track_data method."""

    @pytest.fixture
    def validator(self) -> SecurityValidator:
        """Create validator instance."""
        return SecurityValidator()

    def test_validates_required_fields(self, validator: SecurityValidator) -> None:
        """Should validate all required fields."""
        track = {"id": "123", "artist": "Artist", "name": "Track", "album": "Album"}
        result = validator.validate_track_data(track)
        assert result["id"] == "123"
        assert result["artist"] == "Artist"

    def test_raises_on_missing_field(self, validator: SecurityValidator) -> None:
        """Should raise error for missing required field."""
        track = {"id": "123", "artist": "Artist", "name": "Track"}
        with pytest.raises(SecurityValidationError, match="Required field 'album' is missing"):
            validator.validate_track_data(track)

    def test_raises_on_non_string_field(self, validator: SecurityValidator) -> None:
        """Should raise error for non-string required field."""
        track = {"id": 123, "artist": "Artist", "name": "Track", "album": "Album"}
        with pytest.raises(SecurityValidationError, match="must be a string"):
            validator.validate_track_data(track)

    def test_handles_optional_fields(self, validator: SecurityValidator) -> None:
        """Should handle optional fields."""
        track = {"id": "123", "artist": "Artist", "name": "Track", "album": "Album", "genre": "Rock", "year": "2020"}
        result = validator.validate_track_data(track)
        assert result["genre"] == "Rock"
        assert result["year"] == "2020"

    def test_converts_non_string_optional_to_string(self, validator: SecurityValidator) -> None:
        """Should convert non-string optional to string."""
        track = {"id": "123", "artist": "Artist", "name": "Track", "album": "Album", "year": 2020}
        result = validator.validate_track_data(track)
        assert result["year"] == "2020"

    def test_handles_none_optional_fields(self, validator: SecurityValidator) -> None:
        """Should handle None optional fields."""
        track = {"id": "123", "artist": "Artist", "name": "Track", "album": "Album", "genre": None}
        result = validator.validate_track_data(track)
        assert result["genre"] is None


class TestSecurityValidatorValidateTrackIdFormat:
    """Tests for SecurityValidator._validate_track_id_format method."""

    def test_valid_numeric_id(self) -> None:
        """Should accept valid numeric ID."""
        SecurityValidator._validate_track_id_format("12345")

    def test_valid_alphanumeric_id(self) -> None:
        """Should accept alphanumeric ID."""
        SecurityValidator._validate_track_id_format("track-123_abc")

    def test_raises_on_empty_id(self) -> None:
        """Should raise error for empty ID."""
        with pytest.raises(SecurityValidationError, match="cannot be empty"):
            SecurityValidator._validate_track_id_format("")

    def test_raises_on_too_long_id(self) -> None:
        """Should raise error for too long ID."""
        long_id = "a" * 101
        with pytest.raises(SecurityValidationError, match="too long"):
            SecurityValidator._validate_track_id_format(long_id)

    def test_raises_on_invalid_characters(self) -> None:
        """Should raise error for invalid characters."""
        with pytest.raises(SecurityValidationError, match="Invalid track ID format"):
            SecurityValidator._validate_track_id_format("track@123")


class TestSecurityValidatorCheckSqlInjection:
    """Tests for SecurityValidator._check_sql_injection_patterns method."""

    @pytest.fixture
    def validator(self) -> SecurityValidator:
        """Create validator instance."""
        return SecurityValidator()

    def test_detects_union_select(self, validator: SecurityValidator) -> None:
        """Should detect UNION SELECT pattern."""
        with pytest.raises(SecurityValidationError, match="SQL injection pattern"):
            validator._check_sql_injection_patterns("UNION SELECT * FROM users", "field")

    def test_detects_drop_table(self, validator: SecurityValidator) -> None:
        """Should detect DROP TABLE pattern."""
        with pytest.raises(SecurityValidationError, match="SQL injection pattern"):
            validator._check_sql_injection_patterns("DROP TABLE users", "field")

    def test_allows_safe_string(self, validator: SecurityValidator) -> None:
        """Should allow safe strings."""
        validator._check_sql_injection_patterns("Normal artist name", "field")


class TestSecurityValidatorCheckXss:
    """Tests for SecurityValidator._check_xss_patterns method."""

    @pytest.fixture
    def validator(self) -> SecurityValidator:
        """Create validator instance."""
        return SecurityValidator()

    def test_detects_script_tag(self, validator: SecurityValidator) -> None:
        """Should detect script tag."""
        with pytest.raises(SecurityValidationError, match="XSS pattern"):
            validator._check_xss_patterns("<script>alert('xss')</script>", "field")

    def test_detects_javascript_url(self, validator: SecurityValidator) -> None:
        """Should detect javascript: URL."""
        with pytest.raises(SecurityValidationError, match="XSS pattern"):
            validator._check_xss_patterns("javascript:alert(1)", "field")

    def test_detects_event_handler(self, validator: SecurityValidator) -> None:
        """Should detect event handler."""
        with pytest.raises(SecurityValidationError, match="XSS pattern"):
            validator._check_xss_patterns("onclick=alert(1)", "field")

    def test_allows_safe_string(self, validator: SecurityValidator) -> None:
        """Should allow safe strings."""
        validator._check_xss_patterns("Normal text", "field")


class TestSecurityValidatorValidateFilePath:
    """Tests for SecurityValidator.validate_file_path method."""

    @pytest.fixture
    def validator(self) -> SecurityValidator:
        """Create validator instance."""
        return SecurityValidator()

    def test_validates_normal_path(self, validator: SecurityValidator) -> None:
        """Should validate normal path."""
        result = validator.validate_file_path("/tmp/test.txt")
        assert "test.txt" in result

    def test_raises_on_empty_path(self, validator: SecurityValidator) -> None:
        """Should raise error for empty path."""
        with pytest.raises(SecurityValidationError, match="cannot be empty"):
            validator.validate_file_path("")

    def test_raises_on_too_long_path(self, validator: SecurityValidator) -> None:
        """Should raise error for too long path."""
        long_path = "/tmp/" + "a" * 300
        with pytest.raises(SecurityValidationError, match="too long"):
            validator.validate_file_path(long_path)

    def test_expands_user_path(self, validator: SecurityValidator) -> None:
        """Should expand ~ in path."""
        result = validator.validate_file_path("~/test.txt")
        assert "~" not in result

    def test_validates_against_allowed_base_paths(self, validator: SecurityValidator) -> None:
        """Should validate against allowed base paths."""
        result = validator.validate_file_path("/tmp/test.txt", allowed_base_paths=["/tmp"])
        assert "/tmp" in result

    def test_raises_when_not_in_allowed_paths(self, validator: SecurityValidator) -> None:
        """Should raise error when path not in allowed paths."""
        with pytest.raises(SecurityValidationError, match="not within allowed base paths"):
            validator.validate_file_path("/etc/passwd", allowed_base_paths=["/tmp"])


class TestSecurityValidatorNormalizePath:
    """Tests for SecurityValidator._normalize_path method."""

    def test_normalizes_path(self) -> None:
        """Should normalize and resolve path."""
        result = SecurityValidator._normalize_path("/tmp/../tmp/test.txt")
        assert ".." not in result


class TestSecurityValidatorValidateApiInput:
    """Tests for SecurityValidator.validate_api_input method."""

    @pytest.fixture
    def validator(self) -> SecurityValidator:
        """Create validator instance."""
        return SecurityValidator()

    def test_validates_string_values(self, validator: SecurityValidator) -> None:
        """Should validate string values."""
        data: dict[str, Any] = {"key": "value"}
        result = validator.validate_api_input(data)
        assert result["key"] == "value"

    def test_preserves_numeric_values(self, validator: SecurityValidator) -> None:
        """Should preserve numeric values."""
        data: dict[str, Any] = {"count": 42, "price": 19.99}
        result = validator.validate_api_input(data)
        assert result["count"] == 42
        assert result["price"] == 19.99

    def test_preserves_boolean_values(self, validator: SecurityValidator) -> None:
        """Should preserve boolean values."""
        data: dict[str, Any] = {"enabled": True, "disabled": False}
        result = validator.validate_api_input(data)
        assert result["enabled"] is True
        assert result["disabled"] is False

    def test_validates_list_values(self, validator: SecurityValidator) -> None:
        """Should validate list values."""
        data: dict[str, Any] = {"items": ["item1", "item2"]}
        result = validator.validate_api_input(data)
        assert result["items"] == ["item1", "item2"]

    def test_validates_nested_dict(self, validator: SecurityValidator) -> None:
        """Should validate nested dict."""
        data: dict[str, Any] = {"nested": {"key": "value"}}
        result = validator.validate_api_input(data)
        assert result["nested"]["key"] == "value"

    def test_handles_none_values(self, validator: SecurityValidator) -> None:
        """Should handle None values."""
        data: dict[str, Any] = {"key": None}
        result = validator.validate_api_input(data)
        assert result["key"] is None

    def test_converts_other_types_to_string(self, validator: SecurityValidator) -> None:
        """Should convert other types to string."""
        data: dict[str, Any] = {"key": object()}
        result = validator.validate_api_input(data)
        assert isinstance(result["key"], str)

    def test_raises_on_max_depth_exceeded(self, validator: SecurityValidator) -> None:
        """Should raise error when max depth exceeded."""
        # Create deeply nested structure
        data: dict[str, Any] = {"level": {}}
        current = data["level"]
        for _ in range(10):
            current["next"] = {}
            current = current["next"]

        with pytest.raises(SecurityValidationError, match="Maximum nesting depth"):
            validator.validate_api_input(data, max_depth=5)

    def test_sanitizes_list_string_items(self, validator: SecurityValidator) -> None:
        """Should sanitize string items in list."""
        data: dict[str, Any] = {"items": ["item\x00one", "item two"]}
        result = validator.validate_api_input(data)
        assert result["items"][0] == "itemone"
