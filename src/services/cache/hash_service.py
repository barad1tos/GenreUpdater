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

from core.models.normalization import normalize_for_matching


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
        normalized_artist = normalize_for_matching(artist)
        normalized_album = normalize_for_matching(album)
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
        normalized_source = normalize_for_matching(source)
        normalized_artist = normalize_for_matching(artist)
        normalized_album = normalize_for_matching(album)
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
        # Using default=str ensures non-serializable values (datetime, Path, etc.) are converted
        key_string = json.dumps(data, sort_keys=True, default=str) if isinstance(data, dict) else str(data)
        return hashlib.sha256(key_string.encode()).hexdigest()

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
