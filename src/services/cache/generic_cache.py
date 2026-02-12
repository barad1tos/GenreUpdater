"""General-purpose generic cache with TTL and disk persistence."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel

from services.cache.cache_config import CacheContentType, SmartCacheConfig
from services.cache.hash_service import UnifiedHashService
from core.logger import LogFormat, ensure_directory, get_full_log_path

if TYPE_CHECKING:
    from core.models.protocols import CacheableKey, CacheableValue
else:  # pragma: no cover - runtime-only aliasing for type hints
    CacheableKey = Any
    CacheableValue = Any


class GenericCacheService:
    """Generic in-memory cache service with TTL support and automatic cleanup."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger | None = None) -> None:
        """Initialize generic cache service.

        Args:
            config: Cache configuration dictionary
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.cache_config = SmartCacheConfig(config)

        # LRU cache: OrderedDict maintains insertion/access order for LRU eviction.
        # Each entry maps hash_key to (value, expires_at_timestamp) tuple.
        self.cache: OrderedDict[str, tuple[CacheableValue, float]] = OrderedDict()

        # Max cache size for LRU eviction (evicts on set() when exceeded)
        self.max_size: int = config.get("max_generic_entries", 10000)

        # Cleanup task reference
        self._cleanup_task: asyncio.Task[None] | None = None

        # Resolve TTL and file path configuration
        self.default_ttl = self._resolve_default_ttl()
        self.cache_file = Path(get_full_log_path(config, "generic_cache_file", "cache/generic_cache.json"))

    async def initialize(self) -> None:
        """Initialize generic cache service and start cleanup task."""
        self.logger.info("Initializing %s...", LogFormat.entity("GenericCacheService"))

        await self._load_from_disk()
        # Start periodic cleanup task
        self._start_cleanup_task()

        self.logger.info("%s initialized with %ds default TTL", LogFormat.entity("GenericCacheService"), self.default_ttl)

    def _resolve_default_ttl(self) -> int:
        """Resolve default TTL from configuration sources.

        Checks multiple config locations in order:
        1. config.cache_ttl_seconds
        2. config.cache_ttl
        3. config.caching.default_ttl_seconds
        4. Falls back to SmartCacheConfig default for GENERIC type

        Returns:
            TTL in seconds as positive integer.

        """
        fallback = self.cache_config.get_ttl(CacheContentType.GENERIC)

        caching_section = self.config.get("caching")
        candidate_values: list[Any] = [
            self.config.get("cache_ttl_seconds"),
        ]
        if isinstance(caching_section, dict):
            candidate_values.append(caching_section.get("default_ttl_seconds"))

        for value in candidate_values:
            if value in (None, ""):
                continue
            try:
                ttl = int(value)
                if ttl > 0:
                    return ttl
                self.logger.warning("Ignoring non-positive TTL override (%s); using fallback %ds", value, fallback)
            except (TypeError, ValueError):
                self.logger.warning("Invalid TTL override '%s'; using fallback %ds", value, fallback)

        return fallback

    def _start_cleanup_task(self) -> None:
        """Start periodic cleanup task for expired cache entries.

        Creates an asyncio task that runs cleanup_expired() and enforce_size_limits()
        at regular intervals (default: 5 minutes). Only one cleanup task runs at a time.

        The task handles cancellation gracefully and logs any errors during cleanup.

        """
        caching_section = self.config.get("caching", {})
        cleanup_interval: int = caching_section.get("cleanup_interval_seconds", 300) if isinstance(caching_section, dict) else 300

        if self._cleanup_task and not self._cleanup_task.done():
            self.logger.debug("Cleanup task already running; skipping restart")
            return

        async def cleanup_loop() -> None:
            """Periodic cleanup loop."""
            while True:
                try:
                    await asyncio.sleep(cleanup_interval)
                    self._run_cleanup_iteration()
                except asyncio.CancelledError:
                    self.logger.debug("Cleanup task cancelled")
                    raise
                except Exception as e:
                    self.logger.exception("Error in cleanup task: %s", e)

        self._cleanup_task = asyncio.create_task(cleanup_loop())
        self.logger.debug("Started cleanup task with %ds interval", cleanup_interval)

    def _run_cleanup_iteration(self) -> None:
        """Perform a single cleanup cycle and log results."""
        cleaned = self.cleanup_expired()
        if cleaned > 0:
            self.logger.debug("Periodic cleanup removed %d expired entries", cleaned)

        removed = self.enforce_size_limits()
        if removed > 0:
            self.logger.debug("Periodic size enforcement removed %d oldest entries", removed)

    def get(self, key_data: CacheableKey) -> CacheableValue | None:
        """Get value from generic cache.

        Accessing an entry updates its LRU position (moves to end).

        Args:
            key_data: Cache key data

        Returns:
            Cached value if found and valid, None otherwise
        """
        key = UnifiedHashService.hash_generic_key(key_data)

        if key not in self.cache:
            self.logger.debug("Generic cache miss: %s", key[:16])
            return None

        value, timestamp = self.cache[key]

        # Check if expired
        if GenericCacheService._is_expired(timestamp):
            self.logger.debug("Generic cache expired: %s", key[:16])
            del self.cache[key]
            return None

        # Move to end to mark as recently used (LRU update)
        self.cache.move_to_end(key)

        self.logger.debug("Generic cache hit: %s", key[:16])
        return value

    def set(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Store value in generic cache with LRU eviction.

        If the cache is at capacity and the key is new, the least recently used
        entry is evicted before adding the new entry.

        Args:
            key_data: Cache key data
            value: Value to cache
            ttl: Time to live in seconds (uses default if not specified)
        """
        key = UnifiedHashService.hash_generic_key(key_data)

        # Evict LRU entry if at capacity and this is a new key
        if len(self.cache) >= self.max_size and key not in self.cache:
            # Remove oldest entry (first item in OrderedDict = LRU)
            evicted_key, _ = self.cache.popitem(last=False)
            self.logger.debug("LRU eviction: removed %s to make room", evicted_key[:16])

        # Use provided TTL or default
        actual_ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + actual_ttl

        self.cache[key] = (value, expires_at)

        # Move to end to mark as recently used (handles both new and updated keys)
        self.cache.move_to_end(key)

        self.logger.debug("Stored in generic cache: %s (TTL: %ds)", key[:16], actual_ttl)

    def invalidate(self, key_data: CacheableKey) -> bool:
        """Invalidate specific cache entry.

        Args:
            key_data: Cache key data to invalidate

        Returns:
            True if entry was found and removed, False otherwise
        """
        key = UnifiedHashService.hash_generic_key(key_data)

        if key in self.cache:
            del self.cache[key]
            self.logger.debug("Invalidated generic cache entry: %s", key[:16])
            return True

        return False

    def invalidate_all(self) -> None:
        """Clear all generic cache entries."""
        count = len(self.cache)
        self.cache.clear()
        self.logger.info("Cleared all generic cache entries (%d items)", count)

    def cleanup_expired(self) -> int:
        """Remove expired entries from cache.

        Returns:
            Number of entries removed
        """
        current_time = time.time()
        expired_keys = [key for key, (_, timestamp) in self.cache.items() if timestamp <= current_time]

        # Remove expired entries
        for key in expired_keys:
            del self.cache[key]

        if expired_keys:
            self.logger.debug("Cleaned up %d expired generic cache entries", len(expired_keys))

        return len(expired_keys)

    @staticmethod
    def _is_expired(timestamp: float) -> bool:
        """Check if timestamp indicates expiration.

        Args:
            timestamp: Expiration timestamp

        Returns:
            True if expired, False otherwise
        """
        return timestamp <= time.time()

    def enforce_size_limits(self) -> int:
        """Enforce cache size limits by removing LRU (least recently used) entries.

        Uses OrderedDict iteration order where first = oldest (LRU).

        Returns:
            Number of entries removed
        """
        if len(self.cache) <= self.max_size:
            return 0

        entries_to_remove = len(self.cache) - self.max_size
        removed_count = 0

        for _ in range(entries_to_remove):
            self.cache.popitem(last=False)  # Remove LRU (oldest in OrderedDict)
            removed_count += 1

        if removed_count > 0:
            self.logger.info("Enforced size limit: removed %d LRU entries", removed_count)

        return removed_count

    async def stop_cleanup_task(self) -> None:
        """Stop the periodic cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self.logger.debug("Stopped cleanup task")

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, _exc_type: type[BaseException] | None, _exc: BaseException | None, _tb: object) -> None:
        """Async context manager exit - ensures cleanup task is stopped."""
        await self.stop_cleanup_task()

    def get_stats(self) -> dict[str, Any]:
        """Get generic cache statistics.

        Returns:
            Dictionary containing cache statistics
        """
        current_time = time.time()
        valid_entries = 0
        expired_entries = 0

        for _, timestamp in self.cache.values():
            if timestamp > current_time:
                valid_entries += 1
            else:
                expired_entries += 1

        policy = self.cache_config.get_policy(CacheContentType.GENERIC)

        return {
            "total_entries": len(self.cache),
            "valid_entries": valid_entries,
            "expired_entries": expired_entries,
            "content_type": CacheContentType.GENERIC.value,
            "default_ttl": self.default_ttl,
            "ttl_policy": policy.ttl_seconds,
            "invalidation_strategy": policy.invalidation_strategy.value,
            "max_entries": self.max_size,
            "cleanup_running": self._cleanup_task is not None and not self._cleanup_task.done(),
        }

    async def save_to_disk(self) -> None:
        """Persist cache contents to disk."""
        if not self.cache:
            if self.cache_file.exists():
                try:
                    self.cache_file.unlink()
                    self.logger.info("Deleted empty generic cache file: [cyan]%s[/cyan]", self.cache_file.name)
                except OSError as e:
                    self.logger.warning("Failed to remove generic cache file %s: %s", self.cache_file, e)
            return

        def blocking_save() -> None:
            """Write current cache entries to disk within a worker thread."""
            ensure_directory(str(self.cache_file.parent))
            payload = {
                key: {"value": self._prepare_value_for_disk(value), "expires_at": expires_at} for key, (value, expires_at) in self.cache.items()
            }
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(self.cache_file.parent), delete=False) as tmp_file:
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
                temp_path = Path(tmp_file.name)
            temp_path.replace(self.cache_file)

        try:
            await asyncio.to_thread(blocking_save)
            self.logger.info("Generic cache saved to [cyan]%s[/cyan] (%d entries)", self.cache_file.name, len(self.cache))
        except OSError as e:
            self.logger.exception("Failed to save generic cache to %s: %s", self.cache_file, e)

    async def _load_from_disk(self) -> None:
        """Load cache contents from disk if available."""
        if not self.cache_file.exists():
            self.logger.debug("Generic cache file %s not found; starting fresh", self.cache_file)
            return

        def blocking_load() -> OrderedDict[str, tuple[CacheableValue, float]]:
            """Load cache entries from disk within a worker thread."""
            try:
                with self.cache_file.open(encoding="utf-8") as file_handle:
                    data = json.load(file_handle)
            except (json.JSONDecodeError, OSError) as e:
                self.logger.warning("Failed to load generic cache file %s: %s", self.cache_file, e)
                return OrderedDict()

            now = time.time()
            restored: OrderedDict[str, tuple[CacheableValue, float]] = OrderedDict()
            for key, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                value = entry.get("value")
                expires_at = entry.get("expires_at")
                if not isinstance(expires_at, (int, float)):
                    continue
                expires_at_float = float(expires_at)
                if expires_at_float <= now:
                    continue
                restored[key] = (self._restore_value_from_disk(value), expires_at_float)
            return restored

        restored_cache = await asyncio.to_thread(blocking_load)
        if restored_cache:
            self.cache.update(restored_cache)
            self.cleanup_expired()
            self.enforce_size_limits()
            self.logger.info("Loaded %d generic cache entries from [cyan]%s[/cyan]", len(restored_cache), self.cache_file.name)

    @staticmethod
    def _prepare_value_for_disk(value: CacheableValue) -> CacheableValue:
        """Prepare cache value for JSON serialization."""
        if isinstance(value, list):
            prepared_list: list[Any] = []
            for item in value:
                if isinstance(item, BaseModel):
                    prepared_list.append(item.model_dump())
                else:
                    prepared_list.append(item)
            return prepared_list

        return value.model_dump() if isinstance(value, BaseModel) else value

    @staticmethod
    def _restore_value_from_disk(value: CacheableValue) -> CacheableValue:
        """Restore cache value from serialized representation."""
        # At this layer we keep values as plain data structures; higher layers
        # validate and rebuild domain objects as needed.
        return value
