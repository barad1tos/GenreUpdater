"""Tests for UnifiedHashService."""

from src.services.cache.hash import UnifiedHashService


class TestUnifiedHashService:
    """Test cases for UnifiedHashService."""

    def test_hash_album_key_basic(self) -> None:
        """Test basic album key hashing."""
        result = UnifiedHashService.hash_album_key("Artist", "Album")

        # Should return a 64-character SHA256 hex string
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_album_key_normalization(self) -> None:
        """Test that album keys are normalized consistently."""
        # Different cases and whitespace should produce same hash
        hash1 = UnifiedHashService.hash_album_key("Artist", "Album")
        hash2 = UnifiedHashService.hash_album_key(" ARTIST ", " ALBUM ")
        hash3 = UnifiedHashService.hash_album_key("artist", "album")

        assert hash1 == hash2 == hash3

    def test_hash_album_key_uniqueness(self) -> None:
        """Test that different albums produce different hashes."""
        hash1 = UnifiedHashService.hash_album_key("Artist1", "Album1")
        hash2 = UnifiedHashService.hash_album_key("Artist2", "Album2")

        assert hash1 != hash2

    def test_hash_api_key_basic(self) -> None:
        """Test basic API key hashing."""
        result = UnifiedHashService.hash_api_key("Artist", "Album", "spotify")

        # Should return a 64-character SHA256 hex string
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_api_key_source_differentiation(self) -> None:
        """Test that different sources produce different hashes."""
        hash_spotify = UnifiedHashService.hash_api_key("Artist", "Album", "spotify")
        hash_lastfm = UnifiedHashService.hash_api_key("Artist", "Album", "lastfm")

        assert hash_spotify != hash_lastfm

    def test_hash_generic_key_string(self) -> None:
        """Test generic key hashing with strings."""
        result = UnifiedHashService.hash_generic_key("test_string")

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_generic_key_dict(self) -> None:
        """Test generic key hashing with dictionaries."""
        # Same dictionary content should produce same hash regardless of order
        hash1 = UnifiedHashService.hash_generic_key({"b": 2, "a": 1})
        hash2 = UnifiedHashService.hash_generic_key({"a": 1, "b": 2})

        assert hash1 == hash2

    def test_hash_generic_key_list(self) -> None:
        """Test generic key hashing with lists."""
        result = UnifiedHashService.hash_generic_key([1, 2, 3])

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_custom_key_args_only(self) -> None:
        """Test custom key hashing with positional arguments only."""
        result = UnifiedHashService.hash_custom_key("arg1", "arg2", "arg3")

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_custom_key_kwargs_only(self) -> None:
        """Test custom key hashing with keyword arguments only."""
        result = UnifiedHashService.hash_custom_key(key1="value1", key2="value2")

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_custom_key_mixed(self) -> None:
        """Test custom key hashing with mixed arguments."""
        result = UnifiedHashService.hash_custom_key("arg1", "arg2", key1="value1", key2="value2")

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_custom_key_consistency(self) -> None:
        """Test that custom keys are consistent with same arguments."""
        # Same arguments in same order should produce same hash
        hash1 = UnifiedHashService.hash_custom_key("arg1", key="value")
        hash2 = UnifiedHashService.hash_custom_key("arg1", key="value")

        assert hash1 == hash2

    def test_get_algorithm(self) -> None:
        """Test algorithm getter."""
        assert UnifiedHashService.get_algorithm() == "sha256"

    def test_deterministic_hashing(self) -> None:
        """Test that hashing is deterministic across calls."""
        # Test album key determinism
        hash1 = UnifiedHashService.hash_album_key("Artist", "Album")
        hash2 = UnifiedHashService.hash_album_key("Artist", "Album")
        assert hash1 == hash2

        # Test API key determinism
        hash1 = UnifiedHashService.hash_api_key("Artist", "Album", "source")
        hash2 = UnifiedHashService.hash_api_key("Artist", "Album", "source")
        assert hash1 == hash2

        # Test generic key determinism
        hash1 = UnifiedHashService.hash_generic_key("generic_data")
        hash2 = UnifiedHashService.hash_generic_key("generic_data")
        assert hash1 == hash2

        # Test custom key determinism
        hash1 = UnifiedHashService.hash_custom_key("custom", "args", key="value")
        hash2 = UnifiedHashService.hash_custom_key("custom", "args", key="value")
        assert hash1 == hash2
