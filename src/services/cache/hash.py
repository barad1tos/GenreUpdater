"""Unified Hash Service for Cache Operations.

This module provides a centralized hash service that unifies all hash operations
across the cache system using SHA256 as the standard algorithm.

Replaces:
- UnifiedKeyGenerator.album_key() (MD5) → hash_album_key() (SHA256)
- UnifiedKeyGenerator.hash_key() (SHA256) → hash_generic_key() (SHA256)
- OptimizedHashGenerator.generate_key() (SHA256) → hash_api_key() (SHA256)
"""

import hashlib
import json
from typing import Any


class UnifiedHashService:
    """Unified service for all cache hash operations using SHA256."""

    ALGORITHM = "sha256"

    @classmethod
    def hash_album_key(cls, artist: str, album: str) -> str:
        """Generate SHA256 hash for album cache key.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            SHA256 hash string
        """
        # Normalize inputs to handle variations
        normalized_artist = artist.strip().lower()
        normalized_album = album.strip().lower()
        key_string = f"{normalized_artist}|{normalized_album}"

        return hashlib.sha256(key_string.encode()).hexdigest()

    @classmethod
    def hash_api_key(cls, artist: str, album: str, source: str) -> str:
        """Generate SHA256 hash for API cache key.

        Args:
            artist: Artist name
            album: Album name
            source: API source identifier

        Returns:
            SHA256 hash string
        """
        # Normalize components individually before concatenation (consistent with hash_album_key)
        normalized_source = source.strip().lower()
        normalized_artist = artist.strip().lower()
        normalized_album = album.strip().lower()
        key_string = f"{normalized_source}:{normalized_artist}|{normalized_album}"

        return hashlib.sha256(key_string.encode()).hexdigest()

    @classmethod
    def hash_generic_key(cls, data: Any) -> str:
        """Generate SHA256 hash for generic cache key.

        Args:
            data: Any data that can be converted to string

        Returns:
            SHA256 hash string
        """
        # Handle different data types consistently (including empty dicts)
        key_string = str(sorted(data.items())) if isinstance(data, dict) else str(data)
        return hashlib.sha256(key_string.encode()).hexdigest()

    @classmethod
    def hash_custom_key(cls, *args: Any, **kwargs: Any) -> str:
        """Generate SHA256 hash for custom cache key with multiple components.

        Args:
            *args: Positional arguments to include in key
            **kwargs: Keyword arguments to include in key

        Returns:
            SHA256 hash string
        """
        # Use json.dumps for stable serialization of complex types
        args_string = "|".join(json.dumps(arg, sort_keys=True) for arg in args)

        kwargs_string = "|".join(f"{k}={json.dumps(v, sort_keys=True)}" for k, v in sorted(kwargs.items())) if kwargs else ""

        combined_string = f"{args_string}|{kwargs_string}".strip("|")

        return hashlib.sha256(combined_string.encode()).hexdigest()

    @classmethod
    def hash_pending_key(cls, track_id: str) -> str:
        """Generate SHA256 hash for pending verification key.

        Args:
            track_id: Track identifier for pending verification

        Returns:
            SHA256 hash string
        """
        key_string = f"pending:{track_id}"
        return hashlib.sha256(key_string.encode()).hexdigest()

    @classmethod
    def get_algorithm(cls) -> str:
        """Get the hash algorithm being used.

        Returns:
            Algorithm name (sha256)
        """
        return cls.ALGORITHM
