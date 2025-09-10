#!/usr/bin/env python3

"""Cache Service Module.

This module provides two caching mechanisms:
    1. An in-memory (dict) cache for generic data with TTL support (via get_async, set_async, etc.)
    2. A persistent CSV-based cache for album release years that persists data across application runs.

Features:
    - In-memory cache: supports TTL, async operations, key hashing, and automatic value computation
    - Special handling for key="ALL" to return all valid cached track objects
    - CSV cache: stores album year information persistently with artist/album lookup
    - Both caches support invalidation (individual entries or complete cache)
    - Uses SHA256 hash for album keys to avoid separator issues
    - Automatic cache synchronization to disk based on time interval
    - Atomic file writes for data safety
    - Tracking of last incremental run timestamp

Refactored: Initial asynchronous loading and saving handled in separate async methods,
called by DependencyContainer after service instantiation
"""

import asyncio
import csv
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# Enum from enum removed
# Path from pathlib removed
from typing import Any, Literal, TypeGuard, TypeVar, cast, overload

from src.services.cache.cache_config import CacheConfigurationFactory, CacheLevel
from src.services.cache.cache_coordinator import HierarchicalCacheManager

# tenacity imports removed
from src.services.cache.cache_protocol import CacheProtocol, UnifiedKeyGenerator
from src.services.cache.cache_utils import (
    BatchOperationOptimizer,
    CacheMetricsTracker,
    OptimizedHashGenerator,
)
from src.utils.core.logger import ensure_directory, get_full_log_path
from src.utils.data.models import CachedApiResult, TrackDict

# Import cache types from protocols
from src.utils.data.protocols import CacheableKey, CacheableValue

# A type variable for the cached value type
T = TypeVar("T")

# Constants for magic values - eliminates hardcoded numbers throughout the module
CACHE_ENTRY_TUPLE_SIZE: int = 2  # The number of elements in a cache entry tuple (value, expiry)
ALBUM_CACHE_TUPLE_SIZE: int = 3  # Number of elements in album cache tuple (year, artist, album)
LARGE_CACHE_THRESHOLD: int = 50000  # Threshold for large cache size recommendations
MIN_HIT_RATE_THRESHOLD: float = 0.7  # Minimum acceptable hit rate threshold
CACHE_OPTIMIZATION_FAILED: str = "Cache optimization failed"  # Error message constant
HIGH_HIT_RATE_THRESHOLD: float = 0.9  # High hit rate threshold for optimization
MEMORY_OPTIMIZATION_THRESHOLD: int = 30000  # Threshold for memory optimization recommendations

# Type aliases to avoid literal duplication and improve readability
DictStrAny = dict[str, Any]  # Standard dictionary type with string keys and any values


def is_generic_cache_entry(value: object) -> TypeGuard[tuple[object, float]]:
    """Type guard for generic cache entries (value, expiry) format."""
    try:
        # Check if it's a sequence with exactly 2 elements
        if not (hasattr(value, "__len__") and hasattr(value, "__getitem__")):
            return False

        # Type narrow to sequence-like object
        sequence_value = cast("tuple[object, ...]", value)

        return len(sequence_value) == CACHE_ENTRY_TUPLE_SIZE and isinstance(sequence_value[1], int | float)
    except (TypeError, IndexError, AttributeError):
        return False


def is_album_cache_entry(value: object) -> TypeGuard[tuple[str, str, str]]:
    """Type guard for album cache entries (year, artist, album) format."""
    try:
        # Check if it's a sequence with exactly 3 elements
        if not (hasattr(value, "__len__") and hasattr(value, "__getitem__")):
            return False

        # Type narrow to sequence-like object without assuming type
        sequence_value = cast("tuple[object, ...]", value)

        # Check length and that all elements are string
        return len(sequence_value) == ALBUM_CACHE_TUPLE_SIZE and all(isinstance(item, str) for item in sequence_value)
    except (TypeError, IndexError, AttributeError):
        return False


class LegacyCacheAdapter(CacheProtocol[Any]):
    """Legacy Cache Adapter for existing cache dictionaries.

    This adapter allows existing cache dictionaries to work with the hierarchical cache system.
    It handles both generic and album-specific cache entries with proper type validation.
    """

    def __init__(
        self,
        generic_cache: dict[str, tuple[Any, float]],
        album_cache: dict[str, tuple[str, str, str]],
        api_cache: dict[str, CachedApiResult],
    ) -> None:
        """Initialize the legacy cache adapter.

        Args:
            generic_cache: Generic cache dictionary (key -> (value, expiry))
            album_cache: Album cache dictionary (key -> (year, artist, album))
            api_cache: API cache dictionary (key -> CachedApiResult)

        """
        self.generic_cache = generic_cache
        self.album_cache = album_cache
        self.api_cache = api_cache

    async def get(self, key: str, default: object | None = None) -> object | None:
        """Get a value from the legacy cache with type validation."""
        # Check the generic cache first
        if key in self.generic_cache:
            generic_result = self.generic_cache[key]
            if is_generic_cache_entry(generic_result):
                value, expiry = generic_result
                if time.time() < expiry:
                    return value

        # Check album cache
        if key in self.album_cache:
            album_result = self.album_cache[key]
            if is_album_cache_entry(album_result):
                return album_result

        # Check API cache
        return self.api_cache.get(key, default)

    async def set(self, key: str, value: object, ttl: int | None = None) -> None:
        """Set value in appropriate legacy cache."""
        if ttl is None:
            ttl = 300  # 5-minute default

        expiry = time.time() + ttl

        # Determine which cache to use based on the value type
        if isinstance(value, CachedApiResult):
            self.api_cache[key] = value
        elif isinstance(value, tuple):
            # Type narrow to tuple for type guard validation
            tuple_value = cast("tuple[str, ...]", value)
            # Use type guard to properly validate tuple type
            if is_album_cache_entry(tuple_value):
                # Album cache entry - validated with type guard
                self.album_cache[key] = tuple_value
            else:
                # Generic cache entry for other tuple types
                self.generic_cache[key] = (value, expiry)
        else:
            # Generic cache entry
            self.generic_cache[key] = (value, expiry)

    async def invalidate(self, key: str | list[str]) -> int:
        """Remove key(s) from all legacy caches."""
        keys_to_remove = [key] if isinstance(key, str) else key
        removed_count = 0

        for k in keys_to_remove:
            if k in self.generic_cache:
                del self.generic_cache[k]
                removed_count += 1

            if k in self.album_cache:
                del self.album_cache[k]
                removed_count += 1

            if k in self.api_cache:
                del self.api_cache[k]
                removed_count += 1

        return removed_count

    async def clear(self) -> None:
        """Clear all legacy caches."""
        self.generic_cache.clear()
        self.album_cache.clear()
        self.api_cache.clear()

    async def cleanup(self) -> int:
        """Clean expired entries from legacy caches."""
        cleaned_count = 0
        current_time = time.time()

        # Clean expired entries from the generic cache
        expired_keys: list[str] = []
        for key, value in self.generic_cache.items():
            if is_generic_cache_entry(value):
                _, expiry = value
                if current_time >= expiry:
                    expired_keys.append(key)

        for key in expired_keys:
            del self.generic_cache[key]
            cleaned_count += 1

        return cleaned_count

    def get_stats(self) -> dict[str, Any]:
        """Get statistics from legacy caches."""
        return {
            "size": len(self.generic_cache) + len(self.album_cache) + len(self.api_cache),
            "max_size": 50000,  # Default max size
            "hits": 0,  # Legacy caches don't track hits
            "misses": 0,
            "sets": 0,
            "invalidations": 0,
            "cleanups": 0,
            "evictions": 0,
            "errors": 0,
            "hit_ratio": 0.0,
            "total_requests": 0,
        }


def _is_expired(expiry_time: float) -> bool:
    """Check if the cache entry has expired based on the expiry time.

    :param expiry_time: The expiry time in seconds since epoch.
    :return: True if the entry has expired, False otherwise.
    """
    return time.time() > expiry_time


class CacheService:
    """Cache Service Class.

    This class provides an in-memory cache with TTL support and a persistent CSV-based cache
    for album release years. Use hash-based keys for album data. Initializes asynchronously.
    """

    # Constants - these values define the structure and should not be changed
    CACHE_ENTRY_LENGTH = 2  # Number of elements in cache entry tuple (value, timestamp)
    SHA256_ENCODING = "utf-8"  # Encoding for hash generation
    cache_file: str  # Path to the JSON cache file for general API responses

    def __init__(
        self,
        config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Initialize the CacheService with configuration and loggers.

        Does NOT perform file loading here. Use the async initialize method.

        :param config: Configuration dictionary loaded from my-config.yaml.
        :param console_logger: Logger object for console output.
        :param error_logger: Logger object for error logging.
        """
        self.config = config
        self.console_logger = console_logger
        self.error_logger = error_logger

        # In-memory cache settings
        cache_config = config.get("caching", {})
        self.default_ttl = cache_config.get("default_ttl_seconds", config.get("cache_ttl_seconds", 900))
        # key (hash) -> (value, expiry_time)
        self.cache: dict[str, tuple[TrackDict | Any, float]] = {}

        # API results cache configuration (cache_config already defined above)

        # Cache size limits and cleanup settings
        cache_limits = cache_config.get("cache_size_limits", {})
        self.generic_cache_max_entries = cache_limits.get("generic_cache_max_entries", 50000)
        self.album_cache_max_entries = cache_limits.get("album_cache_max_entries", 10000)
        self.api_cache_max_entries = cache_limits.get("api_cache_max_entries", 50000)
        self.cleanup_interval = cache_config.get("cleanup_interval_seconds", 300)  # 5 minutes

        # Async locks for thread safety
        self._cache_lock = asyncio.Lock()
        self._album_cache_lock = asyncio.Lock()
        self._api_cache_lock = asyncio.Lock()

        # Background cleanup task
        self._cleanup_task: asyncio.Task[None] | None = None

        # CSV cache settings
        # logs_base_dir is retrieved via get_full_log_path
        self.album_cache_csv = get_full_log_path(
            config,
            "album_cache_csv",
            "csv/cache_albums.csv",
        )  # Use utility function

        # Persistent cache file for general API responses
        api_cache_filename = str(config.get("api_cache_file", "cache/cache.json"))
        self.cache_file = get_full_log_path(config, "api_cache", api_cache_filename)
        ensure_directory(path=str(Path(self.cache_file).parent))  # Ensure directory exists

        self.negative_result_ttl = cache_config.get("negative_result_ttl", 2592000)  # 30-day default
        api_cache_path = cache_config.get("api_result_cache_path", "cache/api_results.json")
        self.api_cache_file = get_full_log_path(config, "api_cache", api_cache_path)
        ensure_directory(path=str(Path(self.api_cache_file).parent))
        self.api_cache: dict[str, CachedApiResult] = {}

        # In-memory album years cache
        # key: hash of "artist|album", value: (year, artist, album)
        self.album_years_cache: dict[str, tuple[str, str, str]] = {}
        self.last_cache_sync = time.time()
        self.cache_dirty = False  # Flag indicating unsaved changes
        self.sync_interval = cache_config.get("album_cache_sync_interval", 300)  # Sync every 5 minutes by default

        # Initial album cache loading is now done in the async initialize method

        # Initialize hierarchical cache architecture (TASK-006)
        self.cache_config = CacheConfigurationFactory.from_legacy_config(config)
        self.hierarchical_manager: HierarchicalCacheManager | None = None  # Initialized in initialize()

        # Initialize optimized cache utilities (TASK-008)
        self.hash_generator = OptimizedHashGenerator()
        self.batch_optimizer = BatchOperationOptimizer()
        self.metrics_tracker = CacheMetricsTracker()

    async def initialize(self) -> None:
        """Asynchronously initializes the CacheService by loading data from the disk.

        This method must be called after instantiation.
        """
        self.console_logger.info("Initializing CacheService asynchronously...")
        await self.load_cache()
        await self._load_album_years_cache()
        await self._load_api_cache()

        # Initialize hierarchical cache manager (TASK-006)
        self.hierarchical_manager = HierarchicalCacheManager(config=self.cache_config, logger=self.console_logger)

        # Create a legacy cache adapter for existing caches integration
        legacy_adapter = LegacyCacheAdapter(
            generic_cache=self.cache,
            album_cache=self.album_years_cache,
            api_cache=self.api_cache,
        )
        self.hierarchical_manager.register_l3_cache("legacy", legacy_adapter)

        # Start a background cleanup task
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        self.console_logger.info("CacheService asynchronous initialization complete.")

    async def _periodic_cleanup(self) -> None:
        """Background task to clean expired entries from all caches.

        Runs every cleanup_interval seconds to remove expired entries and
        enforce size limits with LRU eviction.
        """
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)

                # Clean expired entries from all caches
                total_cleaned = 0

                # Clean generic cache
                cleaned_generic = await self._cleanup_expired_entries()
                total_cleaned += cleaned_generic

                # Clean API cache (has its own TTL logic)
                cleaned_api = await self._cleanup_expired_api_entries()
                total_cleaned += cleaned_api

                # Enforce size limits with LRU eviction
                evicted_count = await self._enforce_cache_size_limits()
                total_cleaned += evicted_count

                if total_cleaned > 0:
                    self.console_logger.info(
                        "Background cleanup: removed %d entries (expired + evicted)",
                        total_cleaned,
                    )

            except asyncio.CancelledError:
                self.console_logger.info("Background cleanup task cancelled")
                raise  # Re-raise CancelledError after cleanup
            except (OSError, RuntimeError, MemoryError):
                self.error_logger.exception("Error in background cache cleanup")
                # Continue running despite errors
                cleanup_delay = self.config.get("caching", {}).get("cleanup_error_retry_delay", 60)
                await asyncio.sleep(cleanup_delay)  # Wait before retry

    async def _cleanup_expired_entries(self) -> int:
        """Clean expired entries from the generic cache.

        Returns:
            int: Number of entries removed

        """
        async with self._cache_lock:
            now = time.time()
            expired_keys = [key for key, (_, expiry) in self.cache.items() if now > expiry]

            for key in expired_keys:
                del self.cache[key]

            return len(expired_keys)

    async def _cleanup_expired_api_entries(self) -> int:
        """Clean expired entries from the API cache.

        Returns:
            int: Number of entries removed

        """
        async with self._api_cache_lock:
            now = time.time()
            expired_keys: list[str] = []

            for key, cached_result in self.api_cache.items():
                if cached_result.ttl is not None:
                    elapsed = now - cached_result.timestamp
                    if elapsed > cached_result.ttl:
                        expired_keys.append(key)

            for key in expired_keys:
                del self.api_cache[key]

            if expired_keys:
                await self._save_api_cache()

            return len(expired_keys)

    async def _enforce_cache_size_limits(self) -> int:
        """Enforce cache size limits using LRU eviction.

        Returns:
            int: Total number of entries evicted

        """
        total_evicted = 0

        # Enforce generic cache limit
        async with self._cache_lock:
            if len(self.cache) > self.generic_cache_max_entries:
                # Sort by expiry time (the oldest first for LRU approximation)
                sorted_items = sorted(self.cache.items(), key=lambda item: item[1][1])  # Sort by expiry time

                # Calculate how many to evict
                to_evict = len(self.cache) - self.generic_cache_max_entries

                # Remove oldest entries
                for i in range(to_evict):
                    key_to_remove = sorted_items[i][0]
                    del self.cache[key_to_remove]
                    total_evicted += 1

        # Enforce album cache limit
        async with self._album_cache_lock:
            if len(self.album_years_cache) > self.album_cache_max_entries:
                # Convert to a list of items and sort by insertion order approximation
                # (Since we don't track access time, use dict order as a proxy for LRU)
                cache_items = list(self.album_years_cache.items())
                to_evict = len(cache_items) - self.album_cache_max_entries

                # Remove oldest entries (first in dict order)
                for i in range(to_evict):
                    key_to_remove = cache_items[i][0]
                    del self.album_years_cache[key_to_remove]
                    total_evicted += 1
                    self.cache_dirty = True

        # Enforce API cache limit
        async with self._api_cache_lock:
            if len(self.api_cache) > self.api_cache_max_entries:
                # Sort by timestamp (oldest first)
                sorted_api_items = sorted(self.api_cache.items(), key=lambda item: item[1].timestamp)

                to_evict = len(self.api_cache) - self.api_cache_max_entries

                # Remove oldest entries
                for i in range(to_evict):
                    key_to_remove = sorted_api_items[i][0]
                    del self.api_cache[key_to_remove]
                    total_evicted += 1

                if total_evicted > 0:
                    await self._save_api_cache()

        return total_evicted

    @staticmethod
    def generate_album_key(artist: str, album: str) -> str:
        """Generate a unique hash key for an album based on artist and album names.

        TASK-004: Consolidated to use UnifiedKeyGenerator for consistent hashing
        across all services. This eliminates duplicate implementations.

        :param artist: The artist name.
        :param album: The album name.
        :return: A SHA256 hash string.
        """
        return UnifiedKeyGenerator.album_key(artist, album)

    @staticmethod
    def _hash_key(key_data: CacheableKey) -> str:
        """Generate a SHA256 hash for a general cache key.

        TASK-004: Consolidated to use UnifiedKeyGenerator for consistent hashing
        across all services. This eliminates duplicate implementations.

        :param key_data: The key data to hash (can be a tuple or any hashable object).
        :return: The hashed key string.
        """
        return UnifiedKeyGenerator.hash_key(key_data)

    def set(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Set a value in the in-memory cache with an optional TTL."""
        key = CacheService._hash_key(key_data)
        ttl_value = ttl if ttl is not None else self.default_ttl
        expiry_time = time.time() + ttl_value
        self.cache[key] = (value, expiry_time)

    async def set_async(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Asynchronously set a value in the in-memory cache with an optional TTL.

        Thread-safe implementation with async locking and size limit enforcement.

        :param key_data: The key data to hash (can be a tuple or any hashable object).
        :param value: The value to store in the cache.
        :param ttl: The time-to-live in seconds for the cache entry.
        """
        async with self._cache_lock:
            key = CacheService._hash_key(key_data)
            ttl_value = ttl if ttl is not None else self.default_ttl
            expiry_time = time.time() + ttl_value

            # Add/update the entry
            self.cache[key] = (value, expiry_time)

            # Enforce size limits immediately if exceeded (no lock needed, already in lock)
            if len(self.cache) > self.generic_cache_max_entries:
                # Sort by expiry time (the oldest first for LRU approximation)
                sorted_items = sorted(self.cache.items(), key=lambda item: item[1][1])  # Sort by expiry time

                # Calculate how many to evict
                to_evict = len(self.cache) - self.generic_cache_max_entries

                # Remove oldest entries
                for i in range(to_evict):
                    key_to_remove = sorted_items[i][0]
                    del self.cache[key_to_remove]

    @overload
    async def get_async(self, key_data: Literal["ALL"], compute_func: None = None) -> list[TrackDict]: ...

    @overload
    async def get_async(
        self,
        key_data: str,
        compute_func: None = None,
    ) -> list[TrackDict] | None: ...

    @overload
    async def get_async(
        self,
        key_data: CacheableKey,
        compute_func: Callable[[], "asyncio.Future[CacheableValue]"] | None = None,
    ) -> CacheableValue: ...

    async def get_async(
        self,
        key_data: CacheableKey,
        compute_func: Callable[[], "asyncio.Future[CacheableValue]"] | None = None,
    ) -> list[TrackDict] | CacheableValue:
        """Asynchronously fetch a value from the in-memory cache or compute it if needed.

        Thread-safe implementation with async locking.
        - If `key_data == "ALL"`, returns a list of *valid* cached track objects.
        - Otherwise uses a hashed key for the dictionary-based lookup.
        - If a value is not found or expired, and compute_func is provided, it calls compute_func().
        """
        async with self._cache_lock:
            if key_data == "ALL":
                return self._get_all_tracks()

            cached_value: CacheableValue = self._get_single_cache_item(key_data)
            if cached_value is not None:
                return cached_value

            # If not found in cache or expired, compute if compute_func is given
            if compute_func is not None:
                self.console_logger.debug("Computing value for %s", key_data)

        # Handle compute_func outside the lock to prevent deadlock
        if compute_func is not None:
            value = await compute_func()
            if value is not None:
                await self.set_async(key_data, value)  # Use async version
            return value
        return None

    def _get_all_tracks(self) -> list[TrackDict]:
        """Get all valid cached track objects and remove expired entries."""
        tracks: list[TrackDict] = []
        now_ts: float = time.time()
        expired_keys: list[str] = []

        # Iterate through items to check for expiry and validity
        for key, (val, expiry) in self.cache.items():
            if now_ts >= expiry:
                expired_keys.append(key)
                continue

            if isinstance(val, dict) and "id" in val:
                tracks.append(cast("TrackDict", val))

        # Remove expired entries after iteration
        for key in expired_keys:
            del self.cache[key]

        return tracks

    def _get_single_cache_item(self, key_data: CacheableKey) -> CacheableValue:
        """Get a single cache item by key, returning None if not found or expired."""
        key: str = CacheService._hash_key(key_data)
        if key in self.cache:
            value, expiry_time = self.cache[key]
            if not _is_expired(expiry_time):
                self.console_logger.debug("Cache hit for %s", key_data)
                return value
            self.console_logger.debug("Cache expired for %s", key_data)
            del self.cache[key]  # remove expired entry
        return None

    def invalidate(self, key_data: CacheableKey) -> None:
        """Invalidate a single cache entry by key or clear all entries if key_data == "ALL".

        This is a synchronous operation on the in-memory cache.

        :param key_data: The key data is to invalidate, or "ALL" to clear all entries.
        """
        if key_data == "ALL":
            # Clear all generic cache entries
            self.cache.clear()
            self.console_logger.info("Invalidated ALL in-memory generic cache entries.")
            return
        key = CacheService._hash_key(key_data)
        if key in self.cache:
            del self.cache[key]
            self.console_logger.info("In-memory generic cache invalidated for key: %s", key_data)

    def clear(self) -> None:
        """Clear the entire in-memory generic cache.

        This is a synchronous operation.
        """
        self.cache.clear()
        self.console_logger.info("All in-memory generic cache entries cleared.")

    def _validate_cache_entry(self, k: str, v: list[Any]) -> tuple[str, tuple[Any, float]] | None:
        """Validate a single cache entry and return processed entry or None."""
        # Ensure v has the correct structure
        if len(v) != self.CACHE_ENTRY_LENGTH:
            return None
        # Check if the second element is a number
        if not isinstance(v[1], int | float):
            return None
        # Check if the entry is expired
        expiry_time = float(v[1])
        return None if _is_expired(expiry_time) else (k, (v[0], expiry_time))

    async def load_cache(self) -> None:
        """Load the persistent generic cache from the disk asynchronously."""
        loop = asyncio.get_event_loop()

        def blocking_load() -> dict[str, tuple[Any, float]]:
            """Blocking function to load the generic cache from disk."""
            if not Path(self.cache_file).exists():
                return {}
            try:
                with Path(self.cache_file).open(encoding="utf-8") as f:
                    data: dict[str, list[Any]] = json.load(f)
                result: dict[str, tuple[Any, float]] = {}
                for k, v in data.items():
                    entry = self._validate_cache_entry(k, v)
                    if entry:
                        result[entry[0]] = entry[1]
            except (OSError, ValueError, KeyError):
                self.error_logger.exception("Error loading cache from %s", self.cache_file)
                return {}

            return result

        self.cache = await loop.run_in_executor(None, blocking_load)
        self.console_logger.info("Loaded %d cached entries from %s", len(self.cache), self.cache_file)

    async def save_cache(self) -> None:
        """Persist the generic cache to disk asynchronously."""
        loop = asyncio.get_event_loop()

        def blocking_save() -> None:
            """Blocking function to save the generic cache to disk."""
            try:
                Path(self.cache_file).parent.mkdir(parents=True, exist_ok=True)
                data: dict[str, list[Any]] = {k: [v, exp] for k, (v, exp) in self.cache.items() if not _is_expired(exp)}
                with Path(self.cache_file).open("w", encoding="utf-8") as f:
                    json.dump(data, f)
            except (asyncio.CancelledError, OSError, RuntimeError):  # pragma: no cover - best effort logging
                # Use logger instead of print - fallback error handling
                if hasattr(self, "error_logger"):
                    self.error_logger.exception("ERROR saving cache to %s", self.cache_file)
                else:
                    # Final fallback using logging module directly
                    logging.getLogger(__name__).exception("ERROR saving cache to %s", self.cache_file)

        await loop.run_in_executor(None, blocking_save)

    async def _load_album_years_cache(self) -> None:
        """Load album years cache from CSV file into memory asynchronously.

        Uses loop.run_in_executor for blocking file operations.
        Read artist, album, and year from CSV and stores using hash keys.
        """
        self.album_years_cache.clear()  # Clear the existing cache before loading
        loop = asyncio.get_event_loop()

        def blocking_load() -> dict[str, tuple[str, str, str]]:
            """Blocking function to load album years cache from CSV file."""
            try:
                Path(self.album_cache_csv).parent.mkdir(parents=True, exist_ok=True)
                if not Path(self.album_cache_csv).exists():
                    self.console_logger.info(
                        "Album-year cache file not found, will create at: %s",
                        self.album_cache_csv,
                    )
                    return {}

                return self._read_csv_file()

            except (asyncio.CancelledError, OSError, RuntimeError):
                self.error_logger.exception("Error loading album years cache from %s", self.album_cache_csv)
                return {}

        # Run the blocking load operation in the default executor
        self.album_years_cache = await loop.run_in_executor(None, blocking_load)
        self.console_logger.info(
            "Loaded %d album years into memory cache from %s",
            len(self.album_years_cache),
            self.album_cache_csv,
        )

    def _read_csv_file(self) -> dict[str, tuple[str, str, str]]:
        """Read and validate CSV file data, returning album data dictionary."""
        album_data: dict[str, tuple[str, str, str]] = {}

        with Path(self.album_cache_csv).open(encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # Validate CSV headers
            fieldnames_list: list[str] | None = list(reader.fieldnames) if reader.fieldnames else None
            if not self._validate_csv_headers(fieldnames_list):
                return album_data

            # Process each row
            for row in reader:
                self._process_csv_row(row, album_data)

        return album_data

    def _validate_csv_headers(self, fieldnames: list[str] | None) -> bool:
        """Validate CSV headers contain required fields."""
        if not fieldnames:
            return False

        fieldnames_list: list[str] = list(fieldnames) if fieldnames else []
        required_fields = {"artist", "album", "year"}

        if not required_fields.issubset(fieldnames_list):
            missing = required_fields - set(fieldnames_list)
            self.error_logger.warning(
                "Album cache CSV header missing required fields in %s. Missing: %s. Found: %s. Skipping load.",
                self.album_cache_csv,
                ", ".join(missing),
                fieldnames_list,
            )
            return False

        return True

    def _process_csv_row(self, row: dict[str, str], album_data: dict[str, tuple[str, str, str]]) -> None:
        """Process a single CSV row and add to album data if valid."""
        artist: str = row.get("artist", "").strip()
        album: str = row.get("album", "").strip()
        year: str = row.get("year", "").strip()

        if artist and album and year:
            key_hash: str = CacheService.generate_album_key(artist, album)
            album_data[key_hash] = (year, artist, album)
        else:
            self.error_logger.warning("Skipping malformed row in album cache file: %s", row)

    async def _sync_cache_if_needed(self, *, force: bool = False) -> None:
        """Synchronize cache to disk if needed based on time interval or force flag.

        :param force: If True, force synchronization regardless of time interval
        """
        now = time.time()
        if force or (self.cache_dirty and now - self.last_cache_sync >= self.sync_interval):
            self.console_logger.info(
                "Syncing album years cache to disk (force=%s, dirty=%s, interval_elapsed=%s)...",
                force,
                self.cache_dirty,
                now - self.last_cache_sync >= self.sync_interval,
            )
            await self._save_cache_to_disk()
            self.last_cache_sync = now
            self.cache_dirty = False

    def _log_with_fallback(self, level: str, message: str, *args: str | float) -> None:
        """Log message with fallback to standard logging."""
        logger_name = "error_logger" if level in {"error", "exception"} else "console_logger"
        if logger := getattr(self, logger_name, None):
            getattr(logger, level)(message, *args)
        else:
            getattr(logging.getLogger(__name__), level)(message, *args)

    def _cleanup_temp_file(self, temp_file: str) -> None:
        """Clean up temporary file safely."""
        if Path(temp_file).exists():
            try:
                Path(temp_file).unlink()
            except OSError:
                self._log_with_fallback("warning", "Could not remove temp file %s", temp_file)

    @staticmethod
    def _write_csv_data(file_path: str, items: list[tuple[str, str, str]]) -> None:
        """Write album cache data to CSV file."""
        with Path(file_path).open("w", newline="", encoding="utf-8") as f:
            fieldnames = ["artist", "album", "year"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for year, artist, album in items:
                writer.writerow({"artist": artist, "album": album, "year": year})

    async def _save_cache_to_disk(self) -> None:
        """Save the in-memory album years cache to disk asynchronously."""
        temp_file = f"{self.album_cache_csv}.tmp"
        loop = asyncio.get_event_loop()

        def blocking_save() -> None:
            """Blocking function to save the album years cache to disk."""
            try:
                Path(self.album_cache_csv).parent.mkdir(parents=True, exist_ok=True)
                items_to_save = list(self.album_years_cache.values())
                self._write_csv_data(temp_file, items_to_save)
                Path(temp_file).replace(self.album_cache_csv)
            except Exception:
                self._log_with_fallback("exception", "ERROR saving album years cache to %s", self.album_cache_csv)
                self._cleanup_temp_file(temp_file)
                raise

        try:
            await loop.run_in_executor(None, blocking_save)
            self.console_logger.info("Synchronized %d album years to disk", len(self.album_years_cache))
        except (asyncio.CancelledError, OSError, RuntimeError):
            self.error_logger.exception("Error synchronizing album years cache to disk")

    async def initialize_album_cache_csv(self) -> None:
        """If the album cache CSV file does not exist, create it with the header row asynchronously.

        Ensures the CSV has 'artist', 'album', 'year' columns.
        Use loop.run_in_executor for blocking file operations.
        """
        loop = asyncio.get_event_loop()

        def blocking_init() -> None:
            """Blocking function to initialize the album cache CSV file."""
            # Guard clause: return immediately if the file already exists
            if Path(self.album_cache_csv).exists():
                return
            self.console_logger.info("Creating album-year CSV cache: %s", self.album_cache_csv)
            try:
                # Ensure a directory exists before creating the file
                Path(self.album_cache_csv).parent.mkdir(parents=True, exist_ok=True)
                # Define fieldnames for the CSV file
                fieldnames = ["artist", "album", "year"]
                with Path(self.album_cache_csv).open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
            except Exception:
                if hasattr(self, "error_logger"):
                    self.error_logger.exception("ERROR initializing album cache CSV %s", self.album_cache_csv)
                else:
                    logging.getLogger(__name__).exception("ERROR initializing album cache CSV %s", self.album_cache_csv)
                raise  # Re-raise to propagate

        # Run the blocking initialization operation
        try:
            await loop.run_in_executor(None, blocking_init)
        except (asyncio.CancelledError, OSError, RuntimeError):
            self.error_logger.exception("Error initializing album cache CSV")

    def _validate_timestamp_config(self) -> str | None:
        """Validate timestamp configuration and return the file path key.

        Returns:
            str | None: The file path key if valid, None if missing config.

        """
        logging_config = self.config.get("logging", {})
        last_file_key = logging_config.get("last_incremental_run_file")

        if not last_file_key:
            self._log_with_fallback("warning", "Config key 'logging.last_incremental_run_file' is missing.")
            return None

        return str(last_file_key)

    def _read_timestamp_file(self, file_path: str) -> datetime:
        """Read timestamp from file with error handling.

        Args:
            file_path: Path to the timestamp file.

        Returns:
            datetime: Parsed timestamp or min datetime if error.

        """
        if not Path(file_path).exists():
            return datetime.min.replace(tzinfo=UTC)

        try:
            with Path(file_path).open(encoding="utf-8") as f:
                last_run_str = f.read().strip()
            return datetime.strptime(last_run_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except (ValueError, OSError):
            self._log_with_fallback("exception", "ERROR reading timestamp from %s", file_path)
            return datetime.min.replace(tzinfo=UTC)

    async def get_last_run_timestamp(self) -> datetime:
        """Get the timestamp of the last incremental run asynchronously.

        Uses loop.run_in_executor for blocking file operations.

        Returns:
            datetime: The timestamp of the last incremental run, or datetime.min if not found or an error occurs.

        """
        loop = asyncio.get_event_loop()

        def blocking_read_timestamp() -> datetime:
            """Blocking function to read the last run timestamp from a file."""
            if not self._validate_timestamp_config():
                return datetime.min.replace(tzinfo=UTC)

            last_file = get_full_log_path(
                self.config,
                "last_incremental_run_file",
                "last_incremental_run.log",
            )

            return self._read_timestamp_file(last_file)

        try:
            timestamp = await loop.run_in_executor(None, blocking_read_timestamp)
        except (OSError, RuntimeError, asyncio.CancelledError):
            self.error_logger.exception("Error getting last run timestamp from executor")
            return datetime.min.replace(tzinfo=UTC)

        if timestamp != datetime.min.replace(tzinfo=UTC):
            self.console_logger.debug("Successfully read last run timestamp: %s", timestamp)
        return timestamp

    async def get_album_year_from_cache(self, artist: str, album: str) -> str | None:
        """Get album year from the in-memory cache for a given artist and album.

        Uses the hash key for lookup. Returns the year if found, None otherwise.
        Automatically syncs cache if needed before lookup.

        :param artist: The artist name.
        :param album: The album name.
        :return: The year of the album as a string, or None if not found.
        """
        # Generate the hash key for the album
        key_hash: str = CacheService.generate_album_key(artist, album)
        self.console_logger.info(
            "[YEAR_DEBUG] Cache lookup: artist='%s' album='%s' key_hash='%s'",
            artist,
            album,
            key_hash,
        )

        # Sync cache if needed (this will save to disk if dirtily/interval met)
        # Note: Initial loading is done in async initializing, not here.
        await self._sync_cache_if_needed()

        async with self._album_cache_lock:
            # Check if the hash key exists in the in-memory cache
            if key_hash in self.album_years_cache:
                # The value is a tuple (year, artist, album)
                year, cached_artist, cached_album = self.album_years_cache[key_hash]
                self.console_logger.info(
                    "[YEAR_DEBUG] Album year cache hit for '%s - %s': year=%s, cached_artist='%s', cached_album='%s'",
                    artist,
                    album,
                    year,
                    cached_artist,
                    cached_album,
                )
                return year
        self.console_logger.info("[YEAR_DEBUG] Album year cache miss for '%s - %s'", artist, album)
        return None

    async def store_album_year_in_cache(self, artist: str, album: str, year: str) -> None:
        """Store or update an album year in the in-memory cache and mark it for disk sync.

        Uses the hash key for storage. The actual disk writing happens based on sync interval or forced sync.

        :param artist: The artist name.
        :param album: The album name.
        :param year: The album released year.
        :return: None
        """
        # Generate the hash key for the album
        key_hash: str = CacheService.generate_album_key(artist, album)
        self.console_logger.info(
            "[YEAR_DEBUG] Cache store: artist='%s' album='%s' year='%s' key_hash='%s'",
            artist,
            album,
            year,
            key_hash,
        )

        async with self._album_cache_lock:
            # Store the year, original artist, and original album in the in-memory cache value
            self.album_years_cache[key_hash] = (
                year.strip(),
                artist.strip(),
                album.strip(),
            )
            self.console_logger.info(
                "[YEAR_DEBUG] Stored in cache: key='%s' value=(%s, %s, %s)",
                key_hash,
                year.strip(),
                artist.strip(),
                album.strip(),
            )
            self.cache_dirty = True

            # Sync with disk if needed
            await self._sync_cache_if_needed()

        self.console_logger.debug("Album year stored in cache for '%s - %s': %s", artist, album, year)

    async def invalidate_album_cache(self, artist: str, album: str) -> None:
        """Invalidate the album-year cache for a given artist and album.

        Removes the entry from the in-memory cache using the hash key.
        Mark the cache as dirty for eventual disk sync.
        This is now an async, thread-safe operation with proper locking.

        :param artist: The artist name.
        :param album: The album name.
        :return: None
        """
        async with self._album_cache_lock:
            # Generate the hash key for the album
            key_hash: str = CacheService.generate_album_key(artist, album)

            # Atomic remove operation from the in-memory cache if the hash key exists
            if key_hash in self.album_years_cache:
                del self.album_years_cache[key_hash]
                self.cache_dirty = True
                self.console_logger.info("Invalidated album-year cache for: %s - %s", artist, album)
        # No need to save immediately here, _sync_cache_if_needed will handle it based on interval/force

    async def invalidate_all_albums(self) -> None:
        """Invalidate the entire album-year cache.

        Clears the in-memory cache and marks it for synchronization.
        This is now an async, thread-safe operation with proper locking.
        """
        async with self._album_cache_lock:
            # Atomic clear operation on the in-memory cache
            if self.album_years_cache:
                self.album_years_cache.clear()
                self.cache_dirty = True
                self.console_logger.info("Entire album-year cache cleared.")
        # No need to save immediately here, _sync_cache_if_needed will handle it based on interval/force

    async def sync_cache(self) -> None:
        """Force synchronization of the in-memory album cache to disk.

        Useful before the application shutdown to ensure data persistence.
        """
        await self._sync_cache_if_needed(force=True)

    # --- API Results Cache Methods ---

    async def get_cached_api_result(self, artist: str, album: str, source: str) -> CachedApiResult | None:
        """Get cached API result for an album from a specific source.

        Args:
            artist: Artist name
            album: Album name
            source: API source ("musicbrainz", "discogs", "lastfm")

        Returns:
            Cached result if valid, None if expired or not found

        """
        cache_key = CacheService._generate_api_cache_key(artist, album, source)
        async with self._api_cache_lock:
            if cached := self.api_cache.get(cache_key):
                # Check if TTL expired
                ttl = cached.ttl
                if ttl is not None:
                    elapsed = time.time() - cached.timestamp
                    if elapsed > ttl:
                        # Expired, remove from cache
                        del self.api_cache[cache_key]
                        self.console_logger.debug("API cache expired for %s - %s (%s)", artist, album, source)
                        return None
                return cached
            return None

    async def set_cached_api_result(
        self,
        artist: str,
        album: str,
        source: str,
        year: str | None,
        *,
        metadata: dict[str, Any] | None = None,
        is_negative: bool = False,
    ) -> None:
        """Cache an API result for an artist/album from a specific source.

        Args:
            artist: Artist name
            album: Album name
            source: API source identifier
            year: Year to cache (None for negative cache)
            metadata: Optional metadata about the result
            is_negative: Whether this is a negative cache entry

        """
        cache_key = CacheService._generate_api_cache_key(artist, album, source)

        ttl = self.negative_result_ttl if is_negative else None

        async with self._api_cache_lock:
            self.api_cache[cache_key] = CachedApiResult(
                artist=artist,
                album=album,
                year=year,
                source=source,
                timestamp=time.time(),
                ttl=ttl,
                metadata=metadata or {},
            )

            # Save to disk asynchronously
            await self._save_api_cache()

        self.console_logger.debug(
            "Cached API result for %s - %s (%s): year=%s, ttl=%s",
            artist,
            album,
            source,
            year,
            ttl,
        )

    @staticmethod
    def _generate_api_cache_key(artist: str, album: str, source: str) -> str:
        """Generate the cache key for API results.

        TASK-004: Consolidated to use UnifiedKeyGenerator for consistent hashing
        across all services. This eliminates duplicate implementations.
        """
        return UnifiedKeyGenerator.api_key(source, artist, album)

    async def invalidate_api_cache_for_album(self, artist: str, album: str) -> None:
        """Invalidate API cache entries for a specific album across all sources.

        Args:
            artist: Artist name
            album: Album name

        """
        sources = ["musicbrainz", "discogs", "lastfm"]
        invalidated = 0

        for source in sources:
            cache_key = CacheService._generate_api_cache_key(artist, album, source)
            if cache_key in self.api_cache:
                del self.api_cache[cache_key]
                invalidated += 1

        if invalidated > 0:
            await self._save_api_cache()
            self.console_logger.info("Invalidated %d API cache entries for '%s - %s'", invalidated, artist, album)

    async def _load_api_cache(self) -> None:
        """Load API cache from disk."""
        if not Path(self.api_cache_file).exists():
            return
        try:
            async with asyncio.Lock():

                def load_json() -> dict[str, Any]:
                    """Load JSON data from API cache file."""
                    with Path(self.api_cache_file).open(encoding="utf-8") as f:
                        json_data: Any = json.load(f)
                        if not isinstance(json_data, dict):
                            msg = f"Expected dict, got {type(json_data)}"
                            raise TypeError(msg)
                        return cast("DictStrAny", json_data)

                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(None, load_json)

                # Convert loaded data to CachedApiResult objects
                self.api_cache = {}
                for k, v in data.items():
                    required_keys = ["artist", "album", "year", "source", "timestamp"]
                    if isinstance(v, dict) and all(key in v for key in required_keys):
                        # Type narrowing for pyright
                        cache_entry = cast("DictStrAny", v)
                        # Type-safe extraction with explicit casting
                        artist = cast("str", cache_entry["artist"])
                        album = cast("str", cache_entry["album"])
                        year = cast("str | None", cache_entry["year"])
                        source = cast("str", cache_entry["source"])
                        timestamp = cast("float", cache_entry["timestamp"])
                        ttl = cast("int | None", cache_entry.get("ttl", None))
                        metadata = cast("DictStrAny", cache_entry.get("metadata", {}))
                        api_response = cast(
                            "dict[str, Any] | None",
                            cache_entry.get("api_response", None),
                        )

                        # Create CachedApiResult from dict
                        self.api_cache[k] = CachedApiResult(
                            artist=artist,
                            album=album,
                            year=year,
                            source=source,
                            timestamp=timestamp,
                            ttl=ttl,
                            metadata=metadata,
                            api_response=api_response,
                        )

                self.console_logger.info(
                    "Loaded %d API cache entries from %s",
                    len(self.api_cache),
                    self.api_cache_file,
                )
        except (OSError, KeyError, ValueError):
            self.error_logger.exception("Failed to load API cache from %s", self.api_cache_file)
            self.api_cache = {}

    async def _save_api_cache(self) -> None:
        """Save API cache to disk."""
        try:
            async with asyncio.Lock():

                def save_json() -> None:
                    """Save JSON data to API cache file atomically."""

                    # Convert CachedApiResult objects to dicts (Pydantic v1/v2 compatibility)
                    def serialize_model(model: CachedApiResult) -> dict[str, Any]:
                        """Serialize Pydantic model (v2)."""
                        return model.model_dump()

                    cache_data: dict[str, dict[str, Any]] = {k: serialize_model(v) for k, v in self.api_cache.items()}
                    # Write to the temporary file first
                    temp_file = f"{self.api_cache_file}.tmp"
                    with Path(temp_file).open("w", encoding="utf-8") as f:
                        json.dump(cache_data, f, indent=2)
                    # Atomic rename
                    Path(temp_file).replace(self.api_cache_file)

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, save_json)

                self.console_logger.debug(
                    "Saved %d API cache entries to %s",
                    len(self.api_cache),
                    self.api_cache_file,
                )
        except (OSError, json.JSONDecodeError):
            self.error_logger.exception("Failed to save API cache to %s", self.api_cache_file)

    async def stop_cleanup_task(self) -> None:
        """Stop the background cleanup task gracefully.

        Should be called during service shutdown
        """
        if self._cleanup_task and not self._cleanup_task.done():
            self.console_logger.info("Stopping background cache cleanup task...")
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                self.console_logger.info("Background cleanup task stopped")
                raise  # Re-raise CancelledError after cleanup
            except (OSError, RuntimeError):
                self.error_logger.exception("Error stopping cleanup task")
            finally:
                self._cleanup_task = None

    # ==================== HIERARCHICAL CACHE METHODS (TASK-006) ====================
    # These methods provide hierarchical cache functionality while preserving
    # backward compatibility with existing APIs.

    def get_unified_stats(self) -> dict[str, Any]:
        """Get unified statistics from all cache levels.

        Returns:
            Dict containing legacy stats and hierarchical stats

        """
        legacy_stats = {
            "generic_cache_size": len(self.cache),
            "album_cache_size": len(self.album_years_cache),
            "api_cache_size": len(self.api_cache),
        }

        if self.hierarchical_manager:
            hierarchical_stats = self.hierarchical_manager.get_stats()
            performance_improvement = self._calculate_performance_improvement()
            return {
                "legacy_stats": legacy_stats,
                "hierarchical_stats": hierarchical_stats,
                "performance_improvement": performance_improvement,
            }
        return {"legacy_stats": legacy_stats}

    def _calculate_performance_improvement(self) -> dict[str, Any]:
        """Calculate performance improvements from hierarchical cache."""
        if not self.hierarchical_manager:
            return {"available": False}

        stats = self.hierarchical_manager.get_stats()

        # Calculate hit rate improvements
        hierarchical_hit_rates = stats.get("hierarchical_stats", {}).get("hit_ratios", {})
        overall_hit_rate = stats.get("hierarchical_stats", {}).get("overall_hit_ratio", 0.0)

        # Estimate memory reduction through hierarchical coordination
        total_entries = len(self.cache) + len(self.album_years_cache) + len(self.api_cache)
        estimated_reduction = min(0.5, total_entries * 0.0001)  # Conservative estimate

        return {
            "available": True,
            "hit_rate_improvement": overall_hit_rate,
            "memory_reduction_estimate": estimated_reduction,
            "hierarchical_levels_active": len(hierarchical_hit_rates),
            "coordination_benefits": "L1/L2/L3 promotion and cascade invalidation active",
        }

    async def optimize_cache_performance(self) -> dict[str, Any]:
        """Optimize cache performance using hierarchical coordination.

        Returns:
            Dict with optimization results and recommendations

        """
        if not self.hierarchical_manager:
            return {
                "optimization_complete": False,
                "error": "Hierarchical cache manager not initialized",
            }

        try:
            # Get current statistics
            before_stats = self.get_unified_stats()

            # Perform hierarchical optimization
            # This promotes frequently accessed items to higher cache levels
            optimized_entries = 0

            # Promote frequently accessed album entries from L3 to L2/L1
            for album_key, album_data in self.album_years_cache.items():
                if is_album_cache_entry(album_data):
                    # Convert tuple to list for CacheValue compatibility
                    album_data_list = list(album_data)
                    # Promote to L2 for better access time
                    await self.hierarchical_manager.set(album_key, album_data_list, ttl=3600, level=CacheLevel.L2)
                    optimized_entries += 1

            # Get post-optimization statistics
            after_stats = self.get_unified_stats()

            # Generate recommendations
            recommendations = CacheService._generate_cache_recommendations(after_stats)

        except (OSError, RuntimeError, MemoryError, KeyError):
            self.error_logger.exception("Error during cache optimization")
            return {
                "optimization_complete": False,
                "error": CACHE_OPTIMIZATION_FAILED,
                "entries_optimized": 0,
            }

        return {
            "optimization_complete": True,
            "entries_optimized": optimized_entries,
            "before_stats": before_stats,
            "after_stats": after_stats,
            "recommendations": recommendations,
        }

    @staticmethod
    def _generate_cache_recommendations(after_stats: dict[str, Any]) -> list[str]:
        """Generate cache optimization recommendations."""
        recommendations: list[str] = []

        legacy_after = after_stats.get("legacy_stats", {})

        # Check cache sizes
        total_entries = sum(legacy_after.values())
        if total_entries > LARGE_CACHE_THRESHOLD:
            recommendations.append("Consider increasing cleanup frequency for large cache size")

        # Check hierarchical performance
        if "hierarchical_stats" in after_stats:
            hierarchical_stats = after_stats["hierarchical_stats"]
            overall_hit_rate = hierarchical_stats.get("overall_hit_ratio", 0.0)

            if overall_hit_rate < MIN_HIT_RATE_THRESHOLD:
                recommendations.append("Hit rate below 70% - consider tuning cache levels")
            elif overall_hit_rate > HIGH_HIT_RATE_THRESHOLD:
                recommendations.append("Excellent hit rate - cache optimization working well")

        # Memory recommendations
        if total_entries > MEMORY_OPTIMIZATION_THRESHOLD:
            recommendations.append("Enable automatic L1/L2 eviction for memory optimization")

        if not recommendations:
            recommendations.append("Cache performance is optimal")

        return recommendations

    def get_cache_performance_metrics(self) -> dict[str, Any]:
        """Get current cache performance metrics.

        TASK-008: Provides performance metrics for optimization tracking.

        Returns:
            Dict with performance metrics and algorithm info

        """
        # Get metrics from our tracker
        performance_metrics = self.metrics_tracker.get_performance_summary()

        # Add hash algorithm information
        algorithm_info = self.hash_generator.get_algorithm_info()

        # Add cache sizes
        cache_sizes = {
            "generic_cache_size": len(self.cache),
            "album_cache_size": len(self.album_years_cache),
            "api_cache_size": len(self.api_cache),
            "total_entries": len(self.cache) + len(self.album_years_cache) + len(self.api_cache),
        }

        return {
            "performance_metrics": performance_metrics,
            "algorithm_info": algorithm_info,
            "cache_sizes": cache_sizes,
            "optimization_enabled": True,
        }
