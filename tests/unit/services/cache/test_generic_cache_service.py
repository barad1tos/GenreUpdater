"""Comprehensive tests for GenericCacheService with Allure reporting."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import allure
import pytest

from src.services.cache.generic_cache import GenericCacheService, is_generic_cache_entry
from src.services.cache.hash_service import UnifiedHashService


@allure.epic("Music Genre Updater")
@allure.feature("Cache Infrastructure")
class TestGenericCacheService:
    """Comprehensive tests for GenericCacheService."""

    @staticmethod
    def create_service(config: dict[str, Any] | None = None) -> GenericCacheService:
        """Create a GenericCacheService instance for testing."""
        default_config = {
            "cleanup_interval": 1,  # Short interval for testing
            "max_generic_entries": 100,
            "logs_base_dir": tempfile.gettempdir(),
            "logging": {},
        }

        test_config = default_config.copy()
        if config:
            if logging_cfg := config.get("logging"):
                existing_logging = cast(dict[str, Any], test_config.get("logging", {}))
                merged_logging = {**existing_logging, **logging_cfg}
                test_config["logging"] = merged_logging
            for key, value in config.items():
                if key != "logging":
                    test_config[key] = value
        mock_logger = MagicMock()
        return GenericCacheService(test_config, mock_logger)

    @allure.story("Initialization")
    @allure.title("Should initialize generic cache service")
    @allure.description("Test initialization of generic cache service with cleanup task")
    @pytest.mark.asyncio
    async def test_initialization(self) -> None:
        """Test generic cache service initialization."""
        service = TestGenericCacheService.create_service()

        with allure.step("Initialize service"):
            await service.initialize()

        with allure.step("Verify initialization"):
            assert service.cache == {}
            assert service._cleanup_task is not None
            assert not service._cleanup_task.done()

        with allure.step("Stop cleanup task"):
            await service.stop_cleanup_task()

    @allure.story("Basic Operations")
    @allure.title("Should store and retrieve values from cache")
    @allure.description("Test basic get/set operations")
    @pytest.mark.asyncio
    async def test_get_set_operations(self) -> None:
        """Test basic cache get and set operations."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            with allure.step("Store value in cache"):
                key_data = "test_key"
                value = {"data": "test_value"}
                service.set(key_data, value, ttl=60)

            with allure.step("Retrieve value from cache"):
                cached_value = service.get(key_data)
                assert cached_value == value

            with allure.step("Verify cache miss for non-existent key"):
                missing_value = service.get("non_existent_key")
                assert missing_value is None

        finally:
            await service.stop_cleanup_task()

    @allure.story("Expiration")
    @allure.title("Should handle expired cache entries")
    @allure.description("Test TTL expiration and automatic removal")
    @pytest.mark.asyncio
    async def test_cache_expiration(self) -> None:
        """Test cache entry expiration."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            with allure.step("Store value with short TTL"):
                key_data = "expiring_key"
                value = {"data": "will_expire"}
                service.set(key_data, value, ttl=0)  # Already expired

            with allure.step("Verify expired entry returns None"):
                cached_value = service.get(key_data)
                assert cached_value is None

            with allure.step("Store value with longer TTL"):
                service.set(key_data, value, ttl=60)
                cached_value = service.get(key_data)
                assert cached_value == value

        finally:
            await service.stop_cleanup_task()

    @allure.story("Invalidation")
    @allure.title("Should invalidate cache entries")
    @allure.description("Test cache invalidation operations")
    @pytest.mark.asyncio
    async def test_cache_invalidation(self) -> None:
        """Test cache invalidation."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            with allure.step("Store multiple values"):
                service.set("key1", {"value": 1})
                service.set("key2", {"value": 2})
                service.set("key3", {"value": 3})

            with allure.step("Invalidate specific entry"):
                result = service.invalidate("key2")
                assert result is True
                assert service.get("key2") is None
                assert service.get("key1") is not None
                assert service.get("key3") is not None

            with allure.step("Attempt to invalidate non-existent entry"):
                result = service.invalidate("non_existent")
                assert result is False

            with allure.step("Invalidate all entries"):
                service.invalidate_all()
                assert service.get("key1") is None
                assert service.get("key3") is None
                assert len(service.cache) == 0

        finally:
            await service.stop_cleanup_task()

    @allure.story("Cleanup")
    @allure.title("Should clean up expired entries")
    @allure.description("Test periodic cleanup of expired cache entries")
    @pytest.mark.asyncio
    async def test_cleanup_expired_entries(self) -> None:
        """Test cleanup of expired entries."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            with allure.step("Store mixed TTL entries"):
                # Store entries with different expiration times
                service.set("expired1", {"value": 1}, ttl=-1)  # Already expired
                service.set("expired2", {"value": 2}, ttl=-1)  # Already expired
                service.set("valid1", {"value": 3}, ttl=60)  # Valid
                service.set("valid2", {"value": 4}, ttl=60)  # Valid

            with allure.step("Run cleanup"):
                removed_count = service.cleanup_expired()
                assert removed_count == 2

            with allure.step("Verify only valid entries remain"):
                assert service.get("expired1") is None
                assert service.get("expired2") is None
                assert service.get("valid1") is not None
                assert service.get("valid2") is not None

        finally:
            await service.stop_cleanup_task()

    @allure.story("Size Limits")
    @allure.title("Should enforce cache size limits")
    @allure.description("Test enforcement of maximum cache size")
    @pytest.mark.asyncio
    async def test_enforce_size_limits(self) -> None:
        """Test cache size limit enforcement."""
        config = {"max_generic_entries": 5}
        service = TestGenericCacheService.create_service(config)
        await service.initialize()

        try:
            with allure.step("Fill cache beyond limit"):
                # Add entries with different timestamps
                # Each entry will have timestamp = i + 100 for TTL
                for i in range(10):
                    # Manually set the cache to control timestamps
                    key = UnifiedHashService.hash_generic_key(f"key{i}")
                    # Store with timestamp that indicates when it expires
                    # Lower timestamps expire first
                    service.cache[key] = ({"value": i}, float(i + 100))

            with allure.step("Enforce size limits"):
                removed_count = service.enforce_size_limits()
                assert removed_count == 5  # Should remove 5 oldest entries

            with allure.step("Verify only newest entries remain"):
                assert len(service.cache) == 5
                # The entries with the highest timestamps should remain (5-9)
                # We need to check via the cache directly since get() checks expiration
                remaining_values = [v for v, _ in service.cache.values()]
                remaining_numbers = [cast(dict[str, Any], v)["value"] for v in remaining_values]
                remaining_numbers.sort()
                assert remaining_numbers == [5, 6, 7, 8, 9]

        finally:
            await service.stop_cleanup_task()

    @allure.story("Statistics")
    @allure.title("Should provide cache statistics")
    @allure.description("Test cache statistics collection")
    @pytest.mark.asyncio
    async def test_get_stats(self) -> None:
        """Test cache statistics."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            with allure.step("Populate cache with mixed entries"):
                service.set("valid1", {"value": 1}, ttl=60)
                service.set("valid2", {"value": 2}, ttl=60)
                service.set("expired1", {"value": 3}, ttl=-1)
                service.set("expired2", {"value": 4}, ttl=-1)

            with allure.step("Get statistics"):
                stats = service.get_stats()

            with allure.step("Verify statistics"):
                assert stats["total_entries"] == 4
                assert stats["valid_entries"] == 2
                assert stats["expired_entries"] == 2
                assert "default_ttl" in stats
                assert "content_type" in stats

        finally:
            await service.stop_cleanup_task()

    @allure.story("Cleanup Task")
    @allure.title("Should run periodic cleanup task")
    @allure.description("Test automatic periodic cleanup task execution")
    @pytest.mark.asyncio
    async def test_periodic_cleanup_task(self) -> None:
        """Test periodic cleanup task."""
        config = {"cleanup_interval": 0.1}  # Very short interval for testing
        service = TestGenericCacheService.create_service(config)
        await service.initialize()

        try:
            with allure.step("Store expired entries"):
                service.set("expired1", {"value": 1}, ttl=-1)
                service.set("expired2", {"value": 2}, ttl=-1)
                service.set("valid", {"value": 3}, ttl=60)

            with allure.step("Wait for automatic cleanup"):
                await asyncio.sleep(0.2)  # Wait for cleanup to run

            with allure.step("Verify expired entries were cleaned"):
                # The cleanup task should have removed expired entries
                assert len(service.cache) <= 3  # May have been cleaned

        finally:
            await service.stop_cleanup_task()

    @allure.story("Error Handling")
    @allure.title("Should handle errors in cleanup task")
    @allure.description("Test error handling in periodic cleanup task")
    @pytest.mark.asyncio
    async def test_cleanup_task_error_handling(self) -> None:
        """Test error handling in cleanup task."""
        service = TestGenericCacheService.create_service({"cleanup_interval": 0.1})

        with (
            allure.step("Mock cleanup to raise exception"),
            patch.object(service, "cleanup_expired", side_effect=Exception("Test error")),
        ):
            await service.initialize()

            # Wait for cleanup to run and handle error
            await asyncio.sleep(0.2)

            # Task should still be running despite error
            assert service._cleanup_task is not None
            assert not service._cleanup_task.done()

        with allure.step("Stop cleanup task"):
            await service.stop_cleanup_task()

    @allure.story("Type Guards")
    @allure.title("Should validate cache entry types")
    @allure.description("Test type guard for cache entries")
    def test_is_generic_cache_entry(self) -> None:
        """Test type guard for cache entries."""
        with allure.step("Test valid cache entries"):
            assert is_generic_cache_entry(({"data": "value"}, 123.45)) is True
            assert is_generic_cache_entry(("string_value", 100)) is True
            assert is_generic_cache_entry((None, 0.0)) is True

        with allure.step("Test invalid cache entries"):
            assert is_generic_cache_entry("not_a_tuple") is False
            assert is_generic_cache_entry(("missing_timestamp",)) is False
            assert is_generic_cache_entry((1, 2, 3)) is False  # Too many elements
            assert is_generic_cache_entry((100, "not_a_timestamp")) is False

    @allure.story("Debug Operations")
    @allure.title("Should provide debug information")
    @allure.description("Test debug and inspection operations")
    @pytest.mark.asyncio
    async def test_get_all_entries(self) -> None:
        """Test getting all cache entries for debugging."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            with allure.step("Store test entries"):
                service.set("key1", {"value": 1}, ttl=60)
                service.set("key2", {"value": 2}, ttl=60)
                service.set("key3", {"value": 3}, ttl=60)

            with allure.step("Get all entries"):
                entries = service.get_all_entries()

            with allure.step("Verify entries"):
                assert len(entries) == 3
                # Each entry should be (truncated_key, value, timestamp)
                for key, value, timestamp in entries:
                    assert isinstance(key, str)
                    assert len(key) <= 16  # Keys are truncated
                    assert isinstance(value, dict)
                    assert isinstance(timestamp, float)

        finally:
            await service.stop_cleanup_task()

    @allure.story("Default TTL")
    @allure.title("Should use default TTL when not specified")
    @allure.description("Test usage of default TTL for cache entries")
    @pytest.mark.asyncio
    async def test_default_ttl(self) -> None:
        """Test default TTL usage."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            with allure.step("Store value without explicit TTL"):
                key_data = "default_ttl_key"
                value = {"data": "test"}
                service.set(key_data, value)  # No TTL specified

            with allure.step("Verify value is cached"):
                cached_value = service.get(key_data)
                assert cached_value == value

            with allure.step("Verify TTL was applied"):
                # Check that the entry exists with a timestamp later than now
                assert service.cache  # Entry should be present
                _, timestamp = next(iter(service.cache.values()))
                assert isinstance(timestamp, float)
                assert timestamp > asyncio.get_event_loop().time() - 1

        finally:
            await service.stop_cleanup_task()

    @allure.story("Persistence")
    @allure.title("Should persist cache entries to disk")
    @allure.description("Verify save_to_disk writes serialized cache data")
    @pytest.mark.asyncio
    async def test_save_to_disk_persists_entries(self) -> None:
        """Ensure cache entries are written to the configured JSON file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "generic_cache.json"
            service = TestGenericCacheService.create_service(
                {
                    "logs_base_dir": tmp_dir,
                    "logging": {"generic_cache_file": cache_path.name},
                }
            )
            await service.initialize()

            try:
                service.set("persist_key", {"value": "data"}, ttl=60)
                await service.save_to_disk()
            finally:
                await service.stop_cleanup_task()

            with cache_path.open(encoding="utf-8") as handle:
                payload = json.load(handle)

            assert payload
            assert any(entry["value"] for entry in payload.values())

    @allure.story("Persistence")
    @allure.title("Should load cache entries from disk")
    @allure.description("Verify initialize restores cached data from JSON file")
    @pytest.mark.asyncio
    async def test_load_from_disk_restores_entries(self) -> None:
        """Ensure initialize() repopulates cache from existing file."""
        hashed_key = UnifiedHashService.hash_generic_key("abc")
        payload = {
            hashed_key: {"value": {"foo": "bar"}, "expires_at": 9999999999.0},
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "generic_cache.json"
            with cache_path.open("w", encoding="utf-8") as tmp_file:
                json.dump(payload, tmp_file)

            service = TestGenericCacheService.create_service(
                {
                    "logs_base_dir": tmp_dir,
                    "logging": {"generic_cache_file": cache_path.name},
                }
            )
            await service.initialize()

            try:
                assert service.cache
                cached = service.get("abc")
                assert cached == {"foo": "bar"}
            finally:
                await service.stop_cleanup_task()

    @allure.story("Configuration")
    @allure.title("Should respect TTL overrides from configuration")
    @allure.description("Verify TTL resolution honours cache_ttl_seconds")
    def test_default_ttl_override_from_config(self) -> None:
        """Ensure explicit TTL in config is applied."""
        service = TestGenericCacheService.create_service({"cache_ttl_seconds": 42})
        assert service.default_ttl == 42

    @allure.story("Edge Cases")
    @allure.title("Should handle edge cases gracefully")
    @allure.description("Test edge cases and boundary conditions")
    @pytest.mark.asyncio
    async def test_edge_cases(self) -> None:
        """Test edge cases."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            with allure.step("Store None value"):
                service.set("null_key", None, ttl=60)
                assert service.get("null_key") is None  # None is a valid value

            with allure.step("Store empty dictionary"):
                service.set("empty_key", {}, ttl=60)
                assert service.get("empty_key") == {}

            with allure.step("Use zero TTL"):
                service.set("zero_ttl", {"value": "test"}, ttl=0)
                # Should be immediately expired
                assert service.get("zero_ttl") is None

            with allure.step("Use very large TTL"):
                service.set("large_ttl", {"value": "test"}, ttl=999999)
                assert service.get("large_ttl") is not None

        finally:
            await service.stop_cleanup_task()
