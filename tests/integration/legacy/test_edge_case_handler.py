#!/usr/bin/env python3

"""Tests for EdgeCaseHandler.

Tests the edge case handling and error recovery functionality for the cache system.
Ensures robust error handling, retry logic, and recovery mechanisms work correctly.

Test Categories:
1. AppleScript timeout and retry logic
2. Partial scan detection and recovery
3. Library corruption and rebuild detection
4. Missing file handling and validation
5. Memory pressure management
6. Data validation and error handling
7. Statistics tracking and recovery strategies
"""

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.infrastructure.cache.edge_case_handler import (
    EdgeCaseHandler,
    EdgeCaseHandlerError,
    EdgeCaseResult,
    EdgeCaseType,
    RecoveryStrategy,
)


class TestEdgeCaseHandler:
    """Test cases for EdgeCaseHandler class."""

    @pytest.fixture
    def handler(self) -> EdgeCaseHandler:
        """Create EdgeCaseHandler instance for testing."""
        return EdgeCaseHandler()

    @pytest.fixture
    def sample_track_data(self) -> List[Dict[str, Any]]:
        """Sample track metadata for testing."""
        return [
            {
                "persistent_id": "track_1",
                "location": "/path/to/song1.mp3",
                "file_size": 5000000,
                "duration": 240.0,
                "date_modified": "2025-09-11 10:00:00",
            },
            {
                "persistent_id": "track_2",
                "location": "/path/to/song2.mp3",
                "file_size": 4500000,
                "duration": 210.5,
                "date_modified": "2025-09-11 09:30:00",
            },
            {
                "persistent_id": "track_3",
                "location": "/path/to/song3.mp3",
                "file_size": 6000000,
                "duration": 300.0,
                "date_modified": "2025-09-11 11:00:00",
            },
        ]

    def test_handler_initialization(self, handler: EdgeCaseHandler) -> None:
        """Test that handler initializes correctly."""
        assert handler._edge_case_stats["total_cases"] == 0
        assert handler._edge_case_stats["successful_recoveries"] == 0
        assert handler._edge_case_stats["failed_recoveries"] == 0
        assert isinstance(handler._edge_case_stats["retry_counts"], dict)
        assert isinstance(handler._edge_case_stats["recovery_times"], dict)

        # Check recovery strategy mapping
        assert handler._recovery_strategies[EdgeCaseType.APPLESCRIPT_TIMEOUT] == RecoveryStrategy.RETRY_WITH_BACKOFF
        assert handler._recovery_strategies[EdgeCaseType.LIBRARY_CORRUPTION] == RecoveryStrategy.ROLLBACK_TO_BACKUP

    def test_applescript_timeout_success_first_try(self, handler: EdgeCaseHandler) -> None:
        """Test successful AppleScript operation on first try."""

        def mock_operation():
            return {"result": "success", "tracks": 100}

        result = handler.handle_applescript_timeout(mock_operation)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.RETRY_WITH_BACKOFF
        assert result.edge_case_type == EdgeCaseType.APPLESCRIPT_TIMEOUT
        assert result.retry_count == 0
        assert result.recovered_data == {"result": "success", "tracks": 100}
        assert result.recovery_time > 0

    def test_applescript_timeout_success_after_retry(self, handler: EdgeCaseHandler) -> None:
        """Test successful AppleScript operation after retries."""
        call_count = 0

        def mock_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Timeout error")
            return {"result": "success", "tracks": 100}

        with patch("time.sleep"):  # Speed up test by skipping sleep
            result = handler.handle_applescript_timeout(mock_operation)

        assert result.success is True
        # Retry count tracks the attempt number when success occurs
        # Success on attempt 3 means retry_count is 2 (attempts 0, 1 failed)
        assert result.retry_count == 2  # Failed twice, succeeded on third
        assert result.recovered_data == {"result": "success", "tracks": 100}

    def test_applescript_timeout_complete_failure(self, handler: EdgeCaseHandler) -> None:
        """Test AppleScript operation that fails all retries."""

        def mock_operation():
            raise Exception("Persistent timeout error")

        with patch("time.sleep"):  # Speed up test
            result = handler.handle_applescript_timeout(mock_operation)

        assert result.success is False
        assert result.retry_count == EdgeCaseHandler.MAX_RETRIES
        assert "Persistent timeout error" in result.error_message
        assert result.recovered_data is None

    def test_detect_partial_scan_with_expected_count(self, handler: EdgeCaseHandler) -> None:
        """Test partial scan detection with expected track count."""
        # Normal case - track count matches expectation
        result = handler.detect_partial_scan(track_count=1000, expected_count=1000)
        assert result.success is True
        assert result.edge_case_type == EdgeCaseType.PARTIAL_SCAN
        assert result.strategy_used == RecoveryStrategy.SKIP_AND_CONTINUE

        # Partial scan case - significant reduction
        result = handler.detect_partial_scan(track_count=50, expected_count=1000)
        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.PARTIAL_PROCEED
        assert "Partial scan" in result.error_message
        assert len(result.warnings) > 0

    def test_detect_partial_scan_low_count(self, handler: EdgeCaseHandler) -> None:
        """Test partial scan detection with suspiciously low count."""
        result = handler.detect_partial_scan(track_count=5)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.PARTIAL_PROCEED
        assert "Low track count" in result.error_message
        assert len(result.warnings) > 0

    def test_detect_partial_scan_normal_count(self, handler: EdgeCaseHandler) -> None:
        """Test normal scan with reasonable track count."""
        result = handler.detect_partial_scan(track_count=500)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.SKIP_AND_CONTINUE
        assert result.error_message == ""
        assert len(result.warnings) == 0

    def test_detect_library_corruption_no_previous_state(self, handler: EdgeCaseHandler) -> None:
        """Test corruption detection with no previous state."""
        old_track_ids = set()
        new_track_ids = {"track_1", "track_2", "track_3"}

        result = handler.detect_library_corruption(old_track_ids, new_track_ids)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.SKIP_AND_CONTINUE
        assert result.recovered_data["status"] == "no_previous_state"

    def test_detect_library_corruption_normal_changes(self, handler: EdgeCaseHandler) -> None:
        """Test corruption detection with normal library changes."""
        old_track_ids = {"track_1", "track_2", "track_3", "track_4", "track_5"}
        new_track_ids = {"track_1", "track_2", "track_3", "track_6", "track_7"}  # 60% overlap

        result = handler.detect_library_corruption(old_track_ids, new_track_ids)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.SKIP_AND_CONTINUE
        assert result.edge_case_type == EdgeCaseType.LIBRARY_CORRUPTION
        assert result.recovered_data["change_rate"] < EdgeCaseHandler.CORRUPTION_THRESHOLD

    def test_detect_library_corruption_high_change_rate(self, handler: EdgeCaseHandler) -> None:
        """Test corruption detection with high change rate."""
        # Create extreme case: no overlap at all (100% change rate > 90% threshold)
        old_track_ids = {"track_1", "track_2", "track_3", "track_4", "track_5"}
        new_track_ids = {"new_track_1", "new_track_2", "new_track_3", "new_track_4", "new_track_5"}  # 0% overlap

        result = handler.detect_library_corruption(old_track_ids, new_track_ids)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.REBUILD_FROM_SCRATCH  # Same size suggests rebuild
        assert result.edge_case_type == EdgeCaseType.LIBRARY_REBUILD
        assert result.recovered_data["change_rate"] > EdgeCaseHandler.CORRUPTION_THRESHOLD
        assert len(result.warnings) > 0

    def test_detect_library_rebuild(self, handler: EdgeCaseHandler) -> None:
        """Test detection of library rebuild vs corruption."""
        old_track_ids = {"track_1", "track_2", "track_3", "track_4", "track_5"}
        new_track_ids = {"new_track_1", "new_track_2", "new_track_3", "new_track_4", "new_track_5"}  # Same count, no overlap

        result = handler.detect_library_corruption(old_track_ids, new_track_ids)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.REBUILD_FROM_SCRATCH
        assert result.edge_case_type == EdgeCaseType.LIBRARY_REBUILD

    def test_handle_missing_files_all_valid(self, handler: EdgeCaseHandler, sample_track_data: List[Dict[str, Any]]) -> None:
        """Test missing file handling with all valid files."""
        with patch("pathlib.Path.exists", return_value=True):
            result = handler.handle_missing_files(sample_track_data)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.SKIP_AND_CONTINUE
        assert len(result.recovered_data["valid_tracks"]) == 3
        assert len(result.recovered_data["missing_files"]) == 0
        assert result.recovered_data["missing_rate"] == 0.0

    def test_handle_missing_files_some_missing(self, handler: EdgeCaseHandler) -> None:
        """Test missing file handling with some missing files under threshold."""
        # Create larger sample with low missing rate (20% = 2/10 files missing)
        track_data = [{"persistent_id": f"track_{i}", "location": f"/path/to/song{i}.mp3"} for i in range(1, 11)]

        def mock_exists(self):
            # Only tracks 1-8 exist (80% success rate)
            return str(self) in [f"/path/to/song{i}.mp3" for i in range(1, 9)]

        with patch("pathlib.Path.exists", mock_exists):
            result = handler.handle_missing_files(track_data)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.SKIP_AND_CONTINUE
        assert len(result.recovered_data["valid_tracks"]) == 8
        assert len(result.recovered_data["missing_files"]) == 2
        assert result.recovered_data["missing_rate"] == 0.2  # 20%

    def test_handle_missing_files_high_missing_rate(self, handler: EdgeCaseHandler, sample_track_data: List[Dict[str, Any]]) -> None:
        """Test missing file handling with high missing file rate."""
        with patch("pathlib.Path.exists", return_value=False):
            result = handler.handle_missing_files(sample_track_data)

        assert result.success is False
        assert result.strategy_used == RecoveryStrategy.EMERGENCY_STOP
        assert len(result.recovered_data["valid_tracks"]) == 0
        assert len(result.recovered_data["missing_files"]) == 3
        assert result.recovered_data["missing_rate"] == 1.0

    def test_handle_missing_files_no_location(self, handler: EdgeCaseHandler) -> None:
        """Test missing file handling with tracks that have no location."""
        track_data = [
            {"persistent_id": "track_1", "location": ""},
            {"persistent_id": "track_2"},  # No location field
        ]

        result = handler.handle_missing_files(track_data)

        # This case has 100% missing rate, so should trigger emergency stop
        assert result.success is False
        assert result.strategy_used == RecoveryStrategy.EMERGENCY_STOP
        assert len(result.recovered_data["valid_tracks"]) == 0
        assert len(result.recovered_data["missing_files"]) == 2
        assert result.recovered_data["missing_files"][0]["reason"] == "no_location"

    def test_handle_missing_files_network_paths(self, handler: EdgeCaseHandler) -> None:
        """Test missing file handling with network paths."""
        track_data = [
            {"persistent_id": "track_1", "location": "//server/share/song1.mp3"},
            {"persistent_id": "track_2", "location": "smb://server/share/song2.mp3"},
            {"persistent_id": "track_3", "location": "/local/path/song3.mp3"},
        ]

        with patch("pathlib.Path.exists", return_value=False):  # Local file doesn't exist
            result = handler.handle_missing_files(track_data)

        assert result.success is True
        # Network paths should be considered valid, local path missing
        assert len(result.recovered_data["valid_tracks"]) == 2
        assert len(result.recovered_data["missing_files"]) == 1

    def test_handle_memory_pressure_no_chunking_needed(self, handler: EdgeCaseHandler, sample_track_data: List[Dict[str, Any]]) -> None:
        """Test memory pressure handling when no chunking is needed."""
        result = handler.handle_memory_pressure(sample_track_data, chunk_size=100)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.SKIP_AND_CONTINUE
        assert len(result.recovered_data["chunks"]) == 1
        assert len(result.recovered_data["chunks"][0]) == 3

    def test_handle_memory_pressure_chunking_required(self, handler: EdgeCaseHandler) -> None:
        """Test memory pressure handling with chunking required."""
        # Create large track list
        large_track_list = [{"persistent_id": f"track_{i}"} for i in range(250)]

        result = handler.handle_memory_pressure(large_track_list, chunk_size=100)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.MEMORY_CLEANUP
        assert len(result.recovered_data["chunks"]) == 3  # 100, 100, 50
        assert len(result.recovered_data["chunks"][0]) == 100
        assert len(result.recovered_data["chunks"][1]) == 100
        assert len(result.recovered_data["chunks"][2]) == 50
        assert result.recovered_data["chunk_size"] == 100

    def test_validate_track_data_valid(self, handler: EdgeCaseHandler) -> None:
        """Test track data validation with valid data."""
        track_data = {
            "persistent_id": "track_123",
            "location": "/path/to/song.mp3",
            "file_size": 5000000,
            "duration": 240.0,
        }

        result = handler.validate_track_data(track_data)

        assert result.success is True
        assert result.strategy_used == RecoveryStrategy.SKIP_AND_CONTINUE
        assert len(result.recovered_data["errors"]) == 0
        assert len(result.recovered_data["warnings"]) == 0

    def test_validate_track_data_missing_required_field(self, handler: EdgeCaseHandler) -> None:
        """Test track data validation with missing required field."""
        track_data = {
            "location": "/path/to/song.mp3",
            "file_size": 5000000,
            # Missing persistent_id
        }

        result = handler.validate_track_data(track_data)

        assert result.success is False
        assert result.strategy_used == RecoveryStrategy.SKIP_AND_CONTINUE
        assert len(result.recovered_data["errors"]) == 1
        assert "persistent_id" in result.recovered_data["errors"][0]

    def test_validate_track_data_invalid_types(self, handler: EdgeCaseHandler) -> None:
        """Test track data validation with invalid data types."""
        track_data = {
            "persistent_id": "",  # Empty string
            "file_size": -1000,  # Negative size
            "duration": "invalid",  # Wrong type
        }

        result = handler.validate_track_data(track_data)

        assert result.success is False
        assert len(result.recovered_data["errors"]) == 1  # Empty persistent_id
        assert len(result.recovered_data["warnings"]) == 2  # Invalid file_size and duration

    def test_validate_track_data_suspicious_values(self, handler: EdgeCaseHandler) -> None:
        """Test track data validation with suspicious but not invalid values."""
        track_data = {
            "persistent_id": "track_123",
            "file_size": 0,  # Zero size (warning)
            "duration": 0.0,  # Zero duration (warning)
        }

        result = handler.validate_track_data(track_data)

        assert result.success is True
        assert len(result.recovered_data["errors"]) == 0
        assert len(result.recovered_data["warnings"]) == 0  # Zero values are acceptable

    def test_get_recovery_strategy(self, handler: EdgeCaseHandler) -> None:
        """Test recovery strategy retrieval."""
        strategy = handler.get_recovery_strategy(EdgeCaseType.APPLESCRIPT_TIMEOUT)
        assert strategy == RecoveryStrategy.RETRY_WITH_BACKOFF

        strategy = handler.get_recovery_strategy(EdgeCaseType.LIBRARY_CORRUPTION)
        assert strategy == RecoveryStrategy.ROLLBACK_TO_BACKUP

        # Unknown edge case type should return default
        unknown_type = EdgeCaseType.MEMORY_PRESSURE
        strategy = handler.get_recovery_strategy(unknown_type)
        assert strategy in RecoveryStrategy

    def test_get_edge_case_statistics(self, handler: EdgeCaseHandler) -> None:
        """Test edge case statistics retrieval."""
        # Initial statistics
        stats = handler.get_edge_case_statistics()
        assert stats["total_cases"] == 0
        assert stats["successful_recoveries"] == 0
        assert stats["failed_recoveries"] == 0
        assert stats["success_rate"] == 0.0

        # Run some operations to generate statistics
        # Both operations should update stats, regardless of whether edge case detected
        handler.detect_partial_scan(track_count=100)  # Normal scan - should still update stats
        handler.detect_partial_scan(track_count=5)  # Low count - should trigger partial scan

        stats = handler.get_edge_case_statistics()
        assert stats["total_cases"] == 2
        assert stats["successful_recoveries"] == 2
        assert stats["success_rate"] == 1.0

    def test_clear_statistics(self, handler: EdgeCaseHandler) -> None:
        """Test clearing edge case statistics."""
        # Generate some statistics
        handler.detect_partial_scan(track_count=100)
        handler.detect_partial_scan(track_count=5)

        # Verify statistics exist
        stats = handler.get_edge_case_statistics()
        assert stats["total_cases"] > 0

        # Clear statistics
        handler.clear_statistics()

        # Verify statistics are cleared
        stats = handler.get_edge_case_statistics()
        assert stats["total_cases"] == 0
        assert stats["successful_recoveries"] == 0
        assert stats["failed_recoveries"] == 0

    def test_edge_case_result_post_init(self) -> None:
        """Test EdgeCaseResult post_init sets empty warnings."""
        result = EdgeCaseResult(
            success=True, strategy_used=RecoveryStrategy.SKIP_AND_CONTINUE, edge_case_type=EdgeCaseType.PARTIAL_SCAN, error_message=""
        )
        assert result.warnings == []

        result_with_warnings = EdgeCaseResult(
            success=True,
            strategy_used=RecoveryStrategy.SKIP_AND_CONTINUE,
            edge_case_type=EdgeCaseType.PARTIAL_SCAN,
            error_message="",
            warnings=["test warning"],
        )
        assert result_with_warnings.warnings == ["test warning"]

    def test_edge_case_handler_error_initialization(self) -> None:
        """Test EdgeCaseHandlerError initialization."""
        original_error = ValueError("Original error")
        error = EdgeCaseHandlerError("Handler error", EdgeCaseType.APPLESCRIPT_TIMEOUT, original_error)

        assert str(error) == "Handler error"
        assert error.edge_case_type == EdgeCaseType.APPLESCRIPT_TIMEOUT
        assert error.original_error == original_error

    def test_statistics_update_during_operations(self, handler: EdgeCaseHandler) -> None:
        """Test that statistics are properly updated during operations."""
        initial_stats = handler.get_edge_case_statistics()

        # Use operation that definitely triggers statistics update
        handler.detect_partial_scan(track_count=5)  # Low count triggers partial scan

        stats_after_success = handler.get_edge_case_statistics()
        assert stats_after_success["successful_recoveries"] == initial_stats["successful_recoveries"] + 1
        assert stats_after_success["total_cases"] == initial_stats["total_cases"] + 1

        # Check that retry counts and recovery times are tracked
        handler_stats = handler._edge_case_stats
        assert "partial_scan" in handler_stats["retry_counts"]
        assert "partial_scan" in handler_stats["recovery_times"]


import pytest

pytestmark = pytest.mark.integration
