"""Unified cache protocol interface for TASK-004: Cache Duplication Consolidation Phase 1.

This module provides a unified interface for all cache implementations in the system,
eliminating the duplication found across 5 independent cache systems.

Key consolidation improvements:
- Single hash implementation for all services
- Standardized key generation patterns
- Unified cache interface protocol
- Consistent TTL and memory management
"""

import hashlib
import logging
import time
from abc import abstractmethod
from typing import Any, Protocol, TypeVar

# Import for clean_names function to avoid circular dependency
try:
    from src.utils.data.metadata import clean_names
except ImportError:
    # Fallback if clean_names not available
    def clean_names(
        artist: str,
        track_name: str,
        album_name: str,
        config: dict[str, Any],
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> tuple[str, str]:
        """Fallback clean_names function that returns original values without cleaning.

        This function maintains the exact same signature as the real clean_names function
        but simply returns the original values without any processing.

        Args:
            artist: Artist name (ignored in fallback)
            track_name: Original track name
            album_name: Original album name
            config: Configuration dict (ignored in fallback)
            console_logger: Console logger (ignored in fallback)
            error_logger: Error logger (ignored in fallback)

        Returns:
            Tuple of (track_name, album_name) unchanged

        """
        # Suppress unused parameter warnings by referencing them
        _ = artist, config, console_logger, error_logger
        return track_name, album_name

T = TypeVar("T")


class CacheProtocol[T](Protocol):
    """Unified cache interface for all cache implementations.

    This protocol standardizes the interface across all cache types:
    - Generic in-memory cache
    - Album years cache
    - API results cache
    - Pending verification cache
    """

    @abstractmethod
    async def get(self, key: str, default: T | None = None) -> T | None:
        """Get cached value with optional default.

        Args:
            key: Cache key to retrieve
            default: Default value if key not found

        Returns:
            Cached value or default if not found/expired

        """

    @abstractmethod
    async def set(self, key: str, value: T, ttl: int | None = None) -> None:
        """Set cached value with optional TTL.

        Args:
            key: Cache key to store
            value: Value to cache
            ttl: Time-to-live in seconds (None for default TTL)

        """

    @abstractmethod
    async def invalidate(self, key: str | list[str]) -> int:
        """Invalidate single key or list of keys.

        Args:
            key: Single key or list of keys to invalidate

        Returns:
            Count of keys successfully invalidated

        """

    @abstractmethod
    async def cleanup(self) -> int:
        """Clean expired entries and enforce size limits.

        Returns:
            Count of entries cleaned/evicted

        """

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics and metrics.

        Returns:
            Dictionary with cache statistics (size, hit rate, etc.)

        """


class UnifiedKeyGenerator:
    """Centralized key generation for all cache types.

    This class eliminates the duplication found across:
    - CacheService._hash_key() and generate_album_key()
    - PendingVerificationService._generate_album_key()
    - BaseApiClient key patterns
    - API orchestrator cache keys

    Provides consistent hashing and key prefixes for different cache domains.
    """

    @staticmethod
    def hash_key(data: str | tuple[Any, ...] | dict[str, Any] | float | bool) -> str:
        """Single hash implementation for all services.

        Replaces 4 different SHA256 implementations across the codebase.

        Args:
            data: Data to hash (string, tuple, dict, etc.)

        Returns:
            SHA256 hex digest string

        """
        try:
            key_str = data if isinstance(data, str) else str(data)
        except (TypeError, ValueError, AttributeError):
            # Fallback for unhashable types
            key_str = f"unhashable_{type(data).__name__}"

        return hashlib.sha256(key_str.encode("utf-8")).hexdigest()

    @staticmethod
    def album_key(artist: str, album: str, use_cleaning: bool = False) -> str:
        """Standardized album key generation.

        Unifies album key generation from CacheService and PendingVerificationService.
        Both used nearly identical logic with slight variations.

        Args:
            artist: Artist name
            album: Album name
            use_cleaning: Whether to apply name cleaning (for pending verification)

        Returns:
            Standardized album cache key with 'album:' prefix

        """
        if use_cleaning:
            try:
                # Create minimal null logger for clean_names function
                mock_logger = logging.getLogger(f"{__name__}.mock")
                mock_logger.setLevel(logging.CRITICAL + 1)  # Disable all logging
                # This matches PendingVerificationService behavior
                _, cleaned_album = clean_names(artist, "", album, {}, mock_logger, mock_logger)
                normalized = f"{artist.strip().lower()}|{cleaned_album.strip().lower()}"
            except (TypeError, AttributeError, ImportError):
                # Fallback if clean_names fails
                normalized = f"{artist.strip().lower()}|{album.strip().lower()}"
        else:
            # Fallback for no cleaning or clean_names not available
            normalized = f"{artist.strip().lower()}|{album.strip().lower()}"

        return f"album:{UnifiedKeyGenerator.hash_key(normalized)}"

    @staticmethod
    def api_key(api_name: str, artist: str, album: str) -> str:
        """Standardized API cache key generation.

        Args:
            api_name: Name of API service (musicbrainz, discogs, lastfm)
            artist: Artist name
            album: Album name

        Returns:
            API cache key with hierarchical structure: 'api:{service}:{album_hash}'

        """
        base_key = UnifiedKeyGenerator.album_key(artist, album)
        return f"api:{api_name}:{base_key}"

    @staticmethod
    def pending_key(artist: str, album: str) -> str:
        """Standardized pending verification key generation.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Pending verification cache key with 'pending:' prefix

        """
        base_key = UnifiedKeyGenerator.album_key(artist, album, use_cleaning=True)
        return f"pending:{base_key}"

    @staticmethod
    def generic_key(namespace: str, *components: str | float | bool) -> str:
        """Generate key for other cache types.

        Args:
            namespace: Key namespace/prefix
            *components: Key components to combine

        Returns:
            Generic cache key with namespace prefix

        """
        if components:
            combined = "|".join(str(comp) for comp in components)
            key_hash = UnifiedKeyGenerator.hash_key(combined)
            return f"{namespace}:{key_hash}"
        return f"{namespace}:empty"


class CacheStats:
    """Unified cache statistics tracking.

    Provides consistent metrics across all cache implementations.
    """

    def __init__(self) -> None:
        """Initialize cache statistics."""
        self.hits = 0
        self.misses = 0
        self.sets = 0
        self.invalidations = 0
        self.cleanups = 0
        self.evictions = 0
        self.errors = 0

    def record_hit(self) -> None:
        """Record a cache hit."""
        self.hits += 1

    def record_miss(self) -> None:
        """Record a cache miss."""
        self.misses += 1

    def record_set(self) -> None:
        """Record a cache set operation."""
        self.sets += 1

    def record_invalidation(self, count: int = 1) -> None:
        """Record cache invalidation operations."""
        self.invalidations += count

    def record_cleanup(self, count: int = 1) -> None:
        """Record cache cleanup operations."""
        self.cleanups += count

    def record_eviction(self, count: int = 1) -> None:
        """Record cache evictions."""
        self.evictions += count

    def record_error(self) -> None:
        """Record a cache error."""
        self.errors += 1

    def get_hit_ratio(self) -> float:
        """Calculate cache hit ratio.

        Returns:
            Hit ratio as float between 0.0 and 1.0

        """
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary format.

        Returns:
            Dictionary with all cache statistics

        """
        return {
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "invalidations": self.invalidations,
            "cleanups": self.cleanups,
            "evictions": self.evictions,
            "errors": self.errors,
            "hit_ratio": self.get_hit_ratio(),
            "total_requests": self.hits + self.misses,
        }


class TTLManager:
    """Unified TTL (Time-To-Live) management.

    Standardizes expiration logic across different cache implementations.
    """

    @staticmethod
    def calculate_expiry(ttl_seconds: int | None, default_ttl: int) -> float:
        """Calculate expiry timestamp.

        Args:
            ttl_seconds: TTL in seconds (None for default)
            default_ttl: Default TTL in seconds

        Returns:
            Expiry timestamp (seconds since epoch)

        """
        effective_ttl = ttl_seconds if ttl_seconds is not None else default_ttl
        return time.time() + effective_ttl

    @staticmethod
    def is_expired(expiry_timestamp: float) -> bool:
        """Check if entry has expired.

        Args:
            expiry_timestamp: Expiry timestamp to check

        Returns:
            True if expired, False otherwise

        """
        return time.time() > expiry_timestamp

    @staticmethod
    def ttl_from_days(days: int) -> int:
        """Convert days to seconds for TTL.

        Args:
            days: Number of days

        Returns:
            TTL in seconds

        """
        return days * 86400  # 24 * 60 * 60

    @staticmethod
    def ttl_remaining(expiry_timestamp: float) -> float:
        """Calculate the remaining TTL in seconds.

        Args:
            expiry_timestamp: Expiry timestamp

        Returns:
            Remaining seconds (negative if expired)

        """
        return expiry_timestamp - time.time()


# Export public interfaces
__all__ = [
    "CacheProtocol",
    "CacheStats",
    "TTLManager",
    "UnifiedKeyGenerator",
]
