"""Tests for UnifiedHashService adversarial inputs and collision resistance."""

import pytest
from services.cache.hash_service import UnifiedHashService


@pytest.mark.unit
class TestHashServiceAdversarialInputs:
    """Hash service produces valid hashes for adversarial inputs."""

    def test_empty_artist_produces_valid_hash(self) -> None:
        result = UnifiedHashService.hash_album_key("", "Album")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_empty_album_produces_valid_hash(self) -> None:
        result = UnifiedHashService.hash_album_key("Artist", "")
        assert len(result) == 64

    def test_both_empty_produces_valid_hash(self) -> None:
        result = UnifiedHashService.hash_album_key("", "")
        assert len(result) == 64

    def test_null_bytes_in_input_produces_valid_hash(self) -> None:
        result = UnifiedHashService.hash_album_key("Art\x00ist", "Al\x00bum")
        assert len(result) == 64

    def test_control_chars_produce_valid_hash(self) -> None:
        result = UnifiedHashService.hash_album_key("Art\x01\x02ist", "Album")
        assert len(result) == 64

    def test_extremely_long_input_produces_valid_hash(self) -> None:
        long_name = "A" * 100_000
        result = UnifiedHashService.hash_album_key(long_name, long_name)
        assert len(result) == 64

    def test_unicode_artist_produces_valid_hash(self) -> None:
        result = UnifiedHashService.hash_album_key("Björk", "Homogenic")
        assert len(result) == 64

    def test_cjk_characters_produce_valid_hash(self) -> None:
        result = UnifiedHashService.hash_album_key("坂本龍一", "千のナイフ")
        assert len(result) == 64

    def test_emoji_in_input_produces_valid_hash(self) -> None:
        result = UnifiedHashService.hash_album_key("🎵Artist🎵", "🎶Album🎶")
        assert len(result) == 64

    def test_hash_format_always_64_hex_chars(self) -> None:
        """All hash methods always return 64 hex chars."""
        inputs = [
            UnifiedHashService.hash_album_key("a", "b"),
            UnifiedHashService.hash_api_key("a", "b", "src"),
            UnifiedHashService.hash_generic_key("data"),
            UnifiedHashService.hash_pending_key("123"),
        ]
        for h in inputs:
            assert len(h) == 64
            assert all(c in "0123456789abcdef" for c in h)


@pytest.mark.unit
class TestHashServiceCollisionResistance:
    """Distinct inputs produce distinct hashes."""

    def test_similar_names_produce_different_hashes(self) -> None:
        h1 = UnifiedHashService.hash_album_key("Artist", "Album1")
        h2 = UnifiedHashService.hash_album_key("Artist", "Album2")
        assert h1 != h2

    def test_swapped_artist_album_produce_different_hashes(self) -> None:
        h1 = UnifiedHashService.hash_album_key("Foo", "Bar")
        h2 = UnifiedHashService.hash_album_key("Bar", "Foo")
        assert h1 != h2

    def test_api_key_source_differentiates_hash(self) -> None:
        h1 = UnifiedHashService.hash_api_key("A", "B", "musicbrainz")
        h2 = UnifiedHashService.hash_api_key("A", "B", "discogs")
        assert h1 != h2

    def test_album_key_vs_api_key_different(self) -> None:
        h1 = UnifiedHashService.hash_album_key("A", "B")
        h2 = UnifiedHashService.hash_api_key("A", "B", "src")
        assert h1 != h2

    def test_delimiter_in_artist_causes_known_collision(self) -> None:
        """Known limitation: pipe '|' in artist causes collision with different artist/album split.

        hash("foo|bar", "baz") normalizes to sha256("foo|bar|baz")
        hash("foo", "bar|baz") normalizes to sha256("foo|bar|baz")
        Both produce identical hashes — this is a documented limitation.
        """
        h1 = UnifiedHashService.hash_album_key("foo|bar", "baz")
        h2 = UnifiedHashService.hash_album_key("foo", "bar|baz")
        assert h1 == h2  # Known collision due to delimiter in input

    def test_colon_in_source_does_not_cause_api_key_collision(self) -> None:
        """Source with colon ':' delimiter - check behavior."""
        h1 = UnifiedHashService.hash_api_key("A", "B", "src:extra")
        h2 = UnifiedHashService.hash_api_key("A", "B", "src")
        assert h1 != h2  # Different because "src:extra" != "src"

    def test_whitespace_only_inputs_normalized_consistently(self) -> None:
        """Whitespace-only strings normalize to empty, producing same hash."""
        h1 = UnifiedHashService.hash_album_key("   ", "   ")
        h2 = UnifiedHashService.hash_album_key("", "")
        assert h1 == h2


@pytest.mark.unit
class TestHashServiceGenericKeyAdversarial:
    """Generic key handles edge-case data types."""

    def test_nested_dict_produces_valid_hash(self) -> None:
        data = {"a": {"b": {"c": 1}}}
        result = UnifiedHashService.hash_generic_key(data)
        assert len(result) == 64

    def test_none_value_produces_valid_hash(self) -> None:
        result = UnifiedHashService.hash_generic_key(None)
        assert len(result) == 64

    def test_boolean_produces_valid_hash(self) -> None:
        result = UnifiedHashService.hash_generic_key(True)
        assert len(result) == 64

    def test_integer_produces_valid_hash(self) -> None:
        result = UnifiedHashService.hash_generic_key(42)
        assert len(result) == 64
