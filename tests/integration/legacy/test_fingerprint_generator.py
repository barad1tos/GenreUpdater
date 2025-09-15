#!/usr/bin/env python3

"""Tests for FingerprintGenerator.

Tests the track fingerprinting functionality for content-based cache invalidation.
Ensures fingerprints are deterministic, stable, and properly detect content changes.

Test Categories:
1. Basic fingerprint generation
2. Fingerprint stability and determinism  
3. Property change detection
4. Error handling and validation
5. Edge cases and malformed data
"""

import pytest
from typing import Dict, Any

from src.services.cache.fingerprint_generator import FingerprintGenerator, FingerprintGenerationError


class TestFingerprintGenerator:
    """Test cases for FingerprintGenerator class."""

    @pytest.fixture
    def generator(self) -> FingerprintGenerator:
        """Create a FingerprintGenerator instance for testing."""
        return FingerprintGenerator()

    @pytest.fixture
    def sample_track_data(self) -> Dict[str, Any]:
        """Sample track data for testing."""
        return {
            "persistent_id": "ABC123DEF456",
            "location": "/Users/test/Music/song.mp3",
            "file_size": 5242880,
            "duration": 240.5,
            "date_modified": "2025-09-11 10:30:00",
            "date_added": "2025-09-10 15:00:00",
            # Non-fingerprint properties (should be ignored)
            "play_count": 42,
            "rating": 5,
            "last_played": "2025-09-11 20:00:00",
            "genre": "Rock"  # This is what we're computing!
        }

    def test_basic_fingerprint_generation(self, generator: FingerprintGenerator, sample_track_data: Dict[str, Any]) -> None:
        """Test basic fingerprint generation with valid data."""
        fingerprint = generator.generate_track_fingerprint(sample_track_data)
        
        # Should return SHA-256 hex string (64 characters)
        assert isinstance(fingerprint, str)
        assert len(fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in fingerprint.lower())
        
    def test_fingerprint_determinism(self, generator: FingerprintGenerator, sample_track_data: Dict[str, Any]) -> None:
        """Test that same input always produces same fingerprint."""
        fingerprint1 = generator.generate_track_fingerprint(sample_track_data)
        fingerprint2 = generator.generate_track_fingerprint(sample_track_data)
        
        assert fingerprint1 == fingerprint2

    def test_fingerprint_stability_across_instances(self, sample_track_data: Dict[str, Any]) -> None:
        """Test that different generator instances produce same fingerprint."""
        generator1 = FingerprintGenerator()
        generator2 = FingerprintGenerator()
        
        fingerprint1 = generator1.generate_track_fingerprint(sample_track_data)
        fingerprint2 = generator2.generate_track_fingerprint(sample_track_data)
        
        assert fingerprint1 == fingerprint2

    def test_fingerprint_ignores_non_critical_properties(self, generator: FingerprintGenerator, sample_track_data: Dict[str, Any]) -> None:
        """Test that non-critical properties don't affect fingerprint."""
        # Generate baseline fingerprint
        baseline_fingerprint = generator.generate_track_fingerprint(sample_track_data)
        
        # Modify non-critical properties
        modified_data = sample_track_data.copy()
        modified_data["play_count"] = 100
        modified_data["rating"] = 1
        modified_data["last_played"] = "2025-09-12 10:00:00"
        modified_data["genre"] = "Jazz"  # Genre change shouldn't affect fingerprint
        
        modified_fingerprint = generator.generate_track_fingerprint(modified_data)
        
        # Fingerprint should remain the same
        assert baseline_fingerprint == modified_fingerprint

    def test_fingerprint_detects_critical_property_changes(self, generator: FingerprintGenerator, sample_track_data: Dict[str, Any]) -> None:
        """Test that changes to critical properties change the fingerprint."""
        baseline_fingerprint = generator.generate_track_fingerprint(sample_track_data)
        
        critical_changes = [
            {"persistent_id": "XYZ789ABC123"},  # Different track
            {"location": "/Users/test/Music/different_song.mp3"},  # File moved
            {"file_size": 6000000},  # File changed
            {"duration": 300.0},  # Duration changed
            {"date_modified": "2025-09-12 10:30:00"},  # File modified
            {"date_added": "2025-09-11 15:00:00"},  # Re-imported
        ]
        
        for change in critical_changes:
            modified_data = sample_track_data.copy()
            modified_data.update(change)
            
            modified_fingerprint = generator.generate_track_fingerprint(modified_data)
            
            # Fingerprint should be different
            assert baseline_fingerprint != modified_fingerprint, f"Change {change} should affect fingerprint"

    def test_required_properties_validation(self, generator: FingerprintGenerator) -> None:
        """Test validation of required properties."""
        # Missing persistent_id
        with pytest.raises(FingerprintGenerationError, match="Missing required properties"):
            generator.generate_track_fingerprint({"location": "/path/to/song.mp3"})
        
        # Missing location
        with pytest.raises(FingerprintGenerationError, match="Missing required properties"):
            generator.generate_track_fingerprint({"persistent_id": "ABC123"})
        
        # Empty persistent_id
        with pytest.raises(FingerprintGenerationError, match="persistent_id cannot be empty"):
            generator.generate_track_fingerprint({
                "persistent_id": "",
                "location": "/path/to/song.mp3"
            })
        
        # Whitespace-only persistent_id
        with pytest.raises(FingerprintGenerationError, match="persistent_id cannot be empty"):
            generator.generate_track_fingerprint({
                "persistent_id": "   ",
                "location": "/path/to/song.mp3"
            })

    def test_minimal_valid_data(self, generator: FingerprintGenerator) -> None:
        """Test fingerprint generation with minimal valid data."""
        minimal_data = {
            "persistent_id": "ABC123",
            "location": "/path/to/song.mp3"
        }
        
        fingerprint = generator.generate_track_fingerprint(minimal_data)
        
        # Should work and produce valid fingerprint
        assert isinstance(fingerprint, str)
        assert len(fingerprint) == 64

    def test_optional_properties_defaults(self, generator: FingerprintGenerator) -> None:
        """Test that optional properties get proper defaults."""
        data_without_optionals = {
            "persistent_id": "ABC123",
            "location": "/path/to/song.mp3"
        }
        
        data_with_explicit_defaults = {
            "persistent_id": "ABC123",
            "location": "/path/to/song.mp3",
            "file_size": 0,
            "duration": 0,
            "date_modified": "",
            "date_added": "",
        }
        
        fingerprint1 = generator.generate_track_fingerprint(data_without_optionals)
        fingerprint2 = generator.generate_track_fingerprint(data_with_explicit_defaults)
        
        # Should produce same fingerprint
        assert fingerprint1 == fingerprint2

    def test_type_normalization(self, generator: FingerprintGenerator) -> None:
        """Test that property types are normalized correctly."""
        # String numbers should be converted to float
        data_with_string_numbers = {
            "persistent_id": "ABC123",
            "location": "/path/to/song.mp3",
            "file_size": "5242880",
            "duration": "240.5",
        }
        
        data_with_numeric_types = {
            "persistent_id": "ABC123", 
            "location": "/path/to/song.mp3",
            "file_size": 5242880,
            "duration": 240.5,
        }
        
        fingerprint1 = generator.generate_track_fingerprint(data_with_string_numbers)
        fingerprint2 = generator.generate_track_fingerprint(data_with_numeric_types)
        
        # Should produce same fingerprint after normalization
        assert fingerprint1 == fingerprint2

    def test_invalid_input_types(self, generator: FingerprintGenerator) -> None:
        """Test error handling for invalid input types."""
        # None input
        with pytest.raises(FingerprintGenerationError, match="Track data must be a dictionary"):
            generator.generate_track_fingerprint(None)
        
        # String input
        with pytest.raises(FingerprintGenerationError, match="Track data must be a dictionary"):
            generator.generate_track_fingerprint("not a dict")
        
        # List input  
        with pytest.raises(FingerprintGenerationError, match="Track data must be a dictionary"):
            generator.generate_track_fingerprint(["not", "a", "dict"])

    def test_fingerprint_validation(self, generator: FingerprintGenerator) -> None:
        """Test fingerprint validation functionality."""
        # Valid SHA-256 fingerprint
        valid_fp = "a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456"
        assert generator.validate_fingerprint(valid_fp) is True
        
        # Invalid length
        assert generator.validate_fingerprint("abc123") is False
        assert generator.validate_fingerprint("a" * 65) is False
        
        # Invalid characters
        invalid_fp = "g1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456"
        assert generator.validate_fingerprint(invalid_fp) is False
        
        # Wrong type
        assert generator.validate_fingerprint(123) is False
        assert generator.validate_fingerprint(None) is False

    def test_fingerprint_comparison(self, generator: FingerprintGenerator) -> None:
        """Test fingerprint comparison functionality."""
        fp1 = "a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456"
        fp2 = "A1B2C3D4E5F6789012345678901234567890ABCDEF1234567890ABCDEF123456"  # Same but uppercase
        fp3 = "b1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456"  # Different
        
        # Case-insensitive match
        assert generator.fingerprints_match(fp1, fp2) is True
        
        # Different fingerprints
        assert generator.fingerprints_match(fp1, fp3) is False
        
        # Invalid fingerprints
        assert generator.fingerprints_match(fp1, "invalid") is False

    def test_fingerprint_summary(self, generator: FingerprintGenerator, sample_track_data: Dict[str, Any]) -> None:
        """Test fingerprint summary functionality."""
        summary = generator.get_fingerprint_summary(sample_track_data)
        
        assert "fingerprint" in summary
        assert "properties_used" in summary
        assert "property_count" in summary
        assert "track_id" in summary
        
        # Fingerprint should be valid
        assert generator.validate_fingerprint(summary["fingerprint"])
        
        # Should include expected properties
        expected_props = {"persistent_id", "location", "file_size", "duration", "date_modified", "date_added"}
        assert set(summary["properties_used"].keys()) == expected_props
        
        # Property count should match
        assert summary["property_count"] == len(expected_props)
        
        # Track ID should match
        assert summary["track_id"] == sample_track_data["persistent_id"]

    def test_unicode_handling(self, generator: FingerprintGenerator) -> None:
        """Test handling of Unicode characters in track data."""
        unicode_data = {
            "persistent_id": "ABC123",
            "location": "/Users/test/Музыка/песня.mp3",  # Cyrillic
            "date_modified": "2025-09-11 10:30:00",
        }
        
        # Should handle Unicode without errors
        fingerprint = generator.generate_track_fingerprint(unicode_data)
        assert isinstance(fingerprint, str)
        assert len(fingerprint) == 64

    def test_null_and_empty_values(self, generator: FingerprintGenerator) -> None:
        """Test handling of null and empty values."""
        data_with_nulls = {
            "persistent_id": "ABC123",
            "location": "/path/to/song.mp3",
            "file_size": None,
            "duration": None,
            "date_modified": None,
            "date_added": "",
        }
        
        # Should handle nulls gracefully with defaults
        fingerprint = generator.generate_track_fingerprint(data_with_nulls)
        assert isinstance(fingerprint, str)
        assert len(fingerprint) == 64
import pytest
pytestmark = pytest.mark.integration
