"""Core Cache Foundation Components.

This module provides foundational cache components that form the basis of the caching system:
- Cache configuration and hierarchy levels (L1/L2/L3)
- Cache coordination and hierarchical management
- Cache protocols and interfaces
- Core utilities and helpers
- Integration bridge to new orchestrator-based architecture

These are active production components, not legacy code.
"""

import asyncio
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple, Protocol, TypeVar

_aiofiles: Any

try:
    import aiofiles as _aiofiles
except ImportError:
    _aiofiles = None


class CacheLevel(Enum):
    """Cache hierarchy levels for coordinated caching strategy."""

    L1_MEMORY = "l1_memory"  # Fast in-memory cache
    L2_DISK = "l2_disk"  # Disk-based persistence
    L3_NETWORK = "l3_network"  # Network/API cache layer


@dataclass
class CacheConfiguration:
    """Configuration for hierarchical cache system."""

    l1_max_entries: int = 1000
    l1_ttl_seconds: int = 300
    l2_max_size_mb: int = 100
    l2_ttl_seconds: int = 3600
    l3_ttl_seconds: int = 86400
    sync_interval: int = 60
    compression_enabled: bool = False


class CacheConfigurationFactory:
    """Factory for creating cache configurations."""

    @staticmethod
    def create_from_dict(config: dict[str, Any]) -> CacheConfiguration:
        """Create configuration from dictionary."""
        cache_config = config.get("caching", {})
        return CacheConfiguration(
            l1_max_entries=cache_config.get("l1_max_entries", 1000),
            l1_ttl_seconds=cache_config.get("default_ttl_seconds", 300),
            l2_max_size_mb=cache_config.get("l2_max_size_mb", 100),
            l2_ttl_seconds=cache_config.get("l2_ttl_seconds", 3600),
            l3_ttl_seconds=cache_config.get("l3_ttl_seconds", 86400),
            sync_interval=cache_config.get("sync_interval", 60),
            compression_enabled=cache_config.get("compression_enabled", False),
        )


# ==================== CACHE PROTOCOLS ====================
# Content from cache_protocol.py


T = TypeVar("T")


class CacheProtocol(Protocol[T]):
    """Protocol for cache implementations."""

    async def get(self, key: str) -> T | None:
        """Get value by key."""
        ...

    async def set(self, key: str, value: T, ttl: int | None = None) -> None:
        """Set value with optional TTL."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete key, return success status."""
        ...

    async def clear(self) -> None:
        """Clear all cached values."""
        ...


class CacheStats(NamedTuple):
    """Cache statistics."""

    hits: int
    misses: int
    size: int
    hit_ratio: float


class TTLManager:
    """TTL (Time To Live) management for cache entries."""

    def __init__(self) -> None:
        self.ttl_data: dict[str, float] = {}

    def set_ttl(self, key: str, ttl: int) -> None:
        """Set TTL for a key."""
        self.ttl_data[key] = time.time() + ttl

    def is_expired(self, key: str) -> bool:
        """Check if key has expired."""
        return False if key not in self.ttl_data else time.time() > self.ttl_data[key]

    def cleanup_expired(self) -> list[str]:
        """Return list of expired keys."""
        current_time = time.time()
        expired = [key for key, expiry in self.ttl_data.items() if current_time > expiry]
        for key in expired:
            del self.ttl_data[key]
        return expired


# ==================== CACHE COORDINATION ====================
# Content from cache_coordinator.py


class HierarchicalCacheManager:
    """Manages hierarchical cache levels with intelligent coordination."""

    def __init__(self, config: CacheConfiguration, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

        # Cache level storage
        self.l1_cache: dict[str, tuple[Any, float]] = {}  # (value, timestamp)
        self.l2_cache: dict[str, tuple[Any, float]] = {}
        self.l3_cache: dict[str, tuple[Any, float]] = {}

        # Statistics
        self.stats = {
            "l1": {"hits": 0, "misses": 0},
            "l2": {"hits": 0, "misses": 0},
            "l3": {"hits": 0, "misses": 0},
        }

        # TTL managers
        self.ttl_managers = {"l1": TTLManager(), "l2": TTLManager(), "l3": TTLManager()}

    async def get(self, key: str) -> Any | None:
        """Get value with hierarchical lookup."""
        current_time = time.time()

        # Try L1 first (fastest)
        if key in self.l1_cache:
            value, timestamp = self.l1_cache[key]
            if current_time - timestamp < self.config.l1_ttl_seconds:
                self.stats["l1"]["hits"] += 1
                return value
            del self.l1_cache[key]

        self.stats["l1"]["misses"] += 1

        # Try L2 (disk)
        if key in self.l2_cache:
            value, timestamp = self.l2_cache[key]
            if current_time - timestamp < self.config.l2_ttl_seconds:
                self.stats["l2"]["hits"] += 1
                # Promote to L1
                await self._promote_to_l1(key, value)
                return value
            del self.l2_cache[key]

        self.stats["l2"]["misses"] += 1

        # Try L3 (network/API)
        if key in self.l3_cache:
            value, timestamp = self.l3_cache[key]
            if current_time - timestamp < self.config.l3_ttl_seconds:
                self.stats["l3"]["hits"] += 1
                # Promote to L2 and L1
                await self._promote_to_l2(key, value)
                await self._promote_to_l1(key, value)
                return value
            del self.l3_cache[key]

        self.stats["l3"]["misses"] += 1
        return None

    async def set(self, key: str, value: Any, level: CacheLevel = CacheLevel.L1_MEMORY) -> None:
        """Set value at specified cache level."""
        current_time = time.time()

        if level == CacheLevel.L1_MEMORY:
            await self._set_l1(key, value, current_time)
        elif level == CacheLevel.L2_DISK:
            await self._set_l2(key, value, current_time)
        elif level == CacheLevel.L3_NETWORK:
            await self._set_l3(key, value, current_time)

    async def _set_l1(self, key: str, value: Any, timestamp: float) -> None:
        """Set value in L1 cache."""
        if len(self.l1_cache) >= self.config.l1_max_entries:
            await self._evict_l1()
        self.l1_cache[key] = (value, timestamp)

    async def _set_l2(self, key: str, value: Any, timestamp: float) -> None:
        """Set value in L2 cache."""
        await asyncio.sleep(0)  # Make function truly async
        self.l2_cache[key] = (value, timestamp)

    async def _set_l3(self, key: str, value: Any, timestamp: float) -> None:
        """Set value in L3 cache."""
        await asyncio.sleep(0)  # Make function truly async
        self.l3_cache[key] = (value, timestamp)

    async def _promote_to_l1(self, key: str, value: Any) -> None:
        """Promote value from lower level to L1."""
        await self._set_l1(key, value, time.time())

    async def _promote_to_l2(self, key: str, value: Any) -> None:
        """Promote value from L3 to L2."""
        await self._set_l2(key, value, time.time())

    async def _evict_l1(self) -> None:
        """Evict oldest entries from L1."""
        await asyncio.sleep(0)  # Make function truly async
        if not self.l1_cache:
            return

        # Remove 10% oldest entries
        entries_to_remove = max(1, len(self.l1_cache) // 10)
        sorted_entries = sorted(self.l1_cache.items(), key=lambda x: x[1][1])

        for key, _ in sorted_entries[:entries_to_remove]:
            del self.l1_cache[key]

    def get_stats(self) -> dict[str, CacheStats]:
        """Get cache statistics for all levels."""
        result = {}
        for level, stats in self.stats.items():
            total_requests = stats["hits"] + stats["misses"]
            hit_ratio = stats["hits"] / total_requests if total_requests > 0 else 0.0

            if level == "l1":
                cache_size = len(self.l1_cache)
            elif level == "l2":
                cache_size = len(self.l2_cache)
            else:  # l3
                cache_size = len(self.l3_cache)

            result[level] = CacheStats(hits=stats["hits"], misses=stats["misses"], size=cache_size, hit_ratio=hit_ratio)

        return result

    async def clear_all(self) -> None:
        """Clear all cache levels."""
        await asyncio.sleep(0)  # Make function truly async
        self.l1_cache.clear()
        self.l2_cache.clear()
        self.l3_cache.clear()

        # Reset statistics
        for level_stats in self.stats.values():
            level_stats["hits"] = 0
            level_stats["misses"] = 0


# ==================== CACHE UTILITIES ====================
# Content from cache_utils.py


class CacheFileManager:
    """Manages cache file operations with atomic writes."""

    @staticmethod
    async def save_to_file(file_path: Path, data: dict[str, Any]) -> None:
        """Save data to file atomically."""
        if _aiofiles is None:
            msg = "aiofiles is required for async file operations"
            raise ImportError(msg)

        # Type narrowing: after None check and raise, _aiofiles is guaranteed to be available
        assert _aiofiles is not None

        # Ensure directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temporary file first
        temp_file = file_path.with_suffix(".tmp")

        async with _aiofiles.open(temp_file, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))

        # Atomic rename
        temp_file.replace(file_path)

    @staticmethod
    async def load_from_file(file_path: Path) -> dict[str, Any]:
        """Load data from file."""
        if _aiofiles is None:
            msg = "aiofiles is required for async file operations"
            raise ImportError(msg)

        # Type narrowing: after None check and raise, _aiofiles is guaranteed to be available
        assert _aiofiles is not None

        if not file_path.exists():
            return {}

        try:
            async with _aiofiles.open(file_path, encoding="utf-8") as f:
                content = await f.read()
                result = json.loads(content)
                return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            logger = logging.getLogger(__name__)
            logger.warning("Failed to load cache file %s: %s", file_path, e)
            return {}

    @staticmethod
    def calculate_checksum(data: Any) -> str:
        """Calculate checksum of data using SHA-256."""
        data_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(data_str.encode()).hexdigest()


class CacheMetrics:
    """Cache performance metrics calculator."""

    def __init__(self) -> None:
        self.operation_times: list[float] = []
        self.hit_count = 0
        self.miss_count = 0
        self.error_count = 0

    def record_operation(self, duration: float) -> None:
        """Record operation duration."""
        self.operation_times.append(duration)
        # Keep only last 1000 operations
        if len(self.operation_times) > 1000:
            self.operation_times.pop(0)

    def record_hit(self) -> None:
        """Record cache hit."""
        self.hit_count += 1

    def record_miss(self) -> None:
        """Record cache miss."""
        self.miss_count += 1

    def record_error(self) -> None:
        """Record cache error."""
        self.error_count += 1

    def get_hit_ratio(self) -> float:
        """Get cache hit ratio."""
        total = self.hit_count + self.miss_count
        return self.hit_count / total if total > 0 else 0.0

    def get_avg_operation_time(self) -> float:
        """Get average operation time."""
        return sum(self.operation_times) / len(self.operation_times) if self.operation_times else 0.0

    def get_metrics_summary(self) -> dict[str, float]:
        """Get summary of all metrics."""
        return {
            "hit_ratio": self.get_hit_ratio(),
            "avg_operation_time_ms": self.get_avg_operation_time() * 1000,
            "total_operations": len(self.operation_times),
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "error_count": self.error_count,
        }


def is_cache_entry_valid(entry: tuple[Any, float], ttl: int) -> bool:
    """Check if cache entry is still valid."""
    try:
        _, timestamp = entry
        current_time = time.time()
        return current_time - timestamp < ttl
    except (ValueError, IndexError, TypeError):
        return False


def serialize_cache_key(key_data: Any) -> str:
    """Serialize various key types to string."""
    if isinstance(key_data, str):
        return key_data
    if isinstance(key_data, dict):
        return json.dumps(key_data, sort_keys=True)
    if isinstance(key_data, list | tuple):
        return json.dumps(list(key_data))
    return str(key_data)


def calculate_memory_usage(cache_dict: dict[str, Any]) -> int:
    """Calculate approximate memory usage of cache dictionary."""

    total_size = 0
    for key, value in cache_dict.items():
        total_size += sys.getsizeof(key)
        total_size += sys.getsizeof(value)

        # Recursively calculate for nested structures
        if isinstance(value, dict):
            total_size += calculate_memory_usage(value)
        elif isinstance(value, list | tuple):
            total_size += sum(sys.getsizeof(item) for item in value)

    return total_size


# ==================== NEW ARCHITECTURE INTEGRATION ====================
# Bridge to new orchestrator-based architecture


# noinspection PyUnusedImports
class CacheBridge:
    """Bridge between legacy foundation components and new orchestrator architecture.

    This bridge provides direct access to the new cache service implementation
    while maintaining compatibility with foundation components.
    """

    def __init__(self, config: dict[str, Any], logger: Any = None) -> None:
        """Initialize cache bridge.

        Args:
            config: Cache configuration dictionary
            logger: Logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

        # Lazy initialization of new architecture
        self._orchestrator: Any | None = None
        self._cache_service: Any | None = None

    @property
    def orchestrator(self) -> Any | None:
        """Get orchestrator instance (lazy initialization)."""
        if self._orchestrator is None:
            # Import here to avoid circular dependencies
            try:
                from src.infrastructure.cache.cache_orchestrator import CacheOrchestrator  # noqa: PLC0415

                self._orchestrator = CacheOrchestrator(self.config, self.logger)
                self.logger.info("✅ Bridge to orchestrator architecture established")
            except ImportError as e:
                self.logger.warning("Cannot import orchestrator: %s", e)
                self._orchestrator = None
        return self._orchestrator

    @property
    def cache_service(self) -> Any | None:
        """Get cache service instance (lazy initialization)."""
        if self._cache_service is None:
            # Import here to avoid circular dependencies
            try:
                from src.infrastructure.cache.cache_orchestrator import CacheOrchestrator  # noqa: PLC0415

                self._cache_service = CacheOrchestrator(self.config, self.logger)
                self.logger.info("✅ Bridge to orchestrator cache service established")
            except ImportError as e:
                self.logger.warning("Cannot import orchestrator cache service: %s", e)
                self._cache_service = None
        return self._cache_service

    def is_new_architecture_available(self) -> bool:
        """Check if new architecture is available."""
        return self.orchestrator is not None

    def is_cache_service_available(self) -> bool:
        """Check if cache service is available."""
        return self.cache_service is not None

    def get_integration_status(self) -> dict[str, Any]:
        """Get status of integration with new architecture."""
        return {
            "orchestrator_available": self.is_new_architecture_available(),
            "cache_service_available": self.is_cache_service_available(),
            "foundation_active": True,
            "bridge_version": "3.0",
            "migration_path": "foundation → cache_service → orchestrator",
        }


# ==================== LEGACY COMPATIBILITY ====================
# Maintain compatibility with existing code that expects certain classes

# Re-export important foundation components for backward compatibility
__all__ = [
    "CacheBridge",
    "CacheConfiguration",
    "CacheFileManager",
    "CacheLevel",
    "CacheMetrics",
    "CacheProtocol",
    "CacheStats",
    "HierarchicalCacheManager",
    "calculate_memory_usage",
    "is_cache_entry_valid",
    "serialize_cache_key",
]
