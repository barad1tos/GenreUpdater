#!/usr/bin/env python3

"""Tests for Migration Handler.

Comprehensive test suite for the MigrationHandler class that validates
the safe transition from TTL-based to content-based cache system.

Design Philosophy (Linus approved):
- Safety first: Test all failure scenarios and rollback mechanisms
- Zero downtime: Validate dual-cache system operation
- Data integrity: Ensure no cache data loss during migration
- Progress tracking: Verify migration progress and statistics
"""

import json
import logging
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from src.infrastructure.cache.edge_case_handler import EdgeCaseHandler
from src.infrastructure.cache.fingerprint_generator import FingerprintGenerator
from src.infrastructure.cache.invalidation_engine import InvalidationEngine
from src.infrastructure.cache.library_state_manager import LibraryStateManager
from src.infrastructure.cache.migration_handler import (
    MigrationConfig,
    MigrationError,
    MigrationHandler,
    MigrationPhase,
    MigrationResult,
    MigrationStats,
)


class TestMigrationHandler(TestCase):
    """Test cases for MigrationHandler."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.logger = Mock(spec=logging.Logger)

        # Mock core components
        self.fingerprint_generator = Mock(spec=FingerprintGenerator)
        self.library_state_manager = Mock(spec=LibraryStateManager)
        self.invalidation_engine = Mock(spec=InvalidationEngine)
        self.edge_case_handler = Mock(spec=EdgeCaseHandler)

        # Test configuration
        self.config = MigrationConfig(
            validation_sample_size=10,
            validation_threshold=0.8,
            dual_cache_duration=3600,  # 1 hour for testing
            backup_retention_days=1,
            rollback_timeout=60,
            batch_size=5,
            enable_progress_logging=True,
        )

        # Create temporary directory for testing
        self.temp_dir = tempfile.mkdtemp()

        with patch("src.infrastructure.cache.migration_handler.Path") as mock_path:
            # Mock Path to use temp directory
            mock_path.return_value = Path(self.temp_dir)

            self.migration_handler = MigrationHandler(
                fingerprint_generator=self.fingerprint_generator,
                library_state_manager=self.library_state_manager,
                invalidation_engine=self.invalidation_engine,
                edge_case_handler=self.edge_case_handler,
                config=self.config,
                logger=self.logger,
            )

    def test_initialization(self) -> None:
        """Test migration handler initialization."""
        # Test with default parameters
        default_handler = MigrationHandler(self.fingerprint_generator, self.library_state_manager, self.invalidation_engine, self.edge_case_handler)

        self.assertIsNotNone(default_handler.logger)
        self.assertIsNotNone(default_handler.config)
        self.assertEqual(default_handler.current_phase, MigrationPhase.NOT_STARTED)
        self.assertIsInstance(default_handler.migration_stats, MigrationStats)

        # Test with custom parameters
        self.assertEqual(self.migration_handler.config.validation_sample_size, 10)
        self.assertEqual(self.migration_handler.config.batch_size, 5)
        self.assertIs(self.migration_handler.logger, self.logger)

    def test_migration_phases_enum(self) -> None:
        """Test migration phases enumeration."""
        phases = [
            MigrationPhase.NOT_STARTED,
            MigrationPhase.FINGERPRINT_BUILDING,
            MigrationPhase.DUAL_CACHE_MODE,
            MigrationPhase.VALIDATION_PHASE,
            MigrationPhase.FINGERPRINT_PRIMARY,
            MigrationPhase.MIGRATION_COMPLETE,
            MigrationPhase.ROLLBACK_IN_PROGRESS,
        ]

        # Verify all phases are accessible
        for phase in phases:
            self.assertIsInstance(phase.value, str)

    def test_migration_error(self) -> None:
        """Test migration error handling."""
        error = MigrationError("Test error", MigrationPhase.FINGERPRINT_BUILDING, recoverable=True)

        self.assertEqual(str(error), "Test error")
        self.assertEqual(error.phase, MigrationPhase.FINGERPRINT_BUILDING)
        self.assertTrue(error.recoverable)

    def test_start_migration_already_in_progress(self) -> None:
        """Test starting migration when already in progress."""
        # Set migration as already started
        self.migration_handler.current_phase = MigrationPhase.FINGERPRINT_BUILDING

        with self.assertRaises(MigrationError) as context:
            self.migration_handler.start_migration()

        self.assertIn("Migration already in progress", str(context.exception))
        self.assertEqual(context.exception.phase, MigrationPhase.FINGERPRINT_BUILDING)

    def test_start_migration_fingerprint_build_failure(self) -> None:
        """Test migration failure during fingerprint database building."""
        # Mock library state manager to return empty state
        self.library_state_manager.get_current_library_state.return_value = {}

        # Mock private methods to prevent actual operations
        with patch.object(self.migration_handler, "_save_migration_state"):
            with patch.object(self.migration_handler, "rollback_migration", return_value=MigrationResult.SUCCESS):
                result = self.migration_handler.start_migration()

        self.assertEqual(result, MigrationResult.ROLLBACK_REQUIRED)

    def test_start_migration_successful_flow(self) -> None:
        """Test successful migration flow through all phases."""
        # Mock library state with sample tracks
        mock_tracks = {
            "track1": "fingerprint1",
            "track2": "fingerprint2",
            "track3": "fingerprint3",
        }
        self.library_state_manager.get_current_library_state.return_value = mock_tracks

        # Mock all private methods to simulate success
        with patch.object(self.migration_handler, "_save_migration_state"):
            with patch.object(self.migration_handler, "_build_initial_fingerprint_database", return_value=MigrationResult.SUCCESS):
                with patch.object(self.migration_handler, "_enter_dual_cache_mode", return_value=MigrationResult.SUCCESS):
                    with patch.object(self.migration_handler, "_validate_fingerprint_system", return_value=MigrationResult.SUCCESS):
                        with patch.object(self.migration_handler, "_switch_to_fingerprint_primary", return_value=MigrationResult.SUCCESS):
                            with patch.object(self.migration_handler, "_complete_migration"):
                                result = self.migration_handler.start_migration()

        self.assertEqual(result, MigrationResult.SUCCESS)
        self.assertEqual(self.migration_handler.current_phase, MigrationPhase.MIGRATION_COMPLETE)

    def test_rollback_migration_success(self) -> None:
        """Test successful migration rollback."""
        # Set migration in progress
        self.migration_handler.current_phase = MigrationPhase.VALIDATION_PHASE

        # Mock rollback operations
        with patch.object(self.migration_handler, "_save_migration_state"):
            with patch.object(self.migration_handler, "_restore_ttl_primary"):
                with patch.object(self.migration_handler, "_disable_fingerprint_system"):
                    with patch.object(self.migration_handler, "_cleanup_migration_artifacts"):
                        result = self.migration_handler.rollback_migration("Test rollback")

        self.assertEqual(result, MigrationResult.SUCCESS)
        self.assertEqual(self.migration_handler.current_phase, MigrationPhase.NOT_STARTED)

    def test_rollback_migration_failure(self) -> None:
        """Test migration rollback failure."""
        # Set migration in progress
        self.migration_handler.current_phase = MigrationPhase.DUAL_CACHE_MODE

        # Mock rollback operation to fail
        with patch.object(self.migration_handler, "_save_migration_state"):
            with patch.object(self.migration_handler, "_restore_ttl_primary", side_effect=Exception("Rollback failed")):
                with self.assertRaises(MigrationError) as context:
                    self.migration_handler.rollback_migration("Test rollback failure")

        self.assertIn("Rollback failed", str(context.exception))
        self.assertEqual(context.exception.phase, MigrationPhase.ROLLBACK_IN_PROGRESS)

    def test_get_migration_status_not_started(self) -> None:
        """Test migration status when not started."""
        status = self.migration_handler.get_migration_status()

        self.assertEqual(status["current_phase"], "not_started")
        self.assertEqual(status["progress_percentage"], 0.0)
        self.assertEqual(status["statistics"]["total_tracks"], 0)
        self.assertIn("Run start_migration()", status["next_action"])

    def test_get_migration_status_fingerprint_building(self) -> None:
        """Test migration status during fingerprint building."""
        # Set up migration stats
        self.migration_handler.current_phase = MigrationPhase.FINGERPRINT_BUILDING
        self.migration_handler.migration_stats.total_tracks = 100
        self.migration_handler.migration_stats.fingerprinted_tracks = 50

        status = self.migration_handler.get_migration_status()

        self.assertEqual(status["current_phase"], "fingerprint_building")
        self.assertEqual(status["progress_percentage"], 25.0)  # 50% of 50% complete
        self.assertEqual(status["statistics"]["total_tracks"], 100)
        self.assertEqual(status["statistics"]["fingerprinted_tracks"], 50)

    def test_get_migration_status_validation_phase(self) -> None:
        """Test migration status during validation phase."""
        # Set up migration stats
        self.migration_handler.current_phase = MigrationPhase.VALIDATION_PHASE
        self.migration_handler.migration_stats.total_tracks = 100
        self.migration_handler.migration_stats.fingerprinted_tracks = 100
        self.migration_handler.migration_stats.validation_passed = 8

        status = self.migration_handler.get_migration_status()

        self.assertEqual(status["current_phase"], "validation_phase")
        self.assertEqual(status["progress_percentage"], 74.0)  # 50 + (8/10 * 30)
        self.assertEqual(status["statistics"]["validation_success_rate"], 1.0)  # 8/8

    def test_build_initial_fingerprint_database_success(self) -> None:
        """Test successful fingerprint database building."""
        # Mock library state
        mock_tracks = {f"track{i}": f"fingerprint{i}" for i in range(15)}
        self.library_state_manager.get_current_library_state.return_value = mock_tracks

        # Mock file operations
        with patch.object(self.migration_handler, "_create_cache_backup"):
            with patch.object(self.migration_handler, "_save_fingerprint_database"):
                result = self.migration_handler._build_initial_fingerprint_database()

        self.assertEqual(result, MigrationResult.SUCCESS)
        self.assertEqual(self.migration_handler.migration_stats.total_tracks, 15)
        self.assertEqual(self.migration_handler.migration_stats.fingerprinted_tracks, 15)

    def test_build_initial_fingerprint_database_no_tracks(self) -> None:
        """Test fingerprint database building with no tracks."""
        # Mock empty library state
        self.library_state_manager.get_current_library_state.return_value = {}

        result = self.migration_handler._build_initial_fingerprint_database()

        self.assertEqual(result, MigrationResult.FAILURE)

    def test_enter_dual_cache_mode_success(self) -> None:
        """Test successful dual-cache mode entry."""
        # Mock cache system tests
        with patch.object(self.migration_handler, "_test_ttl_cache_operation", return_value=True):
            with patch.object(self.migration_handler, "_test_fingerprint_cache_operation", return_value=True):
                result = self.migration_handler._enter_dual_cache_mode()

        self.assertEqual(result, MigrationResult.SUCCESS)

    def test_enter_dual_cache_mode_ttl_failure(self) -> None:
        """Test dual-cache mode entry with TTL cache failure."""
        # Mock TTL cache failure
        with patch.object(self.migration_handler, "_test_ttl_cache_operation", return_value=False):
            result = self.migration_handler._enter_dual_cache_mode()

        self.assertEqual(result, MigrationResult.FAILURE)

    def test_enter_dual_cache_mode_fingerprint_failure(self) -> None:
        """Test dual-cache mode entry with fingerprint cache failure."""
        # Mock fingerprint cache failure
        with patch.object(self.migration_handler, "_test_ttl_cache_operation", return_value=True):
            with patch.object(self.migration_handler, "_test_fingerprint_cache_operation", return_value=False):
                result = self.migration_handler._enter_dual_cache_mode()

        self.assertEqual(result, MigrationResult.FAILURE)

    def test_validate_fingerprint_system_success(self) -> None:
        """Test successful fingerprint system validation."""
        # Mock library state
        mock_tracks = {f"track{i}": f"fingerprint{i}" for i in range(20)}
        self.library_state_manager.get_current_library_state.return_value = mock_tracks

        # Mock validation success
        with patch.object(self.migration_handler, "_validate_single_track", return_value=True):
            result = self.migration_handler._validate_fingerprint_system()

        self.assertEqual(result, MigrationResult.SUCCESS)
        self.assertEqual(self.migration_handler.migration_stats.validation_passed, 10)  # Sample size
        self.assertEqual(self.migration_handler.migration_stats.validation_failed, 0)

    def test_validate_fingerprint_system_failure(self) -> None:
        """Test fingerprint system validation failure."""
        # Mock library state
        mock_tracks = {f"track{i}": f"fingerprint{i}" for i in range(20)}
        self.library_state_manager.get_current_library_state.return_value = mock_tracks

        # Mock validation failure (only 70% success, below 80% threshold)
        def mock_validate(track_id):
            return track_id.endswith(("0", "1", "2", "3", "4", "5", "6"))  # 7/10 success

        with patch.object(self.migration_handler, "_validate_single_track", side_effect=mock_validate):
            result = self.migration_handler._validate_fingerprint_system()

        self.assertEqual(result, MigrationResult.FAILURE)

    def test_switch_to_fingerprint_primary_success(self) -> None:
        """Test successful switch to fingerprint primary."""
        # Mock successful switch
        with patch.object(self.migration_handler, "_test_fingerprint_primary_operation", return_value=True):
            result = self.migration_handler._switch_to_fingerprint_primary()

        self.assertEqual(result, MigrationResult.SUCCESS)

    def test_switch_to_fingerprint_primary_failure(self) -> None:
        """Test failed switch to fingerprint primary."""
        # Mock failed switch
        with patch.object(self.migration_handler, "_test_fingerprint_primary_operation", return_value=False):
            result = self.migration_handler._switch_to_fingerprint_primary()

        self.assertEqual(result, MigrationResult.FAILURE)

    def test_save_and_load_migration_state(self) -> None:
        """Test saving and loading migration state."""
        # Set up migration state
        self.migration_handler.current_phase = MigrationPhase.VALIDATION_PHASE
        self.migration_handler.migration_stats.total_tracks = 100
        self.migration_handler.migration_stats.fingerprinted_tracks = 80
        self.migration_handler.migration_stats.validation_passed = 15

        # Create temporary file for state
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            state_file = Path(f.name)

        self.migration_handler.migration_state_file = state_file

        try:
            # Save state
            self.migration_handler._save_migration_state()

            # Verify file was created
            self.assertTrue(state_file.exists())

            # Reset migration handler state
            self.migration_handler.current_phase = MigrationPhase.NOT_STARTED
            self.migration_handler.migration_stats = MigrationStats()

            # Load state
            self.migration_handler._load_migration_state()

            # Verify state was restored
            self.assertEqual(self.migration_handler.current_phase, MigrationPhase.VALIDATION_PHASE)
            self.assertEqual(self.migration_handler.migration_stats.total_tracks, 100)
            self.assertEqual(self.migration_handler.migration_stats.fingerprinted_tracks, 80)
            self.assertEqual(self.migration_handler.migration_stats.validation_passed, 15)

        finally:
            # Clean up
            if state_file.exists():
                state_file.unlink()

    def test_save_fingerprint_database(self) -> None:
        """Test saving fingerprint database."""
        database = {
            "track1": "fingerprint1",
            "track2": "fingerprint2",
            "track3": "fingerprint3",
        }

        # Create temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            db_file = Path(f.name)

        self.migration_handler.fingerprint_db_file = db_file

        try:
            # Save database
            self.migration_handler._save_fingerprint_database(database)

            # Verify file was created and content is correct
            self.assertTrue(db_file.exists())

            with open(db_file, "r") as f:
                saved_data = json.load(f)

            self.assertEqual(saved_data, database)

        finally:
            # Clean up
            if db_file.exists():
                db_file.unlink()

    def test_complete_migration(self) -> None:
        """Test migration completion."""
        # Mock completion operations
        with patch.object(self.migration_handler, "_cleanup_ttl_cache_data"):
            with patch.object(self.migration_handler, "_update_cache_configuration"):
                with patch.object(self.migration_handler, "_generate_migration_report"):
                    # Should not raise any exceptions
                    self.migration_handler._complete_migration()

    def test_migration_config_defaults(self) -> None:
        """Test migration configuration default values."""
        config = MigrationConfig()

        self.assertEqual(config.validation_sample_size, 100)
        self.assertEqual(config.validation_threshold, 0.95)
        self.assertEqual(config.dual_cache_duration, 86400)
        self.assertEqual(config.backup_retention_days, 7)
        self.assertEqual(config.rollback_timeout, 300)
        self.assertEqual(config.batch_size, 50)
        self.assertTrue(config.enable_progress_logging)

    def test_migration_stats_initialization(self) -> None:
        """Test migration statistics initialization."""
        stats = MigrationStats()

        self.assertEqual(stats.total_tracks, 0)
        self.assertEqual(stats.fingerprinted_tracks, 0)
        self.assertEqual(stats.validation_passed, 0)
        self.assertEqual(stats.validation_failed, 0)
        self.assertEqual(stats.cache_hits_ttl, 0)
        self.assertEqual(stats.cache_hits_fingerprint, 0)
        self.assertEqual(stats.migration_start_time, 0.0)
        self.assertEqual(stats.migration_duration, 0.0)
        self.assertEqual(stats.fingerprint_build_time, 0.0)
        self.assertEqual(stats.validation_time, 0.0)

    def test_progress_calculation_edge_cases(self) -> None:
        """Test progress calculation edge cases."""
        # Test with zero tracks
        self.migration_handler.migration_stats.total_tracks = 0
        status = self.migration_handler.get_migration_status()
        self.assertEqual(status["progress_percentage"], 0.0)

        # Test migration complete phase
        self.migration_handler.current_phase = MigrationPhase.MIGRATION_COMPLETE
        self.migration_handler.migration_stats.total_tracks = 100
        status = self.migration_handler.get_migration_status()
        self.assertEqual(status["progress_percentage"], 100.0)

    def test_next_action_recommendations(self) -> None:
        """Test next action recommendations for all phases."""
        test_cases = [
            (MigrationPhase.NOT_STARTED, "Run start_migration()"),
            (MigrationPhase.FINGERPRINT_BUILDING, "building fingerprint database"),
            (MigrationPhase.DUAL_CACHE_MODE, "both cache systems operational"),
            (MigrationPhase.VALIDATION_PHASE, "validating fingerprint system"),
            (MigrationPhase.FINGERPRINT_PRIMARY, "fingerprint system is primary"),
            (MigrationPhase.MIGRATION_COMPLETE, "Migration completed successfully"),
            (MigrationPhase.ROLLBACK_IN_PROGRESS, "Rollback in progress"),
        ]

        for phase, expected_text in test_cases:
            self.migration_handler.current_phase = phase
            recommendation = self.migration_handler._get_next_action_recommendation()
            self.assertIn(expected_text, recommendation)

    def test_validation_success_rate_calculation(self) -> None:
        """Test validation success rate calculation."""
        # Test with no validations
        status = self.migration_handler.get_migration_status()
        self.assertEqual(status["statistics"]["validation_success_rate"], 1.0)  # Default to 100% when no data

        # Test with some validations
        self.migration_handler.migration_stats.validation_passed = 8
        self.migration_handler.migration_stats.validation_failed = 2
        status = self.migration_handler.get_migration_status()
        self.assertEqual(status["statistics"]["validation_success_rate"], 0.8)  # 8/10


if __name__ == "__main__":
    pass
import pytest

pytestmark = pytest.mark.integration
