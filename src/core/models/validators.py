"""Validation utilities for Music Genre Updater."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeGuard, TYPE_CHECKING


if TYPE_CHECKING:
    from core.models.track_models import TrackDict


class SupportsDictConversion(Protocol):
    """Protocol for objects that can be converted to dict (e.g., Pydantic models)."""

    def model_dump(self) -> dict[str, Any]:
        """Convert model to dictionary."""
        ...


def is_valid_year(year_str: str | float | None, min_year: int | None = None, current_year: int | None = None) -> bool:
    """Check if the given value is a valid 4-digit year.

    Args:
        year_str: Value to validate as a year
        min_year: Minimum valid year (default: 1900)
        current_year: Current year for upper bound (default: current year)

    Returns:
        True if valid year, False otherwise.

    """
    if year_str is None:
        return False

    try:
        # Handle different input types
        year_int = int(year_str) if isinstance(year_str, int | float) else int(str(year_str).strip())

        # Use defaults if not provided
        min_year_val = min_year or 1900
        current_year_val = current_year or datetime.now(tz=UTC).year

        # Trust the system - if datetime accepts it, it's valid
        datetime(year_int, 1, 1, tzinfo=UTC)
        return min_year_val <= year_int <= current_year_val

    except (ValueError, TypeError, OverflowError, OSError):
        return False


def is_empty_year(year_value: Any) -> bool:
    """Check if a year value is considered empty.

    Args:
        year_value: Year value to check

    Returns:
        True if the year is empty (None, empty string, or whitespace-only)

    """
    return not year_value or not str(year_value).strip()


def _convert_to_track_dict(item: Any) -> dict[str, Any] | None:
    """Convert item to dict format, validating structure."""
    if isinstance(item, dict):
        try:
            # Validate that it's a string-keyed dict
            track_data: dict[str, Any] = {str(k): v for k, v in item.items()}
            # Verify all keys were actually strings (no conversion happened)
            return track_data if all(isinstance(k, str) for k in item) else None
        except (TypeError, ValueError):
            return None

    # Handle Pydantic models with model_dump method
    if hasattr(item, "model_dump"):
        pydantic_item: SupportsDictConversion = item
        try:
            return pydantic_item.model_dump()
        except (AttributeError, TypeError, ValueError):
            return None

    return None


def _validate_track_fields(track_data: dict[str, Any]) -> bool:
    """Validate required and optional fields in track data."""
    # Check required fields
    required_fields = ["id", "artist", "name", "album"]
    for field in required_fields:
        if field not in track_data or not isinstance(track_data[field], str):
            return False

    # Check optional fields if present
    optional_fields = ["genre", "year", "date_added", "track_status"]
    return not any(field in track_data and track_data[field] is not None and not isinstance(track_data[field], str) for field in optional_fields)


def is_valid_track_item(item: Any) -> TypeGuard[TrackDict]:
    """Validate that the given object is a track dictionary.

    This performs runtime type checking to ensure the object
    has the required structure of a TrackDict.

    Args:
        item: Object to validate

    Returns:
        True if the object is a valid TrackDict, False otherwise

    """
    track_data = _convert_to_track_dict(item)
    return track_data is not None and _validate_track_fields(track_data)


def validate_track_ids(track_ids: list[str], year: str) -> list[str]:
    """Return list of numeric track IDs that are not equal to the year value.

    Args:
        track_ids: List of track IDs to validate
        year: Year value to check against

    Returns:
        List of valid track IDs

    """
    valid_ids: list[str] = []
    valid_ids.extend(track_id for track_id in track_ids if track_id.isdigit() and track_id != year)
    return valid_ids


def validate_artist_name(artist: str | None) -> bool:
    """Validate artist name.

    Args:
        artist: Artist name to validate

    Returns:
        True if valid, False otherwise

    """
    if not artist:
        return False

    # Strip and check if not empty
    cleaned = artist.strip()
    return len(cleaned) > 0


def validate_album_name(album: str | None) -> bool:
    """Validate album name.

    Args:
        album: Album name to validate

    Returns:
        True if valid, False otherwise

    """
    if not album:
        return False

    # Strip and check if not empty
    cleaned = album.strip()
    return len(cleaned) > 0


# Security constants for input validation (only control chars for music metadata)
# Note: DANGEROUS_CHARS is empty because music metadata legitimately uses special characters
# (e.g., "AC/DC", "Don't Stop", "2 < 3"). Validation relies on context-specific checks instead.
DANGEROUS_CHARS: list[str] = []
CONTROL_CHARS = [
    "\x00",
    "\x01",
    "\x02",
    "\x03",
    "\x04",
    "\x05",
    "\x06",
    "\x07",
    "\x08",
    "\x0e",
    "\x0f",
]
SQL_INJECTION_PATTERNS = [
    r"(?i)(union\s+select)",
    r"(?i)(drop\s+table)",
    r"(?i)(delete\s+from)",
    r"(?i)(insert\s+into)",
    r"(?i)(update\s+set)",
    r"(?i)(\-\-)",
    r"(?i)(\/\*.*\*\/)",
    r"(?i)(xp_cmdshell)",
    r"(?i)(sp_execute_sql)",
]
XSS_PATTERNS = [
    r"(?i)(<script[^>]*>)",
    r"(?i)(javascript:)",
    r"(?i)(on\w+\s*=)",
    r"(?i)(<iframe[^>]*>)",
    r"(?i)(<object[^>]*>)",
    r"(?i)(<embed[^>]*>)",
]

# Security limits
MAX_STRING_LENGTH = 1000
MAX_PATH_LENGTH = 260  # Windows MAX_PATH limit
MAX_TRACK_ID_LENGTH = 100


class SecurityValidationError(Exception):
    """Exception raised when security validation fails."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        dangerous_pattern: str | None = None,
    ) -> None:
        """Initialize the security validation error.

        Args:
            message: Error message describing the validation failure
            field: The field that failed validation
            dangerous_pattern: The specific pattern that triggered the error

        """
        super().__init__(message)
        self.field = field
        self.dangerous_pattern = dangerous_pattern


class SecurityValidator:
    """Comprehensive security validator for input sanitization and validation.

    This class provides methods to validate and sanitize various types of input
    to prevent security vulnerabilities including injection attacks, path traversal,
    and malicious content.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the security validator.

        Args:
            logger: Optional logger instance for security event logging

        """
        self.logger = logger or logging.getLogger(__name__)
        self._sql_patterns = [re.compile(pattern) for pattern in SQL_INJECTION_PATTERNS]
        self._xss_patterns = [re.compile(pattern) for pattern in XSS_PATTERNS]

    def validate_track_data(self, track_data: dict[str, Any]) -> dict[str, Any]:
        """Validate and sanitize track data dictionary.

        Args:
            track_data: Track data dictionary to validate

        Returns:
            dict[str, Any]: Validated and sanitized track data

        Raises:
            SecurityValidationError: If validation fails

        """
        validated_data: dict[str, Any] = {}

        # Required string fields (matching TrackDict model)
        required_fields = ["id", "artist", "name", "album"]
        for field in required_fields:
            if field not in track_data:
                msg = f"Required field '{field}' is missing"
                raise SecurityValidationError(msg, field)

            value = track_data[field]
            if not isinstance(value, str):
                msg = f"Field '{field}' must be a string"
                raise SecurityValidationError(msg, field)

            validated_data[field] = self.sanitize_string(value, field)

        # Optional string fields (matching TrackDict model)
        optional_fields = [
            "genre",
            "year",
            "date_added",
            "track_status",
            "year_before_mgu",
            "release_year",
            "year_set_by_mgu",
            "album_artist",
        ]
        for field in optional_fields:
            if field in track_data and track_data[field] is not None:
                value = track_data[field]
                if isinstance(value, str):
                    validated_data[field] = self.sanitize_string(value, field)
                else:
                    validated_data[field] = str(value)
            elif field in track_data:
                validated_data[field] = None

        # Special validation for track ID
        if "id" in validated_data:
            SecurityValidator._validate_track_id_format(validated_data["id"])

        self.logger.debug("Validated track data for ID: %s", validated_data.get("id", "unknown"))
        return validated_data

    @staticmethod
    def sanitize_string(value: str, field_name: str | None = None) -> str:
        """Sanitize a string value by removing dangerous characters and patterns.

        Args:
            value: The string value to sanitize
            field_name: Optional field name for logging

        Returns:
            The sanitized string

        Raises:
            SecurityValidationError: If validation fails

        """
        # Check string length
        if len(value) > MAX_STRING_LENGTH:
            msg = f"String too long: {len(value)} characters (max: {MAX_STRING_LENGTH})"
            raise SecurityValidationError(
                msg,
                field_name,
            )

        # Remove control characters
        sanitized = value
        for char in CONTROL_CHARS:
            sanitized = sanitized.replace(char, "")

        return sanitized.strip()

    def _check_sql_injection_patterns(self, sanitized: str, field_name: str | None) -> None:
        """Check for SQL injection patterns in the sanitized string.

        Args:
            sanitized: The string to check for SQL injection patterns
            field_name: Optional field name for error reporting

        Raises:
            SecurityValidationError: If SQL injection pattern is detected

        """
        for pattern in self._sql_patterns:
            if match := pattern.search(sanitized):
                msg = f"SQL injection pattern detected: '{match.group()}'"
                raise SecurityValidationError(msg, field_name, match.group())

    def _check_xss_patterns(self, sanitized: str, field_name: str | None) -> None:
        """Check for XSS patterns in the sanitized string.

        Args:
            sanitized: The string to check for XSS patterns
            field_name: Optional field name for error reporting

        Raises:
            SecurityValidationError: If XSS pattern is detected

        """
        for pattern in self._xss_patterns:
            if match := pattern.search(sanitized):
                msg = f"XSS pattern detected: '{match.group()}'"
                raise SecurityValidationError(msg, field_name, match.group())

    def validate_file_path(self, file_path: str, allowed_base_paths: list[str] | None = None) -> str:
        """Validate a file path to prevent path traversal attacks.

        Args:
            file_path: The file path to validate
            allowed_base_paths: Optional list of allowed base paths

        Returns:
            The validated and normalized file path

        Raises:
            SecurityValidationError: If validation fails

        """
        if not file_path.strip():
            msg = "File path cannot be empty"
            raise SecurityValidationError(msg)

        # Check path length
        if len(file_path) > MAX_PATH_LENGTH:
            msg = f"File path too long: {len(file_path)} characters"
            raise SecurityValidationError(msg)

        try:
            return self._validate_and_normalize_path(
                file_path,
                allowed_base_paths,
            )
        except (ValueError, OSError) as e:
            msg = f"Invalid file path: {e}"
            raise SecurityValidationError(msg) from e

    def _validate_and_normalize_path(self, file_path: str, allowed_base_paths: list[str] | None) -> str:
        # Expand user (~) and resolve path for proper validation
        expanded_path = Path(file_path).expanduser().resolve()
        normalized_path = str(expanded_path)

        # Check for path traversal: ensure resolved path doesn't contain ".."
        if ".." in expanded_path.parts:
            msg = "Path traversal attempt detected"
            raise SecurityValidationError(msg)

        # Validate against allowed base paths if provided
        if allowed_base_paths:
            allowed_resolved = [Path(base).expanduser().resolve() for base in allowed_base_paths]
            if not any(str(expanded_path).startswith(str(base)) for base in allowed_resolved):
                msg = f"Path not within allowed base paths: '{normalized_path}'"
                raise SecurityValidationError(msg)

        self.logger.debug("Validated file path: %s", normalized_path)
        return normalized_path

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        """Normalize and resolve the file path.

        Args:
            file_path: The file path to normalize

        Returns:
            The normalized absolute path

        """
        return str(Path(file_path).resolve())

    @staticmethod
    def _validate_track_id_format(track_id: str) -> None:
        """Validate track ID format for security.

        Args:
            track_id: The track ID to validate

        Raises:
            SecurityValidationError: If validation fails

        """
        if not track_id:
            msg = "Track ID cannot be empty"
            raise SecurityValidationError(msg)

        if len(track_id) > MAX_TRACK_ID_LENGTH:
            msg = f"Track ID too long: {len(track_id)} characters"
            raise SecurityValidationError(msg)

        # Track IDs should be alphanumeric with limited special characters
        if not re.match(r"^[a-zA-Z0-9\-_]+$", track_id):
            msg = f"Invalid track ID format: {track_id}"
            raise SecurityValidationError(msg)

    def validate_api_input(
        self,
        data: dict[str, Any],
        max_depth: int = 10,
        _current_depth: int = 0,
    ) -> dict[str, Any]:
        """Validate and sanitize API input data.

        Args:
            data: Input data dictionary to validate
            max_depth: Maximum recursion depth allowed (default: 10)
            _current_depth: Internal parameter for tracking recursion depth

        Returns:
            dict[str, Any]: Validated and sanitized data

        Raises:
            SecurityValidationError: If validation fails or max depth exceeded

        """
        # Check recursion depth to prevent deeply nested or cyclic structures
        if _current_depth >= max_depth:
            msg = f"Maximum nesting depth ({max_depth}) exceeded"
            raise SecurityValidationError(msg)

        validated_data: dict[str, Any] = {}

        for key, value in data.items():
            # Validate key
            sanitized_key = self.sanitize_string(str(key), "api_key")

            # Validate value based on type
            sanitized_value: Any
            if isinstance(value, str):
                sanitized_value = self.sanitize_string(value, sanitized_key)
            elif isinstance(value, int | float | bool):
                sanitized_value = value
            elif isinstance(value, list):
                sanitized_value = [(self.sanitize_string(str(item), f"{sanitized_key}_item") if isinstance(item, str) else item) for item in value]
            elif isinstance(value, dict):
                sanitized_value = self.validate_api_input(
                    value,
                    max_depth=max_depth,
                    _current_depth=_current_depth + 1,
                )
            elif value is None:
                sanitized_value = None
            else:
                sanitized_value = self.sanitize_string(str(value), sanitized_key)

            validated_data[sanitized_key] = sanitized_value

        return validated_data
