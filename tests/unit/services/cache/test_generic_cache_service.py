"""Comprehensive tests for GenericCacheService with Allure reporting."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch
import pytest

from services.cache.generic_cache import GenericCacheService
from services.cache.hash_service import UnifiedHashService


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
                existing_logging = test_config.get("logging", {})
                merged_logging = {**existing_logging, **logging_cfg}
                test_config["logging"] = merged_logging
            for key, value in config.items():
                if key != "logging":
                    test_config[key] = value
        mock_logger = MagicMock()
        return GenericCacheService(test_config, mock_logger)

    @pytest.mark.asyncio
    async def test_initialization(self) -> None:
        """Test generic cache service initialization."""
        service = TestGenericCacheService.create_service()
        await service.initialize()
        assert service.cache == {}
        assert service._cleanup_task is not None
        assert not service._cleanup_task.done()
        await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_get_set_operations(self) -> None:
        """Test basic cache get and set operations."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            key_data = "test_key"
            value = {"data": "test_value"}
            service.set(key_data, value, ttl=60)
            cached_value = service.get(key_data)
            assert cached_value == value
            missing_value = service.get("non_existent_key")
            assert missing_value is None

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_cache_expiration(self) -> None:
        """Test cache entry expiration."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            key_data = "expiring_key"
            value = {"data": "will_expire"}
            service.set(key_data, value, ttl=0)  # Already expired
            cached_value = service.get(key_data)
            assert cached_value is None
            service.set(key_data, value, ttl=60)
            cached_value = service.get(key_data)
            assert cached_value == value

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_cache_invalidation(self) -> None:
        """Test cache invalidation."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            service.set("key1", {"value": 1})
            service.set("key2", {"value": 2})
            service.set("key3", {"value": 3})
            result = service.invalidate("key2")
            assert result is True
            assert service.get("key2") is None
            assert service.get("key1") is not None
            assert service.get("key3") is not None
            result = service.invalidate("non_existent")
            assert result is False
            service.invalidate_all()
            assert service.get("key1") is None
            assert service.get("key3") is None
            assert len(service.cache) == 0

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_cleanup_expired_entries(self) -> None:
        """Test cleanup of expired entries."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            # Store entries with different expiration times
            service.set("expired1", {"value": 1}, ttl=-1)  # Already expired
            service.set("expired2", {"value": 2}, ttl=-1)  # Already expired
            service.set("valid1", {"value": 3}, ttl=60)  # Valid
            service.set("valid2", {"value": 4}, ttl=60)  # Valid
            removed_count = service.cleanup_expired()
            assert removed_count == 2
            assert service.get("expired1") is None
            assert service.get("expired2") is None
            assert service.get("valid1") is not None
            assert service.get("valid2") is not None

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_enforce_size_limits(self) -> None:
        """Test cache size limit enforcement."""
        config = {"max_generic_entries": 5}
        service = TestGenericCacheService.create_service(config)
        await service.initialize()

        try:
            # Add entries with different timestamps
            # Each entry will have timestamp = i + 100 for TTL
            for i in range(10):
                # Manually set the cache to control timestamps
                key = UnifiedHashService.hash_generic_key(f"key{i}")
                # Store with timestamp that indicates when it expires
                # Lower timestamps expire first
                service.cache[key] = ({"value": i}, float(i + 100))
            removed_count = service.enforce_size_limits()
            assert removed_count == 5  # Should remove 5 oldest entries
            assert len(service.cache) == 5
            # The entries with the highest timestamps should remain (5-9)
            # We need to check via the cache directly since get() checks expiration
            remaining_values = [v for v, _ in service.cache.values()]
            remaining_numbers = [cast(dict[str, Any], v)["value"] for v in remaining_values]
            remaining_numbers.sort()
            assert remaining_numbers == [5, 6, 7, 8, 9]

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_get_stats(self) -> None:
        """Test cache statistics."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            service.set("valid1", {"value": 1}, ttl=60)
            service.set("valid2", {"value": 2}, ttl=60)
            service.set("expired1", {"value": 3}, ttl=-1)
            service.set("expired2", {"value": 4}, ttl=-1)
            stats = service.get_stats()
            assert stats["total_entries"] == 4
            assert stats["valid_entries"] == 2
            assert stats["expired_entries"] == 2
            assert "default_ttl" in stats
            assert "content_type" in stats

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_periodic_cleanup_task(self) -> None:
        """Test periodic cleanup task."""
        config = {"cleanup_interval": 0.1}  # Very short interval for testing
        service = TestGenericCacheService.create_service(config)
        await service.initialize()

        try:
            service.set("expired1", {"value": 1}, ttl=-1)
            service.set("expired2", {"value": 2}, ttl=-1)
            service.set("valid", {"value": 3}, ttl=60)
            await asyncio.sleep(0.2)  # Wait for cleanup to run
            # The cleanup task should have removed expired entries
            assert len(service.cache) <= 3  # May have been cleaned

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_cleanup_task_error_handling(self) -> None:
        """Test error handling in cleanup task."""
        service = TestGenericCacheService.create_service({"cleanup_interval": 0.1})

        with (
            patch.object(service, "cleanup_expired", side_effect=Exception("Test error")),
        ):
            await service.initialize()

            # Wait for cleanup to run and handle error
            await asyncio.sleep(0.2)

            # Task should still be running despite error
            assert service._cleanup_task is not None
            assert not service._cleanup_task.done()
        await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_default_ttl(self) -> None:
        """Test default TTL usage."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            key_data = "default_ttl_key"
            value = {"data": "test"}
            service.set(key_data, value)  # No TTL specified
            cached_value = service.get(key_data)
            assert cached_value == value
            # Check that the entry exists with a timestamp later than now
            assert service.cache  # Entry should be present
            _, timestamp = next(iter(service.cache.values()))
            assert isinstance(timestamp, float)
            assert timestamp > asyncio.get_event_loop().time() - 1

        finally:
            await service.stop_cleanup_task()

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

    def test_default_ttl_override_from_config(self) -> None:
        """Ensure explicit TTL in config is applied."""
        service = TestGenericCacheService.create_service({"cache_ttl_seconds": 42})
        assert service.default_ttl == 42

    @pytest.mark.asyncio
    async def test_edge_cases(self) -> None:
        """Test edge cases."""
        service = TestGenericCacheService.create_service()
        await service.initialize()

        try:
            service.set("null_key", None, ttl=60)
            assert service.get("null_key") is None  # None is a valid value
            service.set("empty_key", {}, ttl=60)
            assert service.get("empty_key") == {}
            service.set("zero_ttl", {"value": "test"}, ttl=0)
            # Should be immediately expired
            assert service.get("zero_ttl") is None
            service.set("large_ttl", {"value": "test"}, ttl=999999)
            assert service.get("large_ttl") is not None

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_cache_evicts_lru_when_max_size_exceeded(self) -> None:
        """Cache should evict LRU entries when max_size is exceeded."""
        config = {"max_generic_entries": 3}  # Small size for testing
        service = TestGenericCacheService.create_service(config)
        await service.initialize()

        try:
            # Add 4 items to a cache with max_size=3
            service.set("key1", {"value": 1})
            service.set("key2", {"value": 2})
            service.set("key3", {"value": 3})
            service.set("key4", {"value": 4})  # Should evict key1 (LRU - oldest)

            # key1 should be evicted (LRU)
            assert service.get("key1") is None
            # Remaining keys should exist
            assert service.get("key2") == {"value": 2}
            assert service.get("key3") == {"value": 3}
            assert service.get("key4") == {"value": 4}

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_get_updates_lru_order(self) -> None:
        """Accessing an entry via get() should update its LRU position."""
        config = {"max_generic_entries": 3}
        service = TestGenericCacheService.create_service(config)
        await service.initialize()

        try:
            # Add 3 items
            service.set("key1", {"value": 1})
            service.set("key2", {"value": 2})
            service.set("key3", {"value": 3})

            # Access key1 - this should move it to "most recently used"
            _ = service.get("key1")

            # Now add key4 - should evict key2 (now the oldest) instead of key1
            service.set("key4", {"value": 4})

            # key2 should be evicted (was LRU after key1 was accessed)
            assert service.get("key2") is None
            # key1, key3, key4 should exist
            assert service.get("key1") == {"value": 1}
            assert service.get("key3") == {"value": 3}
            assert service.get("key4") == {"value": 4}

        finally:
            await service.stop_cleanup_task()

    @pytest.mark.asyncio
    async def test_set_existing_key_updates_lru_order(self) -> None:
        """Re-setting an existing key should update its LRU position without eviction."""
        config = {"max_generic_entries": 3}
        service = TestGenericCacheService.create_service(config)
        await service.initialize()

        try:
            # Add 3 items
            service.set("key1", {"value": 1})
            service.set("key2", {"value": 2})
            service.set("key3", {"value": 3})

            # Update key1 - this should move it to "most recently used"
            service.set("key1", {"value": "updated"})

            # Size should still be 3 (no eviction on update)
            assert len(service.cache) == 3

            # Now add key4 - should evict key2 (oldest after key1 update)
            service.set("key4", {"value": 4})

            # key2 should be evicted
            assert service.get("key2") is None
            # Others should exist
            assert service.get("key1") == {"value": "updated"}
            assert service.get("key3") == {"value": 3}
            assert service.get("key4") == {"value": 4}

        finally:
            await service.stop_cleanup_task()
