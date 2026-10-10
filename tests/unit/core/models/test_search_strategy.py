"""Tests for search strategy detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.models.search_strategy import (
    SearchStrategy,
    SearchStrategyInfo,
    detect_search_strategy,
)
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


class TestSearchStrategyEnum:
    """Tests for SearchStrategy enum values."""

    def test_enum_values_exist(self) -> None:
        """Verify all strategy enum values exist."""
        assert SearchStrategy.NORMAL.value == "normal"
        assert SearchStrategy.SOUNDTRACK.value == "soundtrack"
        assert SearchStrategy.VARIOUS_ARTISTS.value == "various"
        assert SearchStrategy.STRIP_BRACKETS.value == "strip"


class TestSearchStrategyInfo:
    """Tests for SearchStrategyInfo dataclass."""

    def test_default_values(self) -> None:
        """Verify default values for optional fields."""
        info = SearchStrategyInfo(strategy=SearchStrategy.NORMAL)
        assert info.strategy == SearchStrategy.NORMAL
        assert info.detected_pattern is None
        assert info.modified_artist is None
        assert info.modified_album is None

    def test_all_fields(self) -> None:
        """Verify all fields can be set."""
        info = SearchStrategyInfo(
            strategy=SearchStrategy.SOUNDTRACK,
            detected_pattern="soundtrack",
            modified_artist="Inception",
            modified_album="Inception",
        )
        assert info.strategy == SearchStrategy.SOUNDTRACK
        assert info.detected_pattern == "soundtrack"
        assert info.modified_artist == "Inception"
        assert info.modified_album == "Inception"


class TestDetectSearchStrategy:
    """Tests for detect_search_strategy function."""

    @pytest.fixture
    def config(self) -> AppConfig:
        """Provide test config with patterns."""
        return create_test_app_config(
            album_type_detection={
                "soundtrack_patterns": ["soundtrack", "original score", "OST"],
                "various_artists_names": ["Various Artists", "Various", "VA"],
            }
        )

    def test_normal_album_returns_normal(self, config: AppConfig) -> None:
        """Regular albums should return NORMAL strategy."""
        info = detect_search_strategy("Metallica", "Master of Puppets", config)
        assert info.strategy == SearchStrategy.NORMAL
        assert info.detected_pattern is None

    def test_soundtrack_detected(self, config: AppConfig) -> None:
        """Soundtrack albums should be detected."""
        info = detect_search_strategy("Hans Zimmer", "Inception (Original Soundtrack)", config)
        assert info.strategy == SearchStrategy.SOUNDTRACK

    def test_ost_pattern_detected(self, config: AppConfig) -> None:
        """OST pattern should be detected."""
        info = detect_search_strategy("Various", "Interstellar OST", config)
        assert info.strategy == SearchStrategy.SOUNDTRACK

    def test_various_artists_detected(self, config: AppConfig) -> None:
        """Various Artists should be detected."""
        info = detect_search_strategy("Various Artists", "Metal Hammer Presents", config)
        assert info.strategy == SearchStrategy.VARIOUS_ARTISTS
        assert info.modified_artist is None  # Search without artist

    def test_brackets_detected(self, config: AppConfig) -> None:
        """Special bracket content should trigger strip strategy."""
        info = detect_search_strategy("Ghost", "Prequelle [MESSAGE FROM THE CLERGY]", config)
        assert info.strategy == SearchStrategy.STRIP_BRACKETS
        assert info.modified_album == "Prequelle"

    def test_normal_brackets_not_stripped(self, config: AppConfig) -> None:
        """Normal brackets like (Deluxe) should not trigger strip."""
        info = detect_search_strategy("Artist", "Album (Deluxe Edition)", config)
        assert info.strategy == SearchStrategy.NORMAL

    def test_empty_album_returns_normal(self, config: AppConfig) -> None:
        """Empty album should return NORMAL."""
        info = detect_search_strategy("Artist", "", config)
        assert info.strategy == SearchStrategy.NORMAL

    def test_default_config_uses_defaults(self) -> None:
        """Default config should use default patterns."""
        config = create_test_app_config()
        info = detect_search_strategy("Hans Zimmer", "Inception (Original Soundtrack)", config)
        assert info.strategy == SearchStrategy.SOUNDTRACK


class TestEdgeCases:
    """Tests for edge cases and Unicode handling."""

    @pytest.fixture
    def config(self) -> AppConfig:
        """Provide test config with custom soundtrack and various artists patterns."""
        return create_test_app_config(
            album_type_detection={
                "soundtrack_patterns": ["soundtrack", "OST"],
                "various_artists_names": ["Various Artists", "Різні виконавці"],
            }
        )

    def test_unicode_various_artists(self, config: AppConfig) -> None:
        """Ukrainian Various Artists should be detected."""
        info = detect_search_strategy("Різні виконавці", "Ukrainian Hits", config)
        assert info.strategy == SearchStrategy.VARIOUS_ARTISTS

    def test_case_insensitive_patterns(self, config: AppConfig) -> None:
        """Pattern matching should be case insensitive."""
        info = detect_search_strategy("Artist", "Album SOUNDTRACK", config)
        assert info.strategy == SearchStrategy.SOUNDTRACK

    def test_whitespace_handling(self, config: AppConfig) -> None:
        """Whitespace should be handled gracefully."""
        info = detect_search_strategy("  Various Artists  ", "Album", config)
        assert info.strategy == SearchStrategy.VARIOUS_ARTISTS

    def test_detection_priority(self, config: AppConfig) -> None:
        """Soundtrack takes priority over Various Artists."""
        info = detect_search_strategy("Various Artists", "Movie Soundtrack", config)
        assert info.strategy == SearchStrategy.SOUNDTRACK


class TestSoundtrackTitle:
    """The movie title is the text before the soundtrack label, whichever pattern matches first."""

    @pytest.fixture
    def config(self) -> AppConfig:
        return create_test_app_config()

    @pytest.mark.parametrize(
        ("album", "title"),
        [
            ("Inception (Original Motion Picture Soundtrack)", "Inception"),
            ("Inception - Original Motion Picture Soundtrack", "Inception"),
            ("Inception (Music from the Motion Picture)", "Inception"),
            ("Harry Potter: Original Motion Picture Soundtrack", "Harry Potter"),
            ("Star Wars: Episode IV - A New Hope (Original Soundtrack)", "Star Wars: Episode IV - A New Hope"),
            ("Interstellar OST", "Interstellar"),
            ("Inception\u2014Soundtrack", "Inception"),
            ("Mission: Impossible Soundtrack", "Mission: Impossible"),
            ("Star Wars: The Force Awakens OST", "Star Wars: The Force Awakens"),
            ("Star Trek: The Motion Picture (Original Soundtrack)", "Star Trek: The Motion Picture"),
            ("Dune: Part Two (Original Motion Picture Soundtrack)", "Dune: Part Two"),
            ("The Sound of Music Soundtrack", "The Sound of Music"),
            ("The Last Song Soundtrack", "The Last Song"),
            ("Grease: The Original Soundtrack from the Motion Picture", "Grease"),
        ],
    )
    def test_title_is_cut_before_the_label(self, config: AppConfig, album: str, title: str) -> None:
        info = detect_search_strategy("Hans Zimmer", album, config)
        assert (info.modified_artist, info.modified_album) == (title, title)

    @pytest.mark.parametrize("album", ["Ghost Stories", "Lost Highway", "Frost"])
    def test_a_pattern_inside_a_word_is_not_a_soundtrack(self, config: AppConfig, album: str) -> None:
        assert detect_search_strategy("Artist", album, config).strategy is SearchStrategy.NORMAL

    @pytest.mark.parametrize(("pattern", "album"), [("(OST)", "Akira (OST)"), ("O.S.T.", "Akira O.S.T.")])
    def test_a_configured_pattern_with_punctuation_matches(self, pattern: str, album: str) -> None:
        config = create_test_app_config(album_type_detection={"soundtrack_patterns": [pattern]})
        info = detect_search_strategy("Geinoh Yamashirogumi", album, config)
        assert (info.strategy, info.modified_album) == (SearchStrategy.SOUNDTRACK, "Akira")
