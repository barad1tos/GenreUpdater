"""Track Fingerprint Generator.

This module provides track fingerprinting functionality for content-based cache invalidation.
Instead of using TTL expiration, tracks are fingerprinted based on critical properties that
indicate meaningful changes. The fingerprint is a SHA-256 hash of canonical track data.

Core Concept:
- Track fingerprint = SHA-256(persistent_id + location + file_size + duration + dates)
- Cache invalidates only when fingerprint changes (content-based invalidation)
- Eliminates wasteful TTL expiration for stable music library data

Usage:
    generator = FingerprintGenerator()
    fingerprint = generator.generate_track_fingerprint(track_data)
    if fingerprint != cached_fingerprint:
        invalidate_cache(track_id)

Design Philosophy (Linus Torvalds approved):
- Deterministic: Same track data always produces same fingerprint
- Minimal: Only essential properties that matter for cache validity
- Fast: SHA-256 is fast enough for 1000+ tracks
- Reliable: Detects all meaningful changes, ignores cosmetic ones
"""

import hashlib
import json
import logging
from typing import Any, ClassVar



class FingerprintGenerationError(Exception):
    """Raised when fingerprint generation fails."""

    def __init__(self, message: str, track_data: dict[str, Any] | None = None) -> None:
        """Initialize fingerprint generation error.

        Args:
            message: Error description
            track_data: Track data that caused the error (optional, for debugging)
        """
        super().__init__(message)
        self.track_data = track_data


class FingerprintGenerator:
    """Generates deterministic fingerprints for music tracks.

    Track fingerprints are SHA-256 hashes of critical track properties that indicate
    meaningful changes. This enables content-based cache invalidation instead of
    wasteful time-based TTL expiration.

    Fingerprint Properties (carefully chosen):
        - persistent_id: Primary key from Music.app (never changes for same track)
        - location: File path (changes when file moved/deleted)
        - file_size: File size in bytes (changes when file replaced)
        - duration: Track duration (changes when file replaced)
        - date_modified: File modification timestamp (changes on metadata edits)
        - date_added: When track was added (changes on re-import)

    Deliberately Excluded Properties:
        - play_count: Changes frequently but doesn't affect processing
        - rating: User preference, not content change
        - last_played: Frequent changes irrelevant to processing
        - genre: This is what we're computing, can't use for fingerprint!
    """

    # Constants for fingerprint generation
    ENCODING = "utf-8"
    HASH_ALGORITHM = "sha256"

    # Required properties for valid fingerprint
    REQUIRED_PROPERTIES: ClassVar[set[str]] = {
        "persistent_id",  # Essential - primary key
        "location",       # Essential - file path
    }

    # Optional properties with defaults
    OPTIONAL_PROPERTIES: ClassVar[dict[str, Any]] = {
        "file_size": 0,
        "duration": 0,
        "date_modified": "",
        "date_added": "",
    }

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize fingerprint generator.

        Args:
            logger: Logger for debugging fingerprint generation (optional)
        """
        self.logger = logger or logging.getLogger(__name__)

    def generate_track_fingerprint(self, track_data: dict[str, Any]) -> str:
        """Generate SHA-256 fingerprint for a music track.

        Creates a deterministic hash based on critical track properties that
        indicate meaningful content changes. The fingerprint enables content-based
        cache invalidation instead of arbitrary TTL expiration.

        Args:
            track_data: Track metadata dictionary containing track properties

        Returns:
            SHA-256 hash string (64 hex characters) representing track fingerprint

        Raises:
            FingerprintGenerationError: If required properties missing or invalid

        Example:
            track_data = {
                "persistent_id": "ABC123DEF456",
                "location": "/Users/user/Music/song.mp3",
                "file_size": 5242880,
                "duration": 240.5,
                "date_modified": "2025-09-11 10:30:00",
                "date_added": "2025-09-10 15:00:00"
            }
            fingerprint = generator.generate_track_fingerprint(track_data)
            # Returns: "a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456"
        """
        try:
            return self._create_fingerprint_hash(track_data)
        except Exception as e:
            error_msg = f"Failed to generate fingerprint: {e}"
            self.logger.exception(error_msg)
            raise FingerprintGenerationError(error_msg, track_data) from e

    def _create_fingerprint_hash(self, track_data: dict[str, Any]) -> str:
        # Validate required properties
        self._validate_track_data(track_data)

        # Extract fingerprint properties with defaults
        fingerprint_data = self._extract_fingerprint_properties(track_data)

        # Create canonical JSON representation
        canonical_json = FingerprintGenerator._create_canonical_representation(fingerprint_data)

        # Generate SHA-256 hash
        hash_object = hashlib.new(self.HASH_ALGORITHM)
        hash_object.update(canonical_json.encode(self.ENCODING))
        fingerprint = hash_object.hexdigest()

        self.logger.debug(
            "Generated fingerprint for track %s: %s",
            track_data.get("persistent_id", "unknown"),
            f"{fingerprint[:16]}...",
        )

        return fingerprint

    def _validate_track_data(self, track_data: Any) -> None:
        """Validate that track data contains required properties.

        Args:
            track_data: Track metadata to validate

        Raises:
            FingerprintGenerationError: If required properties missing
        """
        if not isinstance(track_data, dict):
            error_msg = "Track data must be a dictionary"
            raise FingerprintGenerationError(error_msg)

        if missing_properties := self.REQUIRED_PROPERTIES - set(track_data.keys()):
            error_msg = f"Missing required properties: {missing_properties}"
            raise FingerprintGenerationError(error_msg)

        # Validate persistent_id is not empty (critical for fingerprinting)
        persistent_id = track_data.get("persistent_id")
        if not persistent_id or not str(persistent_id).strip():
            error_msg = "persistent_id cannot be empty"
            raise FingerprintGenerationError(error_msg)

    def _extract_fingerprint_properties(self, track_data: dict[str, Any]) -> dict[str, Any]:
        """Extract and normalize fingerprint properties from track data.

        Args:
            track_data: Raw track metadata

        Returns:
            Dictionary containing normalized fingerprint properties
        """
        fingerprint_data = {
            prop: track_data[prop] for prop in self.REQUIRED_PROPERTIES
        } | {
            prop: track_data.get(prop, default)
            for prop, default in self.OPTIONAL_PROPERTIES.items()
        }
        # Normalize data types for consistent hashing
        return FingerprintGenerator._normalize_property_types(fingerprint_data)

    @staticmethod
    def _normalize_numeric_value(value: Any) -> float:
        """Normalize numeric values for consistent typing.

        Args:
            value: Raw numeric value

        Returns:
            Normalized float value, 0.0 if conversion fails
        """
        try:
            return float(value) if value else 0.0
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _normalize_string_value(value: Any) -> str:
        """Normalize string values for consistent typing.

        Args:
            value: Raw string value

        Returns:
            Normalized string value, empty string if None
        """
        return str(value).strip() if value is not None else ""

    @staticmethod
    def _normalize_property_types(fingerprint_data: dict[str, Any]) -> dict[str, Any]:
        """Normalize property types for consistent fingerprint generation.

        Args:
            fingerprint_data: Raw fingerprint properties

        Returns:
            Normalized fingerprint properties with consistent types
        """
        numeric_fields = {"file_size", "duration"}

        return {
            key: (
                FingerprintGenerator._normalize_numeric_value(value) if key in numeric_fields
                else FingerprintGenerator._normalize_string_value(value)
            )
            for key, value in fingerprint_data.items()
        }

    @staticmethod
    def _create_canonical_representation(fingerprint_data: dict[str, Any]) -> str:
        """Create canonical JSON representation for consistent hashing.

        Args:
            fingerprint_data: Normalized fingerprint properties

        Returns:
            Canonical JSON string suitable for hashing
        """
        # Use sorted keys and minimal separators for consistency
        return json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def validate_fingerprint(fingerprint: Any) -> bool:
        """Validate that a fingerprint has correct format.

        Args:
            fingerprint: Fingerprint value to validate (should be string)

        Returns:
            True if fingerprint is valid SHA-256 hex string, False otherwise
        """
        if not isinstance(fingerprint, str):
            return False

        # SHA-256 produces 64 hex characters
        if len(fingerprint) != 64:
            return False

        # Check if all characters are valid hex
        try:
            int(fingerprint, 16)
            return True
        except ValueError:
            return False

    def fingerprints_match(self, fp1: str, fp2: str) -> bool:
        """Compare two fingerprints for equality.

        Args:
            fp1: First fingerprint
            fp2: Second fingerprint

        Returns:
            True if fingerprints match, False otherwise
        """
        if not self.validate_fingerprint(fp1) or not self.validate_fingerprint(fp2):
            self.logger.warning("Invalid fingerprint format in comparison")
            return False

        return fp1.lower() == fp2.lower()  # Case-insensitive comparison

    def get_fingerprint_summary(self, track_data: dict[str, Any]) -> dict[str, Any]:
        """Get fingerprint along with summary of properties used.

        Useful for debugging and understanding what data contributes to fingerprint.

        Args:
            track_data: Track metadata

        Returns:
            Dictionary containing fingerprint and property summary
        """
        fingerprint = self.generate_track_fingerprint(track_data)
        properties_used = self._extract_fingerprint_properties(track_data)

        return {
            "fingerprint": fingerprint,
            "properties_used": properties_used,
            "property_count": len(properties_used),
            "track_id": track_data.get("persistent_id", "unknown")
        }
