"""Cache compression implementation for Music Genre Updater.

This module implements transparent cache compression with configurable algorithms
and threshold-based compression decisions for optimal performance.
"""

import gzip
import logging
import time
import zlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Never, TypeVar

from src.services.cache.cache_protocol import CacheProtocol

# Type variable for compression operation return types
CompressionResult = TypeVar("CompressionResult")


class CompressionAlgorithm(Enum):
    """Supported compression algorithms."""

    GZIP = "gzip"
    ZLIB = "zlib"


@dataclass
class CompressionConfig:
    """Configuration for cache compression behavior."""

    algorithm: CompressionAlgorithm = CompressionAlgorithm.ZLIB
    compression_threshold_bytes: int = 1024  # Compress values larger than this
    compression_level: int = 6  # Compression level (1-9, higher = better compression)
    enable_metrics: bool = True  # Track compression metrics


@dataclass
class CompressionMetrics:
    """Metrics tracking for compression operations."""

    total_compressions: int = 0
    total_decompressions: int = 0
    bytes_before_compression: int = 0
    bytes_after_compression: int = 0
    compression_time_ms: float = 0.0
    decompression_time_ms: float = 0.0

    def record_compression(self, original_size: int, compressed_size: int, duration_ms: float) -> None:
        """Record compression operation metrics.

        Args:
            original_size: Original data size in bytes
            compressed_size: Compressed data size in bytes
            duration_ms: Compression operation duration in milliseconds

        """
        self.total_compressions += 1
        self.bytes_before_compression += original_size
        self.bytes_after_compression += compressed_size
        self.compression_time_ms += duration_ms

    def record_decompression(self, duration_ms: float) -> None:
        """Record decompression operation metrics.

        Args:
            duration_ms: Decompression operation duration in milliseconds

        """
        self.total_decompressions += 1
        self.decompression_time_ms += duration_ms

    def get_compression_ratio(self) -> float:
        """Calculate overall compression ratio.

        Returns:
            Compression ratio (original_size / compressed_size)

        """
        if self.bytes_after_compression == 0:
            return 1.0
        return self.bytes_before_compression / self.bytes_after_compression

    def get_space_savings_percent(self) -> float:
        """Calculate space savings percentage.

        Returns:
            Space savings as percentage (0-100)

        """
        if self.bytes_before_compression == 0:
            return 0.0
        savings = self.bytes_before_compression - self.bytes_after_compression
        return (savings / self.bytes_before_compression) * 100

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary format.

        Returns:
            Dictionary containing all compression metrics

        """
        return {
            "total_compressions": self.total_compressions,
            "total_decompressions": self.total_decompressions,
            "bytes_before_compression": self.bytes_before_compression,
            "bytes_after_compression": self.bytes_after_compression,
            "compression_time_ms": self.compression_time_ms,
            "decompression_time_ms": self.decompression_time_ms,
            "compression_ratio": self.get_compression_ratio(),
            "space_savings_percent": self.get_space_savings_percent(),
        }


class CacheCompressor:
    """Handles cache value compression and decompression operations.

    Provides transparent compression/decompression with configurable algorithms
    and threshold-based compression decisions.
    """

    # Compression markers to identify compressed data
    GZIP_MARKER = b"__GZIP_COMPRESSED__"
    ZLIB_MARKER = b"__ZLIB_COMPRESSED__"

    def __init__(
        self,
        config: CompressionConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize cache compressor.

        Args:
            config: Compression configuration
            logger: Logger for compression events

        """
        self.config = config or CompressionConfig()
        self.logger = logger or logging.getLogger(__name__)
        self.metrics = CompressionMetrics()

    def _raise_unsupported_algorithm_error(self) -> Never:
        """Raise error for unsupported compression algorithm."""
        msg = f"Unsupported compression algorithm: {self.config.algorithm}"
        raise ValueError(msg)

    def should_compress(self, data: bytes) -> bool:
        """Determine if data should be compressed based on threshold.

        Args:
            data: Data to evaluate for compression

        Returns:
            True if data should be compressed, False otherwise

        """
        return len(data) >= self.config.compression_threshold_bytes

    def compress_data(self, data: bytes) -> bytes:
        """Compress data using configured algorithm.

        Args:
            data: Data to compress

        Returns:
            Compressed data with algorithm marker

        Raises:
            ValueError: If compression fails

        """
        if not self.should_compress(data):
            return data

        start_time = time.perf_counter()
        original_size = len(data)

        try:
            # Validate algorithm before processing
            if self.config.algorithm not in {CompressionAlgorithm.GZIP, CompressionAlgorithm.ZLIB}:
                self._raise_unsupported_algorithm_error()

            # Perform compression based on algorithm
            if self.config.algorithm == CompressionAlgorithm.GZIP:
                compressed = gzip.compress(data, compresslevel=self.config.compression_level)
                result = self.GZIP_MARKER + compressed
            else:  # CompressionAlgorithm.ZLIB
                compressed = zlib.compress(data, level=self.config.compression_level)
                result = self.ZLIB_MARKER + compressed

            # Only use compressed version if it's actually smaller
            if len(result) >= original_size:
                self.logger.debug("Compression increased size, using original data")
                return data

            duration_ms = (time.perf_counter() - start_time) * 1000

            if self.config.enable_metrics:
                self.metrics.record_compression(original_size, len(result), duration_ms)

            self.logger.debug(
                "Compressed %d bytes to %d bytes (%.1f%% reduction) in %.2fms",
                original_size,
                len(result),
                ((original_size - len(result)) / original_size) * 100,
                duration_ms,
            )

        except (OSError, ValueError):
            self.logger.exception("Compression failed")
            return data  # Return original data on compression failure

        return result

    def decompress_data(self, data: bytes) -> bytes:
        """Decompress data if it was compressed.

        Args:
            data: Data to potentially decompress

        Returns:
            Decompressed data or original data if not compressed

        Raises:
            ValueError: If decompression fails for marked compressed data

        """
        start_time = time.perf_counter()

        try:
            if data.startswith(self.GZIP_MARKER):
                compressed_data = data[len(self.GZIP_MARKER) :]
                result = gzip.decompress(compressed_data)

            elif data.startswith(self.ZLIB_MARKER):
                compressed_data = data[len(self.ZLIB_MARKER) :]
                result = zlib.decompress(compressed_data)

            else:
                # Data is not compressed
                return data

            duration_ms = (time.perf_counter() - start_time) * 1000

            if self.config.enable_metrics:
                self.metrics.record_decompression(duration_ms)

            self.logger.debug(
                "Decompressed %d bytes to %d bytes in %.2fms",
                len(data),
                len(result),
                duration_ms,
            )

        except Exception as exc:
            self.logger.exception("Decompression failed")
            msg = f"Failed to decompress data: {exc}"
            raise ValueError(msg) from exc

        return result

    def get_metrics(self) -> dict[str, Any]:
        """Get compression metrics.

        Returns:
            Dictionary containing compression statistics

        """
        return self.metrics.to_dict()

    def reset_metrics(self) -> None:
        """Reset compression metrics."""
        self.metrics = CompressionMetrics()
        self.logger.info("Compression metrics reset")


class CompressingCacheWrapper:
    """Cache wrapper that adds transparent compression to any CacheProtocol implementation.

    This wrapper automatically compresses large values when storing and decompresses
    them when retrieving, providing a transparent compression layer.
    """

    def __init__(
        self,
        cache_backend: CacheProtocol[Any],
        compressor: CacheCompressor | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize compressing cache wrapper.

        Args:
            cache_backend: Underlying cache implementation
            compressor: Cache compressor instance
            logger: Logger for cache events

        """
        self.cache_backend = cache_backend
        self.compressor = compressor or CacheCompressor()
        self.logger = logger or logging.getLogger(__name__)

    @staticmethod
    def _serialize_value(value: str | dict[str, Any] | list[Any] | float | bool | bytes) -> bytes:
        """Serialize value to bytes for compression.

        Args:
            value: Value to serialize

        Returns:
            Serialized bytes

        """
        if isinstance(value, bytes):
            return value
        return value.encode() if isinstance(value, str) else str(value).encode()

    @staticmethod
    def _deserialize_value(data: bytes, original_type: type) -> str | dict[str, Any] | list[Any] | float | bool | bytes:
        """Deserialize bytes back to original type.

        Args:
            data: Serialized data
            original_type: Original value type

        Returns:
            Deserialized value

        """
        return data if original_type is bytes else data.decode()

    async def get(
        self, key: str, default: str | dict[str, Any] | list[Any] | float | bool | bytes | None = None
    ) -> str | dict[str, Any] | list[Any] | int | float | bool | bytes | None:
        """Get cached value with automatic decompression.

        Args:
            key: Cache key to retrieve
            default: Default value if key not found

        Returns:
            Cached value or default if not found

        """
        try:
            # Get raw compressed value from backend
            raw_value = await self.cache_backend.get(key)

            if raw_value is None:
                return default

            # Convert to bytes if needed
            if isinstance(raw_value, str):
                raw_bytes = raw_value.encode()
            elif isinstance(raw_value, bytes):
                raw_bytes = raw_value
            else:
                # For other types, serialize first
                raw_bytes = str(raw_value).encode()

            # Decompress if needed
            decompressed = self.compressor.decompress_data(raw_bytes)

            # Deserialize to appropriate type
            return decompressed if isinstance(default, bytes) else decompressed.decode()

        except UnicodeDecodeError:
            self.logger.exception("Failed to get and decompress value for key %s", key)
            return default

    async def set(self, key: str, value: str | dict[str, Any] | list[Any] | float | bool, ttl: int | None = None) -> None:
        """Set cached value with automatic compression.

        Args:
            key: Cache key to store
            value: Value to cache
            ttl: Time-to-live in seconds

        """
        try:
            # Serialize value to bytes
            serialized = self._serialize_value(value)

            # Compress if beneficial
            compressed = self.compressor.compress_data(serialized)

            # Store in backend (convert back to backend's expected type)
            if isinstance(value, str):
                # Store as string if original was string
                await self.cache_backend.set(key, compressed.decode(errors="ignore"), ttl)
            else:
                # Store as bytes for other types
                await self.cache_backend.set(key, compressed, ttl)

        except (ValueError, OSError):
            self.logger.exception("Failed to compress and set value for key %s", key)
            raise

    async def invalidate(self, key: str | list[str]) -> int:
        """Invalidate cache keys.

        Args:
            key: Single key or list of keys to invalidate

        Returns:
            Count of keys successfully invalidated

        """
        return await self.cache_backend.invalidate(key)

    async def cleanup(self) -> int:
        """Clean cache.

        Returns:
            Count of entries cleaned

        """
        return await self.cache_backend.cleanup()

    def get_stats(self) -> dict[str, Any]:
        """Get combined cache and compression statistics.

        Returns:
            Combined statistics from cache backend and compressor

        """
        cache_stats = {}
        try:
            cache_stats = self.cache_backend.get_stats()
        except (AttributeError, RuntimeError, OSError) as exc:
            self.logger.warning("Failed to get cache backend stats: %s", exc)

        compression_stats = {"compression": self.compressor.get_metrics()}

        return {**cache_stats, **compression_stats}


class CompressionCacheFactory:
    """Factory for creating compression-enabled cache instances."""

    @classmethod
    def create_gzip_cache(
        cls,
        cache_backend: CacheProtocol[Any],
        threshold_bytes: int = 1024,
        compression_level: int = 6,
        logger: logging.Logger | None = None,
    ) -> CompressingCacheWrapper:
        """Create cache with GZIP compression.

        Args:
            cache_backend: Cache implementation to wrap
            threshold_bytes: Minimum size for compression
            compression_level: GZIP compression level (1-9)
            logger: Logger for compression events

        Returns:
            CompressingCacheWrapper with GZIP compression

        """
        config = CompressionConfig(
            algorithm=CompressionAlgorithm.GZIP,
            compression_threshold_bytes=threshold_bytes,
            compression_level=compression_level,
        )

        compressor = CacheCompressor(config=config, logger=logger)
        return CompressingCacheWrapper(
            cache_backend=cache_backend,
            compressor=compressor,
            logger=logger,
        )

    @classmethod
    def create_zlib_cache(
        cls,
        cache_backend: CacheProtocol[Any],
        threshold_bytes: int = 1024,
        compression_level: int = 6,
        logger: logging.Logger | None = None,
    ) -> CompressingCacheWrapper:
        """Create cache with ZLIB compression.

        Args:
            cache_backend: Cache implementation to wrap
            threshold_bytes: Minimum size for compression
            compression_level: ZLIB compression level (1-9)
            logger: Logger for compression events

        Returns:
            CompressingCacheWrapper with ZLIB compression

        """
        config = CompressionConfig(
            compression_threshold_bytes=threshold_bytes,
            compression_level=compression_level,
        )

        compressor = CacheCompressor(config=config, logger=logger)
        return CompressingCacheWrapper(
            cache_backend=cache_backend,
            compressor=compressor,
            logger=logger,
        )


# Export public interfaces
__all__ = [
    "CacheCompressor",
    "CompressingCacheWrapper",
    "CompressionAlgorithm",
    "CompressionCacheFactory",
    "CompressionConfig",
    "CompressionMetrics",
]
