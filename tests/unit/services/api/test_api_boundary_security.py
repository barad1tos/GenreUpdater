"""Tests for API client input boundary security.

Verifies that adversarial inputs (control chars, null bytes, extremely long strings)
do not crash API clients during request construction.

The API clients delegate URL encoding to aiohttp, so these tests verify that
the clients can construct requests without crashing, not that the encoding is correct.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.api.applemusic import AppleMusicClient
from services.api.discogs import DiscogsClient
from services.api.musicbrainz import MusicBrainzClient
from services.api.year_scoring import ArtistContext
from tests.factories import create_test_app_config


@pytest.mark.unit
@pytest.mark.asyncio
class TestMusicBrainzInputBoundary:
    """MusicBrainz client handles adversarial inputs without crashing."""

    @staticmethod
    def _create_mock_client() -> MusicBrainzClient:
        """Create a MusicBrainz client with mocked dependencies."""
        console_logger = MagicMock()
        error_logger = MagicMock()
        analytics = MagicMock()

        make_api_request_func = AsyncMock()
        make_api_request_func.return_value = {"release-groups": [], "count": 0}

        score_release_func = MagicMock(return_value=0.0)

        return MusicBrainzClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=make_api_request_func,
            score_release_func=score_release_func,
            analytics=analytics,
        )

    async def test_control_chars_in_artist_name_handled(self) -> None:
        """Control characters in artist name don't crash Lucene escaping."""
        client = self._create_mock_client()

        artist_with_control_chars = "Artist\x00\x01\x02\x03Name"
        album = "Normal Album"

        releases = await client.get_scored_releases(
            artist_norm=artist_with_control_chars,
            album_norm=album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_null_bytes_in_album_name_handled(self) -> None:
        """Null bytes in album name don't crash request construction."""
        client = self._create_mock_client()

        artist = "Normal Artist"
        album_with_nulls = "Album\x00With\x00Nulls"

        releases = await client.get_scored_releases(
            artist_norm=artist,
            album_norm=album_with_nulls,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_extremely_long_artist_name_handled(self) -> None:
        """Very long input (10,000 chars) doesn't crash the client."""
        client = self._create_mock_client()

        extremely_long_artist = "A" * 10000
        album = "Normal Album"

        releases = await client.get_scored_releases(
            artist_norm=extremely_long_artist,
            album_norm=album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_extremely_long_album_name_handled(self) -> None:
        """Very long album name doesn't crash the client."""
        client = self._create_mock_client()

        artist = "Normal Artist"
        extremely_long_album = "B" * 10000

        releases = await client.get_scored_releases(
            artist_norm=artist,
            album_norm=extremely_long_album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_unicode_normalization_in_search(self) -> None:
        """Unicode characters (CJK, emoji) are handled."""
        client = self._create_mock_client()

        unicode_artist = "日本語アーティスト🎵"
        unicode_album = "中文專輯名稱🎶"

        releases = await client.get_scored_releases(
            artist_norm=unicode_artist,
            album_norm=unicode_album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_empty_artist_and_album_handled(self) -> None:
        """Empty strings don't crash the client."""
        client = self._create_mock_client()

        releases = await client.get_scored_releases(
            artist_norm="",
            album_norm="",
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_lucene_special_chars_handled(self) -> None:
        """Lucene special characters are properly escaped."""
        client = self._create_mock_client()

        artist_with_lucene_chars = 'Artist+-&|!(){}[]^"~*?:\\/Name'
        album = "Normal Album"

        releases = await client.get_scored_releases(
            artist_norm=artist_with_lucene_chars,
            album_norm=album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_whitespace_only_inputs_handled(self) -> None:
        """Whitespace-only inputs don't crash the client."""
        client = self._create_mock_client()

        releases = await client.get_scored_releases(
            artist_norm="   \t\n\r   ",
            album_norm="   \t\n\r   ",
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)


@pytest.mark.unit
@pytest.mark.asyncio
class TestDiscogsInputBoundary:
    """Discogs client handles adversarial inputs without crashing."""

    @staticmethod
    def _create_mock_client() -> DiscogsClient:
        """Create a Discogs client with mocked dependencies."""
        console_logger = MagicMock()
        error_logger = MagicMock()
        analytics = MagicMock()

        make_api_request_func = AsyncMock()
        make_api_request_func.return_value = {"results": [], "pagination": {}}

        score_release_func = MagicMock(return_value=0.0)

        discogs_auth = "fake_value_for_testing"
        test_config = create_test_app_config()
        return DiscogsClient(
            token=discogs_auth,
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=analytics,
            make_api_request_func=make_api_request_func,
            score_release_func=score_release_func,
            scoring_config=test_config.year_retrieval,
            config=test_config,
        )

    async def test_control_chars_in_search_query_handled(self) -> None:
        """Control characters don't crash Discogs search."""
        client = self._create_mock_client()

        artist_with_control = "Artist\x00\x01\x02Name"
        album = "Normal Album"

        releases = await client.get_scored_releases(
            artist_norm=artist_with_control,
            album_norm=album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_empty_artist_name_handled(self) -> None:
        """Empty artist name doesn't crash Discogs search."""
        client = self._create_mock_client()

        releases = await client.get_scored_releases(
            artist_norm="",
            album_norm="Some Album",
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_empty_album_name_handled(self) -> None:
        """Empty album name doesn't crash Discogs search."""
        client = self._create_mock_client()

        releases = await client.get_scored_releases(
            artist_norm="Some Artist",
            album_norm="",
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_extremely_long_search_term(self) -> None:
        """Very long search term doesn't crash."""
        client = self._create_mock_client()

        long_artist = "X" * 10000
        long_album = "Y" * 10000

        releases = await client.get_scored_releases(
            artist_norm=long_artist,
            album_norm=long_album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_unicode_search_handled(self) -> None:
        """Unicode characters in search don't crash."""
        client = self._create_mock_client()

        unicode_artist = "Артист🎵"
        unicode_album = "Альбом🎶"

        releases = await client.get_scored_releases(
            artist_norm=unicode_artist,
            album_norm=unicode_album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_special_url_chars_handled(self) -> None:
        """Aiohttp handles special URL characters."""
        client = self._create_mock_client()

        artist_with_special = "Artist & The Band / Group"
        album = "Album: Subtitle (Deluxe)"

        releases = await client.get_scored_releases(
            artist_norm=artist_with_special,
            album_norm=album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_whitespace_only_inputs_handled(self) -> None:
        """Whitespace-only inputs don't crash."""
        client = self._create_mock_client()

        releases = await client.get_scored_releases(
            artist_norm="   \t\n   ",
            album_norm="   \r\n   ",
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)


@pytest.mark.unit
@pytest.mark.asyncio
class TestAppleMusicInputBoundary:
    """iTunes/Apple Music client handles adversarial inputs without crashing."""

    @staticmethod
    def _create_mock_client() -> AppleMusicClient:
        """Create an Apple Music client with mocked dependencies."""
        console_logger = MagicMock()
        error_logger = MagicMock()

        make_api_request_func = AsyncMock()
        make_api_request_func.return_value = {"resultCount": 0, "results": []}

        score_release_func = MagicMock(return_value=0.0)

        return AppleMusicClient(
            console_logger=console_logger,
            error_logger=error_logger,
            make_api_request_func=make_api_request_func,
            score_release_func=score_release_func,
        )

    async def test_special_chars_in_itunes_search(self) -> None:
        """Special characters don't crash iTunes search."""
        client = self._create_mock_client()

        artist_with_special = "Artist & The Band / Group"
        album = "Album: Subtitle (Deluxe)"

        releases = await client.get_scored_releases(
            artist_norm=artist_with_special,
            album_norm=album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_control_chars_in_search(self) -> None:
        """Control characters don't crash iTunes search."""
        client = self._create_mock_client()

        artist_with_control = "Artist\x00\x01\x02Name"
        album = "Album\x03\x04Title"

        releases = await client.get_scored_releases(
            artist_norm=artist_with_control,
            album_norm=album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_extremely_long_search_term(self) -> None:
        """Very long search term doesn't crash."""
        client = self._create_mock_client()

        long_artist = "Z" * 10000
        long_album = "W" * 10000

        releases = await client.get_scored_releases(
            artist_norm=long_artist,
            album_norm=long_album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_unicode_search_handled(self) -> None:
        """Unicode characters in search don't crash."""
        client = self._create_mock_client()

        unicode_artist = "한국어🎵"
        unicode_album = "日本語🎶"

        releases = await client.get_scored_releases(
            artist_norm=unicode_artist,
            album_norm=unicode_album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_empty_inputs_handled(self) -> None:
        """Empty inputs don't crash."""
        client = self._create_mock_client()

        releases = await client.get_scored_releases(
            artist_norm="",
            album_norm="",
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_whitespace_only_inputs_handled(self) -> None:
        """Whitespace-only inputs don't crash."""
        client = self._create_mock_client()

        releases = await client.get_scored_releases(
            artist_norm="   \t\n   ",
            album_norm="   \r\n   ",
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_newline_in_search_terms(self) -> None:
        """Newline characters in search terms are handled."""
        client = self._create_mock_client()

        artist_with_newline = "Artist\nName\rHere"
        album_with_newline = "Album\nTitle\rHere"

        releases = await client.get_scored_releases(
            artist_norm=artist_with_newline,
            album_norm=album_with_newline,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)

    async def test_mixed_adversarial_inputs(self) -> None:
        """Mixed adversarial inputs (control chars + unicode + long) don't crash."""
        client = self._create_mock_client()

        mixed_artist = "A" * 1000 + "\x00日本語\n" + "🎵" * 100
        mixed_album = "B" * 1000 + "\x01中文\r" + "🎶" * 100

        releases = await client.get_scored_releases(
            artist_norm=mixed_artist,
            album_norm=mixed_album,
            artist_context=ArtistContext(),
        )

        assert isinstance(releases, list)
