"""Property-based tests for UnifiedHashService using Hypothesis."""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

from services.cache.hash_service import UnifiedHashService

HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@pytest.mark.unit
class TestHashAlbumKeyProperties:
    """Property-based tests for hash_album_key."""

    @given(
        artist=st.text(min_size=1, max_size=200),
        album=st.text(min_size=1, max_size=200),
    )
    @settings(max_examples=100)
    def test_determinism(self, artist: str, album: str) -> None:
        """Same inputs always produce same hash."""
        h1 = UnifiedHashService.hash_album_key(artist, album)
        h2 = UnifiedHashService.hash_album_key(artist, album)
        assert h1 == h2

    @given(
        artist=st.text(min_size=1, max_size=200),
        album=st.text(min_size=1, max_size=200),
    )
    @settings(max_examples=100)
    def test_format_invariant_64_hex(self, artist: str, album: str) -> None:
        """Output is always 64-char lowercase hex."""
        h = UnifiedHashService.hash_album_key(artist, album)
        assert HEX_64_PATTERN.match(h)

    @given(artist=st.text(min_size=1, max_size=200))
    @settings(max_examples=100)
    def test_lowercase_normalization_idempotent(self, artist: str) -> None:
        """Applying normalization twice produces same hash as once."""
        normalized_once = artist.strip().lower()
        h1 = UnifiedHashService.hash_album_key(normalized_once, "album")
        h2 = UnifiedHashService.hash_album_key(artist, "album")
        assert h1 == h2

    @given(
        artist=st.text(min_size=1, max_size=200),
        album=st.text(min_size=1, max_size=200),
    )
    @settings(max_examples=100)
    def test_whitespace_insensitive(self, artist: str, album: str) -> None:
        """Leading/trailing whitespace doesn't affect hash."""
        h1 = UnifiedHashService.hash_album_key(artist, album)
        h2 = UnifiedHashService.hash_album_key(f"  {artist}  ", f"  {album}  ")
        assert h1 == h2

    @given(
        artist=st.text(min_size=0, max_size=200),
        album=st.text(min_size=0, max_size=200),
    )
    @settings(max_examples=100)
    def test_never_raises(self, artist: str, album: str) -> None:
        """hash_album_key never raises for any string inputs."""
        result = UnifiedHashService.hash_album_key(artist, album)
        assert isinstance(result, str)
        assert len(result) == 64


@pytest.mark.unit
class TestHashGenericKeyProperties:
    """Property-based tests for hash_generic_key."""

    @given(
        data=st.dictionaries(
            st.text(min_size=1, max_size=50),
            st.one_of(
                st.integers(),
                st.text(max_size=100),
                st.booleans(),
            ),
            max_size=10,
        ),
    )
    @settings(max_examples=100)
    def test_dict_key_order_independence(self, data: dict) -> None:
        """Dict key order doesn't affect hash."""
        h1 = UnifiedHashService.hash_generic_key(data)
        # Reverse the dict
        reversed_data = dict(reversed(list(data.items())))
        h2 = UnifiedHashService.hash_generic_key(reversed_data)
        assert h1 == h2

    @given(data=st.one_of(st.text(), st.integers(), st.booleans()))
    @settings(max_examples=100)
    def test_always_returns_64_hex(self, data: str | int | bool) -> None:
        """Generic key always returns 64-char hex."""
        h = UnifiedHashService.hash_generic_key(data)
        assert HEX_64_PATTERN.match(h)

    @given(data=st.one_of(st.text(), st.integers(), st.booleans()))
    @settings(max_examples=100)
    def test_determinism(self, data: str | int | bool) -> None:
        """Same input always produces same hash."""
        h1 = UnifiedHashService.hash_generic_key(data)
        h2 = UnifiedHashService.hash_generic_key(data)
        assert h1 == h2


@pytest.mark.unit
class TestHashApiKeyProperties:
    """Property-based tests for hash_api_key."""

    @given(
        artist=st.text(min_size=1, max_size=100),
        album=st.text(min_size=1, max_size=100),
        source=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=100)
    def test_format_invariant(self, artist: str, album: str, source: str) -> None:
        """API key is always 64-char hex."""
        h = UnifiedHashService.hash_api_key(artist, album, source)
        assert HEX_64_PATTERN.match(h)

    @given(
        artist=st.text(min_size=1, max_size=100),
        album=st.text(min_size=1, max_size=100),
        source=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=100)
    def test_source_normalization_idempotent(self, artist: str, album: str, source: str) -> None:
        """Applying normalization twice produces same hash as once."""
        normalized_source = source.strip().lower()
        h1 = UnifiedHashService.hash_api_key(artist, album, normalized_source)
        h2 = UnifiedHashService.hash_api_key(artist, album, source)
        assert h1 == h2
