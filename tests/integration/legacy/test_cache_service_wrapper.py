#!/usr/bin/env python3

"""Tests for Cache Service Backward Compatibility Wrapper.

This test suite validates that the CacheServiceWrapper maintains perfect
backward compatibility with the original CacheService while adding migration
capabilities when enabled.

Test Categories:
    - Backward compatibility validation
    - Migration control functionality
    - Configuration handling
    - Safe fallback behavior
    - Interface preservation
"""

import logging
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

try:
    from src.infrastructure.cache.cache_service_wrapper import CacheServiceWrapper
    from src.infrastructure.cache.migration_handler import MigrationPhase
    from src.shared.data.models import CachedApiResult
except ModuleNotFoundError:
    pytest.skip("CacheServiceWrapper legacy module not available", allow_module_level=True)

pytestmark = pytest.mark.asyncio


class TestCacheServiceWrapper:
    """Test suite for CacheServiceWrapper backward compatibility."""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration for testing with temp-backed cache paths."""
        temp_dir = tempfile.mkdtemp()
        config = {
            "caching": {
                "default_ttl_seconds": 900,
                "cache_size_limits": {
                    "generic_cache_max_entries": 50000,
                    "album_cache_max_entries": 10000,
                    "api_cache_max_entries": 50000,
                },
                "migration": {
                    "enabled": False,
                    "force": False,
                    "fallback_on_error": True,
                },
                "api_result_cache_path": str(Path(temp_dir) / "api_results.json"),
            },
            "cache_dir": temp_dir,
            "logs_base_dir": temp_dir,
            "api_cache_file": str(Path(temp_dir) / "cache.json"),
        }

        try:
            yield config
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def mock_loggers(self):
        """Mock loggers for testing."""
        console_logger = Mock(spec=logging.Logger)
        error_logger = Mock(spec=logging.Logger)
        return console_logger, error_logger

    @pytest.fixture
    def wrapper(self, mock_config, mock_loggers):
        """Create a CacheServiceWrapper instance for testing."""
        console_logger, error_logger = mock_loggers
        wrapper = CacheServiceWrapper(mock_config, console_logger, error_logger)
        return wrapper

    async def init_wrapper(self, wrapper):
        """Helper to initialize wrapper with mocked dependencies."""
        with patch.object(wrapper.legacy_cache, "initialize", new_callable=AsyncMock):
            await wrapper.initialize()
        return wrapper

    @pytest.fixture
    def migration_enabled_config(self, mock_config):
        """Configuration with migration enabled."""
        config = mock_config.copy()
        config["caching"]["migration"]["enabled"] = True
        return config

    async def test_initialization_without_migration(self, mock_config, mock_loggers):
        """Test wrapper initialization with migration disabled."""
        console_logger, error_logger = mock_loggers
        wrapper = CacheServiceWrapper(mock_config, console_logger, error_logger)

        with patch.object(wrapper.legacy_cache, "initialize", new_callable=AsyncMock) as mock_init:
            await wrapper.initialize()

        # Verify legacy cache was initialized
        mock_init.assert_called_once()

        # Verify migration components are not initialized
        assert wrapper.fingerprint_generator is None
        assert wrapper.library_state_manager is None
        assert wrapper.migration_handler is None
        assert not wrapper.migration_active
        assert not wrapper.use_fingerprint_cache
        assert not wrapper.fallback_mode

    async def test_initialization_with_migration_enabled(self, migration_enabled_config, mock_loggers):
        """Test wrapper initialization with migration enabled."""
        console_logger, error_logger = mock_loggers
        wrapper = CacheServiceWrapper(migration_enabled_config, console_logger, error_logger)

        with (
            patch.object(wrapper.legacy_cache, "initialize", new_callable=AsyncMock),
            patch.object(wrapper, "_initialize_migration_components", new_callable=AsyncMock) as mock_migration_init,
            patch.object(wrapper, "_check_migration_status", new_callable=AsyncMock) as mock_status_check,
        ):
            await wrapper.initialize()

        # Verify migration components were initialized
        mock_migration_init.assert_called_once()
        mock_status_check.assert_called_once()

    async def test_migration_initialization_failure_with_fallback(self, migration_enabled_config, mock_loggers):
        """Test migration initialization failure with fallback enabled."""
        console_logger, error_logger = mock_loggers
        wrapper = CacheServiceWrapper(migration_enabled_config, console_logger, error_logger)

        with (
            patch.object(wrapper.legacy_cache, "initialize", new_callable=AsyncMock),
            patch.object(wrapper, "_initialize_migration_components", side_effect=Exception("Migration init failed")),
        ):
            await wrapper.initialize()

        # Should fall back to legacy cache
        assert wrapper.fallback_mode
        assert not wrapper.migration_active

    async def test_migration_initialization_failure_without_fallback(self, migration_enabled_config, mock_loggers):
        """Test migration initialization failure with fallback disabled."""
        console_logger, error_logger = mock_loggers
        migration_enabled_config["caching"]["migration"]["fallback_on_error"] = False
        wrapper = CacheServiceWrapper(migration_enabled_config, console_logger, error_logger)

        with (
            patch.object(wrapper.legacy_cache, "initialize", new_callable=AsyncMock),
            patch.object(wrapper, "_initialize_migration_components", side_effect=Exception("Migration init failed")),
        ):
            with pytest.raises(Exception, match="Migration init failed"):
                await wrapper.initialize()

    # ==================== BACKWARD COMPATIBILITY TESTS ====================

    def test_generate_album_key_compatibility(self):
        """Test that generate_album_key maintains exact compatibility."""
        # Test the static method directly
        key = CacheServiceWrapper.generate_album_key("Artist", "Album")
        assert isinstance(key, str)
        assert len(key) > 0

        # Should be deterministic
        key2 = CacheServiceWrapper.generate_album_key("Artist", "Album")
        assert key == key2

    async def test_set_get_async_compatibility(self, wrapper):
        """Test set and get_async methods maintain compatibility."""
        # Initialize wrapper first
        await self.init_wrapper(wrapper)

        # Mock the legacy cache methods
        wrapper.legacy_cache.set_async = AsyncMock()
        wrapper.legacy_cache.get_async = AsyncMock(return_value="test_value")

        # Test async set
        await wrapper.set_async("test_key", "test_value", ttl=300)
        wrapper.legacy_cache.set_async.assert_called_once_with("test_key", "test_value", 300)

        # Test async get
        result = await wrapper.get_async("test_key")
        wrapper.legacy_cache.get_async.assert_called_once_with("test_key", None)
        assert result == "test_value"

    async def test_album_cache_methods_compatibility(self, wrapper):
        """Test album cache methods maintain exact compatibility."""
        # Mock legacy cache methods
        wrapper.legacy_cache.get_album_year_from_cache = AsyncMock(return_value="2023")
        wrapper.legacy_cache.store_album_year_in_cache = AsyncMock()
        wrapper.legacy_cache.invalidate_album_cache = AsyncMock()

        # Test get
        year = await wrapper.get_album_year_from_cache("Artist", "Album")
        assert year == "2023"
        wrapper.legacy_cache.get_album_year_from_cache.assert_called_once_with("Artist", "Album")

        # Test store
        await wrapper.store_album_year_in_cache("Artist", "Album", "2023")
        wrapper.legacy_cache.store_album_year_in_cache.assert_called_once_with("Artist", "Album", "2023")

        # Test invalidate
        await wrapper.invalidate_album_cache("Artist", "Album")
        wrapper.legacy_cache.invalidate_album_cache.assert_called_once_with("Artist", "Album")

    async def test_api_cache_methods_compatibility(self, wrapper):
        """Test API cache methods maintain exact compatibility."""
        # Mock legacy cache methods
        mock_result = CachedApiResult(artist="Artist", album="Album", year="2023", source="musicbrainz", timestamp=1234567890.0)
        wrapper.legacy_cache.get_cached_api_result = AsyncMock(return_value=mock_result)
        wrapper.legacy_cache.set_cached_api_result = AsyncMock()

        # Test get
        result = await wrapper.get_cached_api_result("Artist", "Album", "musicbrainz")
        assert result == mock_result
        wrapper.legacy_cache.get_cached_api_result.assert_called_once_with("Artist", "Album", "musicbrainz")

        # Test set
        await wrapper.set_cached_api_result("Artist", "Album", "musicbrainz", "2023")
        wrapper.legacy_cache.set_cached_api_result.assert_called_once_with("Artist", "Album", "musicbrainz", "2023", metadata=None, is_negative=False)

    async def test_cache_management_methods_compatibility(self, wrapper):
        """Test cache management methods maintain compatibility."""
        # Mock legacy cache methods
        wrapper.legacy_cache.invalidate = Mock()
        wrapper.legacy_cache.clear = Mock()
        wrapper.legacy_cache.load_cache = AsyncMock()
        wrapper.legacy_cache.save_cache = AsyncMock()
        wrapper.legacy_cache.sync_cache = AsyncMock()

        # Test invalidate
        wrapper.invalidate("test_key")
        wrapper.legacy_cache.invalidate.assert_called_once_with("test_key")

        # Test clear
        wrapper.clear()
        wrapper.legacy_cache.clear.assert_called_once()

        # Test load_cache
        await wrapper.load_cache()
        wrapper.legacy_cache.load_cache.assert_called_once()

        # Test save_cache
        await wrapper.save_cache()
        wrapper.legacy_cache.save_cache.assert_called_once()

        # Test sync_cache
        await wrapper.sync_cache()
        wrapper.legacy_cache.sync_cache.assert_called_once()

    # ==================== MIGRATION CONTROL TESTS ====================

    async def test_migration_status_disabled(self, wrapper):
        """Test migration status when migration is disabled."""
        status = await wrapper.get_migration_status()

        assert not status["migration_enabled"]
        assert status["phase"] == "NOT_STARTED"
        assert not status["use_fingerprint_cache"]

    async def test_start_migration_disabled(self, wrapper):
        """Test starting migration when it's disabled."""
        result = await wrapper.start_migration()

        assert not result["success"]
        assert "not enabled" in result["error"]
        assert result["phase"] == "NOT_STARTED"

    async def test_migration_with_enabled_config(self, migration_enabled_config, mock_loggers):
        """Test migration functionality when enabled."""
        console_logger, error_logger = mock_loggers
        wrapper = CacheServiceWrapper(migration_enabled_config, console_logger, error_logger)

        # Mock migration components
        mock_migration_handler = AsyncMock()
        mock_migration_handler.get_migration_status.return_value = {"current_phase": MigrationPhase.NOT_STARTED}
        mock_migration_handler.start_migration.return_value = Mock(
            success=True, final_phase=MigrationPhase.MIGRATION_COMPLETE, statistics={"processed": 100}, error_message=None
        )

        with (
            patch.object(wrapper.legacy_cache, "initialize", new_callable=AsyncMock),
            patch.object(wrapper, "_initialize_migration_components", new_callable=AsyncMock),
            patch.object(wrapper, "_check_migration_status", new_callable=AsyncMock),
        ):
            await wrapper.initialize()
            wrapper.migration_handler = mock_migration_handler

            # Test migration status
            status = await wrapper.get_migration_status()
            assert status["migration_enabled"]

            # Test start migration
            result = await wrapper.start_migration()
            assert result["success"]
            assert result["phase"] == "migration_complete"

    async def test_rollback_migration(self, migration_enabled_config, mock_loggers):
        """Test migration rollback functionality."""
        console_logger, error_logger = mock_loggers
        wrapper = CacheServiceWrapper(migration_enabled_config, console_logger, error_logger)

        # Mock migration handler
        mock_migration_handler = AsyncMock()
        mock_migration_handler.rollback_migration.return_value = Mock(success=True, final_phase=MigrationPhase.NOT_STARTED, error_message=None)

        wrapper.migration_handler = mock_migration_handler
        wrapper.migration_active = True
        wrapper.use_fingerprint_cache = True

        # Test rollback
        result = await wrapper.rollback_migration("Test rollback")

        assert result["success"]
        assert result["phase"] == "not_started"
        assert result["reason"] == "Test rollback"
        assert not wrapper.migration_active
        assert not wrapper.use_fingerprint_cache

    # ==================== STATISTICS AND METRICS TESTS ====================

    async def test_get_unified_stats_with_migration_info(self, wrapper):
        """Test that unified stats include migration information."""
        # Mock legacy cache stats
        wrapper.legacy_cache.get_unified_stats = Mock(return_value={"legacy_stats": {"cache_size": 100}})

        stats = wrapper.get_unified_stats()

        assert "migration_info" in stats
        migration_info = stats["migration_info"]
        assert "migration_enabled" in migration_info
        assert "migration_active" in migration_info
        assert "use_fingerprint_cache" in migration_info
        assert "fallback_mode" in migration_info

    async def test_performance_metrics_delegation(self, wrapper):
        """Test that performance metrics are properly delegated."""
        expected_metrics = {"performance": "data"}
        wrapper.legacy_cache.get_cache_performance_metrics = Mock(return_value=expected_metrics)

        metrics = wrapper.get_cache_performance_metrics()
        assert metrics == expected_metrics

    # ==================== ERROR HANDLING TESTS ====================

    async def test_migration_error_handling(self, migration_enabled_config, mock_loggers):
        """Test proper error handling in migration operations."""
        console_logger, error_logger = mock_loggers
        wrapper = CacheServiceWrapper(migration_enabled_config, console_logger, error_logger)

        # Mock migration handler that raises exception
        mock_migration_handler = AsyncMock()
        mock_migration_handler.start_migration.side_effect = Exception("Migration failed")

        wrapper.migration_handler = mock_migration_handler

        # Test error handling in start_migration
        result = await wrapper.start_migration()
        assert not result["success"]
        assert "Migration failed" in result["error"]
        assert result["phase"] == "ERROR"

    async def test_fingerprint_update_error_handling(self, wrapper):
        """Test error handling when fingerprint updates fail."""
        # Enable fingerprint cache mode
        wrapper.use_fingerprint_cache = True
        wrapper.library_state_manager = AsyncMock()
        wrapper.fingerprint_generator = Mock()
        wrapper.fingerprint_generator.generate_track_fingerprint.side_effect = Exception("Fingerprint failed")

        # Mock legacy cache
        wrapper.legacy_cache.set_async = AsyncMock()

        # Should not fail even if fingerprint update fails
        track_data = {"id": "123", "name": "Test Track"}
        await wrapper.set_async("test_key", track_data)

        # Legacy cache should still be called
        wrapper.legacy_cache.set_async.assert_called_once()

    # ==================== INTEGRATION TESTS ====================

    async def test_complete_workflow_without_migration(self, wrapper):
        """Test complete cache workflow without migration enabled."""
        # Mock all legacy cache methods
        wrapper.legacy_cache.set_async = AsyncMock()
        wrapper.legacy_cache.get_async = AsyncMock(return_value="cached_value")
        wrapper.legacy_cache.invalidate = Mock()

        # Perform typical cache operations
        await wrapper.set_async("key1", "value1")
        result = await wrapper.get_async("key1")
        wrapper.invalidate("key1")

        # Verify all operations went to legacy cache
        wrapper.legacy_cache.set_async.assert_called_once()
        wrapper.legacy_cache.get_async.assert_called_once()
        wrapper.legacy_cache.invalidate.assert_called_once()

        assert result == "cached_value"

    async def test_cleanup_task_delegation(self, wrapper):
        """Test that cleanup task management is properly delegated."""
        wrapper.legacy_cache.stop_cleanup_task = AsyncMock()

        await wrapper.stop_cleanup_task()
        wrapper.legacy_cache.stop_cleanup_task.assert_called_once()


class TestCacheServiceWrapperExport:
    """Test the backward compatibility export."""

    def test_compatible_export_exists(self):
        """Test that CacheServiceCompatible is properly exported."""
        from src.infrastructure.cache.cache_service_wrapper import CacheServiceCompatible

        # Should be the same as CacheServiceWrapper
        assert CacheServiceCompatible is CacheServiceWrapper

    def test_import_compatibility(self):
        """Test that existing imports can work without changes."""
        # This would allow existing code to use:
        # from src.infrastructure.cache.cache_service_wrapper import CacheServiceCompatible as CacheService
        from src.infrastructure.cache.cache_service_wrapper import CacheServiceCompatible

        # Verify it has the expected interface
        assert hasattr(CacheServiceCompatible, "initialize")
        assert hasattr(CacheServiceCompatible, "get_async")
        assert hasattr(CacheServiceCompatible, "set_async")
        assert hasattr(CacheServiceCompatible, "get_album_year_from_cache")
        assert hasattr(CacheServiceCompatible, "store_album_year_in_cache")
        assert hasattr(CacheServiceCompatible, "get_cached_api_result")
        assert hasattr(CacheServiceCompatible, "set_cached_api_result")


if __name__ == "__main__":
    pass
import pytest

pytestmark = pytest.mark.integration
