"""General-purpose generic cache with TTL and disk persistence."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, TypeGuard, cast

from src.services.cache.config import CacheContentType, SmartCacheConfig
from src.services.cache.hash import UnifiedHashService
from src.core.logger import ensure_directory, get_full_log_path

if TYPE_CHECKING:
    from src.core.models.protocols import CacheableKey, CacheableValue
else:  # pragma: no cover - runtime-only aliasing for type hints
    CacheableKey = Any
    CacheableValue = Any


def is_generic_cache_entry(value: object) -> TypeGuard[tuple[CacheableValue, float]]:
    """Type guard to check if value is a valid generic cache entry.

    Args:
        value: Value to check

    Returns:
        True if value is a (data, timestamp) tuple
    """
    if not isinstance(value, tuple) or len(value) != 2:
        return False

    # Check timestamp (second element) is numeric
    if not isinstance(value[1], (int, float)):
        return False

    # Check first element is a cacheable type (str, int, float, bool, dict, list, or None)
    return isinstance(value[0], (str, int, float, bool, dict, list, type(None)))


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
        self.cache_config = SmartCacheConfig()

        # Generic cache: {hash_key: (value, timestamp)}
        self.cache: dict[str, tuple[CacheableValue, float]] = {}

        # Cleanup task reference
        self._cleanup_task: asyncio.Task[None] | None = None

        # Resolve TTL and file path configuration
        self.default_ttl = self._resolve_default_ttl()
        self.cache_file = Path(get_full_log_path(config, "generic_cache_file", "cache/generic_cache.json"))

    async def initialize(self) -> None:
        """Initialize generic cache service and start cleanup task."""
        self.logger.info("Initializing GenericCacheService...")

        await self._load_from_disk()
        # Start periodic cleanup task
        self._start_cleanup_task()

        self.logger.info("GenericCacheService initialized with %ds default TTL", self.default_ttl)

    def _resolve_default_ttl(self) -> int:
        """Resolve default TTL from configuration sources."""
        fallback = self.cache_config.get_ttl(CacheContentType.GENERIC)

        candidate_values: list[Any] = [
            self.config.get("cache_ttl_seconds"),
            self.config.get("cache_ttl"),
        ]
        caching_section = self.config.get("caching")
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
        """Start periodic cleanup task for expired entries."""
        cleanup_interval = self.config.get("cleanup_interval", 300)  # 5 minutes default

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

        self.logger.debug("Generic cache hit: %s", key[:16])
        return value

    def set(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Store value in generic cache.

        Args:
            key_data: Cache key data
            value: Value to cache
            ttl: Time to live in seconds (uses default if not specified)
        """
        key = UnifiedHashService.hash_generic_key(key_data)

        # Use provided TTL or default
        actual_ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + actual_ttl

        self.cache[key] = (value, expires_at)

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

    def get_all_entries(self) -> list[tuple[str, Any, float]]:
        """Get all cache entries for debugging/inspection.

        Returns:
            List of (key, value, timestamp) tuples
        """
        return [(key[:16], value, timestamp) for key, (value, timestamp) in self.cache.items()]

    def enforce_size_limits(self) -> int:
        """Enforce cache size limits by removing oldest entries.

        Returns:
            Number of entries removed
        """
        max_entries = self.config.get("max_generic_entries", 10000)

        if len(self.cache) <= max_entries:
            return 0

        # Sort by timestamp (oldest first) and remove excess
        sorted_items = sorted(self.cache.items(), key=lambda x: x[1][1])
        entries_to_remove = len(self.cache) - max_entries

        removed_count = 0
        for key, _ in sorted_items[:entries_to_remove]:
            del self.cache[key]
            removed_count += 1

        if removed_count > 0:
            self.logger.info("Enforced size limit: removed %d oldest entries", removed_count)

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

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> None:
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
            "max_entries": self.config.get("max_generic_entries", 10000),
            "cleanup_running": self._cleanup_task is not None and not self._cleanup_task.done(),
        }

    async def save_to_disk(self) -> None:
        """Persist cache contents to disk."""
        if not self.cache:
            if self.cache_file.exists():
                try:
                    self.cache_file.unlink()
                    self.logger.info("Deleted empty generic cache file: %s", self.cache_file)
                except OSError as e:
                    self.logger.warning("Failed to remove generic cache file %s: %s", self.cache_file, e)
            return

        def blocking_save() -> None:
            """Write current cache entries to disk within a worker thread."""
            ensure_directory(str(self.cache_file.parent))
            payload = {
                key: {"value": self._prepare_value_for_disk(value), "expires_at": expires_at}
                for key, (value, expires_at) in self.cache.items()
            }
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=str(self.cache_file.parent), delete=False
            ) as tmp_file:
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
                temp_path = Path(tmp_file.name)
            temp_path.replace(self.cache_file)

        await asyncio.to_thread(blocking_save)
        self.logger.info("Generic cache saved to %s (%d entries)", self.cache_file, len(self.cache))

    async def _load_from_disk(self) -> None:
        """Load cache contents from disk if available."""
        if not self.cache_file.exists():
            self.logger.debug("Generic cache file %s not found; starting fresh", self.cache_file)
            return

        def blocking_load() -> dict[str, tuple[CacheableValue, float]]:
            """Load cache entries from disk within a worker thread."""
            try:
                with self.cache_file.open(encoding="utf-8") as file_handle:
                    data = json.load(file_handle)
            except (json.JSONDecodeError, OSError) as e:
                self.logger.warning("Failed to load generic cache file %s: %s", self.cache_file, e)
                return {}

            now = time.time()
            restored: dict[str, tuple[CacheableValue, float]] = {}
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
            self.logger.info("Loaded %d generic cache entries from %s", len(restored_cache), self.cache_file)

    @staticmethod
    def _prepare_value_for_disk(value: CacheableValue) -> CacheableValue:
        """Prepare cache value for JSON serialization."""
        if isinstance(value, list):
            prepared_list: list[Any] = []
            for item in value:
                if hasattr(item, "model_dump"):
                    prepared_list.append(cast(Any, item).model_dump())
                else:
                    prepared_list.append(item)
            return cast(CacheableValue, prepared_list)

        if hasattr(value, "model_dump"):
            return cast(CacheableValue, cast(Any, value).model_dump())

        return cast(CacheableValue, value)

    @staticmethod
    def _restore_value_from_disk(value: CacheableValue) -> CacheableValue:
        """Restore cache value from serialized representation."""
        # At this layer we keep values as plain data structures; higher layers
        # validate and rebuild domain objects as needed.
        return cast(CacheableValue, value)
