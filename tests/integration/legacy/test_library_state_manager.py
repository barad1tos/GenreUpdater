#!/usr/bin/env python3

"""Tests for LibraryStateManager.

Tests the library state management functionality for content-based cache invalidation.
Ensures state persistence, change detection, and corruption handling work correctly.

Test Categories:
1. State generation and persistence
2. Change detection and comparison
3. File operations and atomic saves
4. Corruption detection and recovery
5. Performance and caching behavior
"""

import json
import pytest
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock

from src.services.cache.fingerprint_generator import FingerprintGenerator
from src.services.cache.library_state_manager import (
    LibraryStateManager, 
    LibraryStateError, 
    ChangeSet
)


class TestLibraryStateManager:
    """Test cases for LibraryStateManager class."""

    @pytest.fixture
    def temp_cache_dir(self) -> Path:
        """Create temporary directory for state files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def fingerprint_generator(self) -> FingerprintGenerator:
        """Create FingerprintGenerator for testing."""
        return FingerprintGenerator()

    @pytest.fixture
    def state_manager(self, temp_cache_dir: Path, fingerprint_generator: FingerprintGenerator) -> LibraryStateManager:
        """Create LibraryStateManager instance for testing."""
        return LibraryStateManager(temp_cache_dir, fingerprint_generator)

    @pytest.fixture
    def sample_track_list(self) -> List[Dict[str, Any]]:
        """Sample track metadata list for testing."""
        return [
            {
                "persistent_id": "track_1",
                "location": "/path/to/song1.mp3",
                "file_size": 5000000,
                "duration": 240.0,
                "date_modified": "2025-09-11 10:00:00",
                "date_added": "2025-09-10 15:00:00"
            },
            {
                "persistent_id": "track_2", 
                "location": "/path/to/song2.mp3",
                "file_size": 4500000,
                "duration": 210.5,
                "date_modified": "2025-09-11 09:30:00", 
                "date_added": "2025-09-10 14:30:00"
            },
            {
                "persistent_id": "track_3",
                "location": "/path/to/song3.mp3",
                "file_size": 6000000,
                "duration": 300.0,
                "date_modified": "2025-09-11 11:00:00",
                "date_added": "2025-09-10 16:00:00"
            }
        ]

    def test_state_directory_creation(self, temp_cache_dir: Path, fingerprint_generator: FingerprintGenerator) -> None:
        """Test that state manager creates cache directory."""
        non_existent_dir = temp_cache_dir / "new_cache_dir"
        assert not non_existent_dir.exists()
        
        state_manager = LibraryStateManager(non_existent_dir, fingerprint_generator)
        
        assert non_existent_dir.exists()
        assert non_existent_dir.is_dir()

    def test_current_library_state_generation(self, state_manager: LibraryStateManager, sample_track_list: List[Dict[str, Any]]) -> None:
        """Test generation of current library state from track metadata."""
        library_state = state_manager.get_current_library_state(sample_track_list)
        
        # Should return fingerprint for each track
        assert len(library_state) == 3
        assert "track_1" in library_state
        assert "track_2" in library_state
        assert "track_3" in library_state
        
        # All fingerprints should be valid SHA-256 hashes
        for track_id, fingerprint in library_state.items():
            assert isinstance(fingerprint, str)
            assert len(fingerprint) == 64
            assert all(c in "0123456789abcdef" for c in fingerprint.lower())

    def test_state_persistence(self, state_manager: LibraryStateManager, sample_track_list: List[Dict[str, Any]]) -> None:
        """Test saving and loading library state."""
        # Generate and save state
        library_state = state_manager.get_current_library_state(sample_track_list)
        state_manager.save_state(library_state, "/path/to/library")
        
        # Load state back
        loaded_state = state_manager.get_cached_state()
        
        assert loaded_state == library_state
        
        # Check state file exists
        assert state_manager.state_file.exists()
        
        # Verify file format
        with state_manager.state_file.open('r') as f:
            state_data = json.load(f)
        
        assert "timestamp" in state_data
        assert "library_path" in state_data
        assert "track_fingerprints" in state_data
        assert "track_count" in state_data
        assert state_data["library_path"] == "/path/to/library"
        assert state_data["track_count"] == 3

    def test_empty_state_handling(self, state_manager: LibraryStateManager) -> None:
        """Test handling of empty states."""
        # No state file exists initially
        cached_state = state_manager.get_cached_state()
        assert cached_state == {}
        
        # Save empty state
        state_manager.save_state({})
        loaded_state = state_manager.get_cached_state()
        assert loaded_state == {}

    def test_change_detection_no_changes(self, state_manager: LibraryStateManager) -> None:
        """Test change detection when no changes occurred."""
        old_state = {"track_1": "fingerprint_1", "track_2": "fingerprint_2"}
        new_state = {"track_1": "fingerprint_1", "track_2": "fingerprint_2"}
        
        changes = state_manager.detect_changes(old_state, new_state)
        
        assert len(changes.deleted) == 0
        assert len(changes.added) == 0
        assert len(changes.modified) == 0

    def test_change_detection_added_tracks(self, state_manager: LibraryStateManager) -> None:
        """Test detection of added tracks."""
        old_state = {"track_1": "fingerprint_1"}
        new_state = {"track_1": "fingerprint_1", "track_2": "fingerprint_2", "track_3": "fingerprint_3"}
        
        changes = state_manager.detect_changes(old_state, new_state)
        
        assert len(changes.deleted) == 0
        assert changes.added == {"track_2", "track_3"}
        assert len(changes.modified) == 0

    def test_change_detection_deleted_tracks(self, state_manager: LibraryStateManager) -> None:
        """Test detection of deleted tracks."""
        old_state = {"track_1": "fingerprint_1", "track_2": "fingerprint_2", "track_3": "fingerprint_3"}
        new_state = {"track_1": "fingerprint_1"}
        
        changes = state_manager.detect_changes(old_state, new_state)
        
        assert changes.deleted == {"track_2", "track_3"}
        assert len(changes.added) == 0
        assert len(changes.modified) == 0

    def test_change_detection_modified_tracks(self, state_manager: LibraryStateManager) -> None:
        """Test detection of modified tracks (fingerprint changes)."""
        old_state = {"track_1": "old_fingerprint_1", "track_2": "fingerprint_2"}
        new_state = {"track_1": "new_fingerprint_1", "track_2": "fingerprint_2"}
        
        changes = state_manager.detect_changes(old_state, new_state)
        
        assert len(changes.deleted) == 0
        assert len(changes.added) == 0
        assert changes.modified == {"track_1"}

    def test_change_detection_mixed_changes(self, state_manager: LibraryStateManager) -> None:
        """Test detection of mixed changes (added, deleted, modified)."""
        old_state = {
            "track_1": "old_fingerprint_1",  # Will be modified
            "track_2": "fingerprint_2",      # Will be deleted
            "track_3": "fingerprint_3"       # Unchanged
        }
        new_state = {
            "track_1": "new_fingerprint_1",  # Modified
            "track_3": "fingerprint_3",      # Unchanged
            "track_4": "fingerprint_4"       # Added
        }
        
        changes = state_manager.detect_changes(old_state, new_state)
        
        assert changes.deleted == {"track_2"}
        assert changes.added == {"track_4"}
        assert changes.modified == {"track_1"}

    def test_atomic_save_operation(self, state_manager: LibraryStateManager) -> None:
        """Test that state saves are atomic (temp file + rename)."""
        library_state = {"track_1": "fingerprint_1"}
        
        # Save state
        state_manager.save_state(library_state)
        
        # State file should exist, temp file should not
        assert state_manager.state_file.exists()
        temp_file = state_manager.state_file.with_suffix('.tmp')
        assert not temp_file.exists()
        
        # Content should be correct
        loaded_state = state_manager.get_cached_state()
        assert loaded_state == library_state

    def test_backup_creation(self, state_manager: LibraryStateManager) -> None:
        """Test that backup is created when overwriting existing state."""
        # Save initial state
        initial_state = {"track_1": "fingerprint_1"}
        state_manager.save_state(initial_state)
        
        # Save new state (should create backup)
        new_state = {"track_1": "new_fingerprint_1", "track_2": "fingerprint_2"}
        state_manager.save_state(new_state)
        
        # Backup should exist
        assert state_manager.backup_file.exists()
        
        # Current state should be new state
        current_state = state_manager.get_cached_state()
        assert current_state == new_state

    def test_corruption_detection_high_change_rate(self, state_manager: LibraryStateManager) -> None:
        """Test detection of possible library corruption (high change rate)."""
        # Create large old state
        old_state = {f"track_{i}": f"fingerprint_{i}" for i in range(100)}
        
        # Create completely different new state (simulating corruption)
        new_state = {f"new_track_{i}": f"new_fingerprint_{i}" for i in range(100)}
        
        # Should raise LibraryStateError due to high change rate
        with pytest.raises(LibraryStateError, match="Possible library corruption"):
            state_manager.detect_changes(old_state, new_state)

    def test_corruption_detection_normal_changes(self, state_manager: LibraryStateManager) -> None:
        """Test that normal library changes don't trigger corruption detection."""
        # Create old state with 100 tracks
        old_state = {f"track_{i}": f"fingerprint_{i}" for i in range(100)}
        
        # Modify small number of tracks (normal operation)
        new_state = old_state.copy()
        new_state["track_1"] = "modified_fingerprint_1"
        new_state["track_2"] = "modified_fingerprint_2"
        del new_state["track_99"]  # Delete one track
        new_state["new_track_100"] = "new_fingerprint_100"  # Add one track
        
        # Should not raise corruption error
        changes = state_manager.detect_changes(old_state, new_state)
        
        assert len(changes.modified) == 2
        assert len(changes.deleted) == 1
        assert len(changes.added) == 1

    def test_refresh_needed_no_cached_state(self, state_manager: LibraryStateManager) -> None:
        """Test that refresh is needed when no cached state exists."""
        assert state_manager.should_refresh_library_state() is True

    def test_refresh_needed_library_modified(self, state_manager: LibraryStateManager, sample_track_list: List[Dict[str, Any]]) -> None:
        """Test that refresh is needed when library was modified."""
        # Save initial state with timestamp 1000
        library_state = state_manager.get_current_library_state(sample_track_list)
        state_manager.save_state(library_state)
        state_manager._cached_timestamp = 1000.0
        
        # Check with newer modification time
        assert state_manager.should_refresh_library_state(library_modification_time=2000.0) is True
        
        # Check with older modification time
        assert state_manager.should_refresh_library_state(library_modification_time=500.0) is False

    def test_state_caching(self, state_manager: LibraryStateManager, sample_track_list: List[Dict[str, Any]]) -> None:
        """Test that state is cached in memory for performance."""
        # Generate and save state
        library_state = state_manager.get_current_library_state(sample_track_list)
        state_manager.save_state(library_state)
        
        # First load should read from disk
        cached_state = state_manager.get_cached_state()
        assert cached_state == library_state
        
        # Second load should use memory cache (remove file to verify)
        state_manager.state_file.unlink()
        cached_state_2 = state_manager.get_cached_state()
        assert cached_state_2 == library_state  # Should still work from memory

    def test_invalid_track_data_handling(self, state_manager: LibraryStateManager) -> None:
        """Test handling of invalid track data during state generation with low failure rate."""
        # Create list with 10% invalid tracks (below 10% failure threshold)
        invalid_track_list = [
            {"persistent_id": "track_1", "location": "/path/to/song1.mp3"},  # Valid
            {"persistent_id": "track_2", "location": "/path/to/song2.mp3"},  # Valid
            {"persistent_id": "track_3", "location": "/path/to/song3.mp3"},  # Valid
            {"persistent_id": "track_4", "location": "/path/to/song4.mp3"},  # Valid
            {"persistent_id": "track_5", "location": "/path/to/song5.mp3"},  # Valid
            {"persistent_id": "track_6", "location": "/path/to/song6.mp3"},  # Valid
            {"persistent_id": "track_7", "location": "/path/to/song7.mp3"},  # Valid
            {"persistent_id": "track_8", "location": "/path/to/song8.mp3"},  # Valid
            {"persistent_id": "track_9", "location": "/path/to/song9.mp3"},  # Valid
            {"location": "/path/to/song10.mp3"},  # Missing persistent_id (1 invalid out of 10 = 10%)
        ]
        
        # Should generate state for valid tracks only (90% success rate, at 10% failure threshold)
        library_state = state_manager.get_current_library_state(invalid_track_list)
        
        assert len(library_state) == 9  # Only 9 valid tracks
        assert "track_1" in library_state
        assert "track_9" in library_state

    def test_high_failure_rate_error(self, state_manager: LibraryStateManager) -> None:
        """Test that high fingerprint failure rate raises error."""
        # Create track list where most tracks will fail fingerprinting
        invalid_track_list = [
            {"location": "/path/to/song.mp3"}  # Missing persistent_id
            for _ in range(10)
        ]
        
        # Should raise LibraryStateError due to high failure rate
        with pytest.raises(LibraryStateError, match="High fingerprint failure rate"):
            state_manager.get_current_library_state(invalid_track_list)

    def test_state_summary(self, state_manager: LibraryStateManager, sample_track_list: List[Dict[str, Any]]) -> None:
        """Test state summary functionality."""
        # Generate and save state
        library_state = state_manager.get_current_library_state(sample_track_list)
        state_manager.save_state(library_state)
        
        summary = state_manager.get_state_summary()
        
        assert summary["track_count"] == 3
        assert summary["state_file_exists"] is True
        assert summary["backup_file_exists"] is False  # No backup yet
        assert summary["last_update"] is not None
        assert "cache_directory" in summary
        assert summary["state_file_size"] > 0

    def test_corrupted_state_file_recovery(self, state_manager: LibraryStateManager, sample_track_list: List[Dict[str, Any]]) -> None:
        """Test recovery from corrupted state file."""
        # Save valid state first
        library_state = state_manager.get_current_library_state(sample_track_list)
        state_manager.save_state(library_state)
        
        # Save another state to create backup
        modified_state = library_state.copy()
        modified_state["track_4"] = "fingerprint_4"
        state_manager.save_state(modified_state)
        
        # Corrupt the main state file
        with state_manager.state_file.open('w') as f:
            f.write("invalid json content")
        
        # Clear memory cache to force file reload
        state_manager._cached_state = None
        
        # Should recover from backup
        recovered_state = state_manager.get_cached_state()
        assert recovered_state == library_state  # Should match original backup

    def test_no_backup_available(self, state_manager: LibraryStateManager) -> None:
        """Test behavior when no backup is available for recovery."""
        # Corrupt main file without backup
        state_manager.state_file.write_text("invalid json")
        
        # Should return empty state
        state = state_manager.get_cached_state()
        assert state == {}
        
        # Recovery should fail
        recovery_result = state_manager.recover_from_backup()
        assert recovery_result is False
import pytest
pytestmark = pytest.mark.integration
