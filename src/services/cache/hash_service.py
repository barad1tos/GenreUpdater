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

        Note:
            Non-JSON-serializable dict values are converted to their string representation.
        """
        # Handle different data types consistently (including nested dicts)
        if isinstance(data, dict):
            try:
                key_string = json.dumps(data, sort_keys=True)
            except TypeError:
                # Fallback for non-serializable values (e.g., datetime, Path, custom objects)
                key_string = str(sorted(data.items()))
        else:
            key_string = str(data)
        return hashlib.sha256(key_string.encode()).hexdigest()

    @classmethod
    def hash_custom_key(cls, *args: Any, **kwargs: Any) -> str:
        """Generate SHA256 hash for custom cache key with multiple components.

        Args:
            *args: Positional arguments to include in key
            **kwargs: Keyword arguments to include in key

        Returns:
            SHA256 hash string

        Note:
            Non-JSON-serializable arguments are converted to their string representation.
        """

        def safe_serialize(obj: Any) -> str:
            """Serialize object to JSON, falling back to str() if not serializable."""
            try:
                return json.dumps(obj, sort_keys=True)
            except TypeError:
                return str(obj)

        # Use json.dumps for stable serialization, fallback to str() if not serializable
        args_string = "|".join(safe_serialize(arg) for arg in args)

        # Build kwargs string from sorted key-value pairs
        kwargs_string = (
            "|".join(f"{k}={safe_serialize(v)}" for k, v in sorted(kwargs.items()))
            if kwargs
            else ""
        )

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
