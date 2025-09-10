"""Cache configuration management for hierarchical caching system.

This module provides configuration classes and factories for managing
different cache levels and their settings.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class CacheLevel(Enum):
    """Cache levels for hierarchical caching."""

    AUTO = "auto"
    L1 = "l1"  # Memory cache - fastest
    L2 = "l2"  # Persistent cache - medium speed
    L3 = "l3"  # Archive cache - slowest


@dataclass
class CacheConfiguration:
    """Configuration for cache settings."""

    # L1 (Memory) cache settings
    l1_max_entries: int = 1000
    l1_ttl_seconds: int = 3600  # 1 hour

    # L2 (Persistent) cache settings
    l2_max_entries: int = 10000
    l2_ttl_seconds: int = 86400  # 24 hours

    # L3 (Archive) cache settings
    l3_max_size: int = 100000
    l3_ttl_default: int = 604800  # 7 days

    # General settings
    compression_enabled: bool = True
    compression_threshold: int = 1024  # bytes
    circuit_breaker_enabled: bool = True

    # Eviction settings
    eviction_policy: str = "lru"  # least recently used
    eviction_batch_size: int = 100

    # Warming settings
    warming_enabled: bool = False
    warming_threads: int = 2


class CacheConfigurationFactory:
    """Factory for creating cache configurations."""

    @classmethod
    def from_legacy_config(cls, config: dict[str, Any]) -> CacheConfiguration:
        """Create cache configuration from legacy config dictionary.

        Args:
            config: Legacy configuration dictionary

        Returns:
            CacheConfiguration instance

        """
        cache_config = config.get("cache", {})

        return CacheConfiguration(
            l1_max_entries=cache_config.get("l1_max_entries", 1000),
            l1_ttl_seconds=cache_config.get("l1_ttl_seconds", 3600),
            l2_max_entries=cache_config.get("l2_max_entries", 10000),
            l2_ttl_seconds=cache_config.get("l2_ttl_seconds", 86400),
            l3_max_size=cache_config.get("l3_max_size", 100000),
            l3_ttl_default=cache_config.get("l3_ttl_default", 604800),
            compression_enabled=cache_config.get("compression_enabled", True),
            compression_threshold=cache_config.get("compression_threshold", 1024),
            circuit_breaker_enabled=cache_config.get("circuit_breaker_enabled", True),
            eviction_policy=cache_config.get("eviction_policy", "lru"),
            eviction_batch_size=cache_config.get("eviction_batch_size", 100),
            warming_enabled=cache_config.get("warming_enabled", False),
            warming_threads=cache_config.get("warming_threads", 2),
        )

    @classmethod
    def default(cls) -> CacheConfiguration:
        """Create the default cache configuration.

        Returns:
            CacheConfiguration with default settings

        """
        return CacheConfiguration()

    @classmethod
    def high_performance(cls) -> CacheConfiguration:
        """Create high-performance cache configuration.

        Returns:
            CacheConfiguration optimized for performance

        """
        return CacheConfiguration(
            l1_max_entries=5000,
            l1_ttl_seconds=7200,  # 2 hours
            l2_max_entries=50000,
            l2_ttl_seconds=172800,  # 48 hours
            compression_enabled=False,  # Disable for speed
            warming_enabled=True,
            warming_threads=4,
        )

    @classmethod
    def memory_optimized(cls) -> CacheConfiguration:
        """Create memory-optimized cache configuration.

        Returns:
            CacheConfiguration optimized for memory usage

        """
        return CacheConfiguration(
            l1_max_entries=200,
            l1_ttl_seconds=1800,
            l2_max_entries=2000,
            l2_ttl_seconds=43200,
            compression_threshold=512,
            eviction_batch_size=50,
        )
