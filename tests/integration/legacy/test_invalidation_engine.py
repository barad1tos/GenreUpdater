#!/usr/bin/env python3

"""Tests for InvalidationEngine.

Tests the cache invalidation coordination functionality that determines which
cache entries need to be invalidated based on library state changes.

Test Categories:
1. Basic invalidation detection
2. Invalidation plan creation and prioritization
3. Task execution and error handling
4. Dependency pattern registration and usage
5. Statistics tracking and management
6. Performance estimation and optimization
"""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.infrastructure.cache.invalidation_engine import (
    InvalidationEngine,
    InvalidationError,
    InvalidationPlan,
    InvalidationPriority,
    InvalidationReason,
    InvalidationTask,
)
from src.infrastructure.cache.library_state_manager import ChangeSet


class TestInvalidationEngine:
    """Test cases for InvalidationEngine class."""

    @pytest.fixture
    def engine(self) -> InvalidationEngine:
        """Create InvalidationEngine instance for testing."""
        return InvalidationEngine()

    @pytest.fixture
    def mock_cache_service(self) -> Mock:
        """Create mock cache service with invalidate method."""
        mock_service = Mock()
        mock_service.invalidate = Mock(return_value=True)
        return mock_service

    @pytest.fixture
    def sample_changes_mixed(self) -> ChangeSet:
        """Sample ChangeSet with mixed changes for testing."""
        return ChangeSet(deleted={"track_1", "track_2"}, added={"track_4", "track_5"}, modified={"track_3"})

    @pytest.fixture
    def sample_changes_empty(self) -> ChangeSet:
        """Empty ChangeSet for testing no-change scenarios."""
        return ChangeSet(deleted=set(), added=set(), modified=set())

    @pytest.fixture
    def sample_changes_deletions_only(self) -> ChangeSet:
        """ChangeSet with only deletions for testing."""
        return ChangeSet(deleted={"track_1", "track_2", "track_3"}, added=set(), modified=set())

    def test_engine_initialization(self, engine: InvalidationEngine) -> None:
        """Test that engine initializes correctly."""
        assert engine._dependency_patterns == {}
        assert engine._invalidation_stats["total_invalidations"] == 0
        assert engine._invalidation_stats["immediate_invalidations"] == 0
        assert engine._invalidation_stats["dependency_invalidations"] == 0
        assert engine._invalidation_stats["batch_invalidations"] == 0

    def test_should_invalidate_cache_with_changes(self, engine: InvalidationEngine, sample_changes_mixed: ChangeSet) -> None:
        """Test that invalidation is needed when changes exist."""
        result = engine.should_invalidate_cache(sample_changes_mixed)
        assert result is True

    def test_should_invalidate_cache_no_changes(self, engine: InvalidationEngine, sample_changes_empty: ChangeSet) -> None:
        """Test that no invalidation is needed when no changes exist."""
        result = engine.should_invalidate_cache(sample_changes_empty)
        assert result is False

    def test_create_invalidation_plan_mixed_changes(self, engine: InvalidationEngine, sample_changes_mixed: ChangeSet) -> None:
        """Test creating invalidation plan with mixed changes."""
        plan = engine.create_invalidation_plan(sample_changes_mixed)

        # Check immediate tasks (deletions)
        assert len(plan.immediate_tasks) == 4  # 2 tracks × 2 cache keys each
        deletion_tasks = [task for task in plan.immediate_tasks if task.reason == InvalidationReason.TRACK_DELETED]
        assert len(deletion_tasks) == 4

        # Check high priority tasks (modifications)
        assert len(plan.high_priority_tasks) == 2  # 1 track × 2 cache keys
        modification_tasks = [task for task in plan.high_priority_tasks if task.reason == InvalidationReason.TRACK_MODIFIED]
        assert len(modification_tasks) == 2

        # Check normal priority tasks (additions) - should have ALL cache invalidation
        assert len(plan.normal_priority_tasks) == 2  # 2 tracks × 1 ALL cache key each
        addition_tasks = [task for task in plan.normal_priority_tasks if task.cache_key == "ALL"]
        assert len(addition_tasks) == 2

        # Check performance estimates
        assert plan.estimated_keys_affected > 0
        assert plan.estimated_rebuild_time > 0

    def test_create_invalidation_plan_deletions_only(self, engine: InvalidationEngine, sample_changes_deletions_only: ChangeSet) -> None:
        """Test creating invalidation plan with only deletions."""
        plan = engine.create_invalidation_plan(sample_changes_deletions_only)

        # All tasks should be immediate priority
        assert len(plan.immediate_tasks) == 6  # 3 tracks × 2 cache keys each
        assert len(plan.high_priority_tasks) == 0
        assert len(plan.normal_priority_tasks) == 0
        assert len(plan.low_priority_tasks) == 0

        # All tasks should be deletion-related
        for task in plan.immediate_tasks:
            assert task.reason == InvalidationReason.TRACK_DELETED
            assert task.priority == InvalidationPriority.IMMEDIATE

    def test_create_invalidation_plan_empty_changes(self, engine: InvalidationEngine, sample_changes_empty: ChangeSet) -> None:
        """Test creating invalidation plan with no changes."""
        plan = engine.create_invalidation_plan(sample_changes_empty)

        assert len(plan.immediate_tasks) == 0
        assert len(plan.high_priority_tasks) == 0
        assert len(plan.normal_priority_tasks) == 0
        assert len(plan.low_priority_tasks) == 0
        assert plan.estimated_keys_affected == 0
        assert plan.estimated_rebuild_time == 0

    def test_deletion_task_creation(self, engine: InvalidationEngine) -> None:
        """Test creation of deletion tasks."""
        tasks = engine._create_deletion_tasks("track_123")

        assert len(tasks) == 2

        # Check direct track cache task
        track_task = next(task for task in tasks if task.cache_key == "track_track_123")
        assert track_task.reason == InvalidationReason.TRACK_DELETED
        assert track_task.priority == InvalidationPriority.IMMEDIATE
        assert track_task.track_id == "track_123"

        # Check processed track cache task
        processed_task = next(task for task in tasks if task.cache_key == "processed_track_123")
        assert processed_task.reason == InvalidationReason.TRACK_DELETED
        assert processed_task.priority == InvalidationPriority.IMMEDIATE
        assert processed_task.track_id == "track_123"

    def test_modification_task_creation(self, engine: InvalidationEngine) -> None:
        """Test creation of modification tasks."""
        tasks = engine._create_modification_tasks("track_456")

        assert len(tasks) == 2

        # Check tasks have high priority (not immediate)
        for task in tasks:
            assert task.reason == InvalidationReason.TRACK_MODIFIED
            assert task.priority == InvalidationPriority.HIGH
            assert task.track_id == "track_456"

    def test_addition_task_creation(self, engine: InvalidationEngine) -> None:
        """Test creation of addition tasks."""
        tasks = engine._create_addition_tasks("track_789")

        assert len(tasks) == 1

        # Check ALL cache invalidation
        all_task = tasks[0]
        assert all_task.cache_key == "ALL"
        assert all_task.reason == InvalidationReason.TRACK_MODIFIED
        assert all_task.priority == InvalidationPriority.LOW
        assert all_task.track_id == "track_789"

    def test_execute_invalidation_plan_success(self, engine: InvalidationEngine, mock_cache_service: Mock) -> None:
        """Test successful execution of invalidation plan."""
        # Create simple plan with one immediate task
        task = InvalidationTask(
            cache_key="test_key", reason=InvalidationReason.TRACK_DELETED, priority=InvalidationPriority.IMMEDIATE, track_id="track_1"
        )
        plan = InvalidationPlan(
            immediate_tasks=[task],
            high_priority_tasks=[],
            normal_priority_tasks=[],
            low_priority_tasks=[],
            estimated_keys_affected=1,
            estimated_rebuild_time=0.001,
        )

        results = engine.execute_invalidation_plan(plan, mock_cache_service)

        assert results["immediate_executed"] == 1
        assert results["high_priority_executed"] == 0
        assert results["normal_priority_executed"] == 0
        assert results["low_priority_executed"] == 0
        assert len(results["failed_tasks"]) == 0
        assert results["execution_time"] > 0

        # Verify cache service was called
        mock_cache_service.invalidate.assert_called_once_with("test_key")

    def test_execute_invalidation_plan_with_execute_all(self, engine: InvalidationEngine, mock_cache_service: Mock) -> None:
        """Test execution of invalidation plan with execute_all=True."""
        # Create plan with tasks at different priorities
        immediate_task = InvalidationTask("immediate_key", InvalidationReason.TRACK_DELETED, InvalidationPriority.IMMEDIATE)
        normal_task = InvalidationTask("normal_key", InvalidationReason.TRACK_MODIFIED, InvalidationPriority.NORMAL)
        low_task = InvalidationTask("low_key", InvalidationReason.TRACK_MODIFIED, InvalidationPriority.LOW)

        plan = InvalidationPlan(
            immediate_tasks=[immediate_task],
            high_priority_tasks=[],
            normal_priority_tasks=[normal_task],
            low_priority_tasks=[low_task],
            estimated_keys_affected=3,
            estimated_rebuild_time=0.003,
        )

        results = engine.execute_invalidation_plan(plan, mock_cache_service, execute_all=True)

        assert results["immediate_executed"] == 1
        assert results["high_priority_executed"] == 0
        assert results["normal_priority_executed"] == 1
        assert results["low_priority_executed"] == 1
        assert len(results["failed_tasks"]) == 0

        # Verify all cache service calls
        assert mock_cache_service.invalidate.call_count == 3

    def test_execute_invalidation_plan_cache_service_failure(self, engine: InvalidationEngine) -> None:
        """Test execution when cache service invalidation fails."""
        # Create mock that returns False (failure)
        failing_cache_service = Mock()
        failing_cache_service.invalidate = Mock(return_value=False)

        # Use LOW priority task to avoid critical failure exception
        task = InvalidationTask("test_key", InvalidationReason.TRACK_MODIFIED, InvalidationPriority.LOW)
        plan = InvalidationPlan([], [], [], [task], 1, 0.001)

        results = engine.execute_invalidation_plan(plan, failing_cache_service, execute_all=True)

        assert results["low_priority_executed"] == 0
        assert len(results["failed_tasks"]) == 1
        assert results["failed_tasks"][0] == task

    def test_execute_invalidation_plan_missing_invalidate_method(self, engine: InvalidationEngine) -> None:
        """Test execution when cache service lacks invalidate method."""
        cache_service_no_method = Mock(spec=[])  # No invalidate method

        # Use LOW priority task to avoid critical failure exception
        task = InvalidationTask("test_key", InvalidationReason.TRACK_MODIFIED, InvalidationPriority.LOW)
        plan = InvalidationPlan([], [], [], [task], 1, 0.001)

        results = engine.execute_invalidation_plan(plan, cache_service_no_method, execute_all=True)

        assert results["low_priority_executed"] == 0
        assert len(results["failed_tasks"]) == 1

    def test_execute_invalidation_plan_critical_failure_raises_error(self, engine: InvalidationEngine) -> None:
        """Test that critical task failures raise InvalidationError."""
        failing_cache_service = Mock()
        failing_cache_service.invalidate = Mock(return_value=False)

        # Create immediate priority task (critical)
        task = InvalidationTask("critical_key", InvalidationReason.TRACK_DELETED, InvalidationPriority.IMMEDIATE)
        plan = InvalidationPlan([task], [], [], [], 1, 0.001)

        with pytest.raises(InvalidationError, match="critical invalidation tasks failed"):
            engine.execute_invalidation_plan(plan, failing_cache_service)

    def test_execute_invalidation_plan_non_critical_failure_succeeds(self, engine: InvalidationEngine) -> None:
        """Test that non-critical task failures don't raise error."""
        failing_cache_service = Mock()
        failing_cache_service.invalidate = Mock(return_value=False)

        # Create low priority task (non-critical)
        task = InvalidationTask("low_key", InvalidationReason.TRACK_MODIFIED, InvalidationPriority.LOW)
        plan = InvalidationPlan([], [], [], [task], 1, 0.001)

        # Should not raise error
        results = engine.execute_invalidation_plan(plan, failing_cache_service, execute_all=True)
        assert len(results["failed_tasks"]) == 1

    def test_register_dependency_pattern(self, engine: InvalidationEngine) -> None:
        """Test registering dependency patterns."""
        dependencies = {"track_{track_id}", "metadata_{track_id}"}
        engine.register_dependency_pattern("album_{track_id}", dependencies)

        assert "album_{track_id}" in engine._dependency_patterns
        assert engine._dependency_patterns["album_{track_id}"] == dependencies

    def test_dependency_task_creation(self, engine: InvalidationEngine) -> None:
        """Test creation of dependency-based invalidation tasks."""
        # Register dependency pattern
        dependencies = {"track_{track_id}"}
        engine.register_dependency_pattern("album_{track_id}", dependencies)

        # Create dependency tasks for track change
        tasks = engine._create_dependency_tasks("track_123", InvalidationReason.TRACK_MODIFIED)

        assert len(tasks) == 1
        task = tasks[0]
        assert task.cache_key == "album_track_123"
        assert task.reason == InvalidationReason.DEPENDENCY_CHANGED
        assert task.priority == InvalidationPriority.NORMAL
        assert task.track_id == "track_123"
        assert "track_{track_id}" in task.dependencies

    def test_dependency_task_creation_no_match(self, engine: InvalidationEngine) -> None:
        """Test dependency task creation when no patterns match."""
        # Register pattern that won't match - use pattern without {track_id}
        dependencies = {"static_pattern"}
        engine.register_dependency_pattern("album_{track_id}", dependencies)

        # No dependency should match since pattern doesn't contain {track_id}
        tasks = engine._create_dependency_tasks("track_123", InvalidationReason.TRACK_MODIFIED)
        assert len(tasks) == 0

    def test_pattern_matches_track(self, engine: InvalidationEngine) -> None:
        """Test pattern matching logic."""
        assert engine._pattern_matches_track("track_{track_id}", "any_track") is True
        assert engine._pattern_matches_track("static_pattern", "any_track") is False

    def test_resolve_pattern(self, engine: InvalidationEngine) -> None:
        """Test pattern resolution with track ID."""
        result = engine._resolve_pattern("album_{track_id}", "track_123")
        assert result == "album_track_123"

        result = engine._resolve_pattern("static_key", "track_123")
        assert result == "static_key"

    def test_estimate_rebuild_time(self, engine: InvalidationEngine) -> None:
        """Test rebuild time estimation."""
        time_1_key = engine._estimate_rebuild_time(1)
        time_10_keys = engine._estimate_rebuild_time(10)
        time_100_keys = engine._estimate_rebuild_time(100)

        assert time_1_key > 0
        assert time_10_keys > time_1_key
        assert time_100_keys > time_10_keys

    def test_get_invalidation_statistics(self, engine: InvalidationEngine) -> None:
        """Test retrieval of invalidation statistics."""
        # Register some dependency patterns
        engine.register_dependency_pattern("pattern1", {"dep1"})
        engine.register_dependency_pattern("pattern2", {"dep2"})

        stats = engine.get_invalidation_statistics()

        assert "total_invalidations" in stats
        assert "immediate_invalidations" in stats
        assert "dependency_invalidations" in stats
        assert "batch_invalidations" in stats
        assert "dependency_patterns_registered" in stats
        assert stats["dependency_patterns_registered"] == 2

    def test_clear_statistics(self, engine: InvalidationEngine) -> None:
        """Test clearing invalidation statistics."""
        # Modify some statistics
        engine._invalidation_stats["total_invalidations"] = 10
        engine._invalidation_stats["immediate_invalidations"] = 5

        engine.clear_statistics()

        assert engine._invalidation_stats["total_invalidations"] == 0
        assert engine._invalidation_stats["immediate_invalidations"] == 0
        assert engine._invalidation_stats["dependency_invalidations"] == 0
        assert engine._invalidation_stats["batch_invalidations"] == 0

    def test_invalidation_statistics_update_during_execution(self, engine: InvalidationEngine, mock_cache_service: Mock) -> None:
        """Test that statistics are updated during plan execution."""
        # Create plan with immediate and dependency tasks
        engine.register_dependency_pattern("album_{track_id}", {"track_{track_id}"})

        immediate_task = InvalidationTask("immediate_key", InvalidationReason.TRACK_DELETED, InvalidationPriority.IMMEDIATE)
        plan = InvalidationPlan([immediate_task], [], [], [], 1, 0.001)

        initial_stats = engine.get_invalidation_statistics()

        engine.execute_invalidation_plan(plan, mock_cache_service)

        final_stats = engine.get_invalidation_statistics()

        assert final_stats["total_invalidations"] == initial_stats["total_invalidations"] + 1
        assert final_stats["immediate_invalidations"] == initial_stats["immediate_invalidations"] + 1
        assert final_stats["batch_invalidations"] == initial_stats["batch_invalidations"] + 1

    def test_invalidation_plan_with_dependencies(self, engine: InvalidationEngine) -> None:
        """Test that dependency tasks are included in invalidation plans."""
        # Register dependency pattern
        engine.register_dependency_pattern("album_{track_id}", {"track_{track_id}"})

        changes = ChangeSet(deleted=set(), added=set(), modified={"track_123"})

        plan = engine.create_invalidation_plan(changes)

        # Should have high priority tasks (direct + dependencies)
        # Modification tasks include dependency tasks in the same list
        assert len(plan.high_priority_tasks) == 3  # 2 direct + 1 dependency
        assert len(plan.normal_priority_tasks) == 0

        # Check dependency task is included in high_priority_tasks
        dependency_tasks = [task for task in plan.high_priority_tasks if task.reason == InvalidationReason.DEPENDENCY_CHANGED]
        assert len(dependency_tasks) == 1
        dependency_task = dependency_tasks[0]
        assert dependency_task.cache_key == "album_track_123"

    def test_execute_task_batch_exception_handling(self, engine: InvalidationEngine) -> None:
        """Test that task batch execution handles exceptions gracefully."""
        # Create mock that raises exception
        exception_cache_service = Mock()
        exception_cache_service.invalidate = Mock(side_effect=Exception("Test exception"))

        tasks = [
            InvalidationTask("key1", InvalidationReason.TRACK_DELETED, InvalidationPriority.IMMEDIATE),
            InvalidationTask("key2", InvalidationReason.TRACK_DELETED, InvalidationPriority.IMMEDIATE),
        ]
        failed_tasks = []

        executed_count = engine._execute_task_batch(tasks, exception_cache_service, failed_tasks)

        assert executed_count == 0
        assert len(failed_tasks) == 2
        assert failed_tasks[0].cache_key == "key1"
        assert failed_tasks[1].cache_key == "key2"

    def test_invalidation_task_post_init(self) -> None:
        """Test InvalidationTask post_init sets empty dependencies."""
        task = InvalidationTask("test_key", InvalidationReason.TRACK_DELETED, InvalidationPriority.IMMEDIATE)
        assert task.dependencies == set()

        task_with_deps = InvalidationTask("test_key", InvalidationReason.TRACK_DELETED, InvalidationPriority.IMMEDIATE, dependencies={"dep1", "dep2"})
        assert task_with_deps.dependencies == {"dep1", "dep2"}

    def test_invalidation_error_initialization(self) -> None:
        """Test InvalidationError initialization with and without failed tasks."""
        # Without failed tasks
        error1 = InvalidationError("Test error")
        assert str(error1) == "Test error"
        assert error1.failed_tasks == []

        # With failed tasks
        task = InvalidationTask("test_key", InvalidationReason.TRACK_DELETED, InvalidationPriority.IMMEDIATE)
        error2 = InvalidationError("Test error with tasks", [task])
        assert error2.failed_tasks == [task]


import pytest

pytestmark = pytest.mark.integration
