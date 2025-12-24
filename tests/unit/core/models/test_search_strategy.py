"""Tests for search strategy detection."""

from __future__ import annotations

import pytest

from core.models.search_strategy import (
    SearchStrategy,
    SearchStrategyInfo,
    detect_search_strategy,
)


class TestSearchStrategyEnum:
    """Tests for SearchStrategy enum values."""

    def test_enum_values_exist(self) -> None:
        """Verify all strategy enum values exist."""
        assert SearchStrategy.NORMAL.value == "normal"
        assert SearchStrategy.SOUNDTRACK.value == "soundtrack"
        assert SearchStrategy.VARIOUS_ARTISTS.value == "various"
        assert SearchStrategy.STRIP_BRACKETS.value == "strip"
        assert SearchStrategy.GREATEST_HITS.value == "hits"


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
    def config(self) -> dict:
        """Provide test config with patterns."""
        return {
            "album_type_detection": {
                "soundtrack_patterns": ["soundtrack", "original score", "OST"],
                "various_artists_names": ["Various Artists", "Various", "VA"],
            }
        }

    def test_normal_album_returns_normal(self, config: dict) -> None:
        """Regular albums should return NORMAL strategy."""
        info = detect_search_strategy("Metallica", "Master of Puppets", config)
        assert info.strategy == SearchStrategy.NORMAL
        assert info.detected_pattern is None

    def test_soundtrack_detected(self, config: dict) -> None:
        """Soundtrack albums should be detected."""
        info = detect_search_strategy("Hans Zimmer", "Inception (Original Soundtrack)", config)
        assert info.strategy == SearchStrategy.SOUNDTRACK

    def test_ost_pattern_detected(self, config: dict) -> None:
        """OST pattern should be detected."""
        info = detect_search_strategy("Various", "Interstellar OST", config)
        assert info.strategy == SearchStrategy.SOUNDTRACK

    def test_various_artists_detected(self, config: dict) -> None:
        """Various Artists should be detected."""
        info = detect_search_strategy("Various Artists", "Metal Hammer Presents", config)
        assert info.strategy == SearchStrategy.VARIOUS_ARTISTS
        assert info.modified_artist is None  # Search without artist

    def test_brackets_detected(self, config: dict) -> None:
        """Special bracket content should trigger strip strategy."""
        info = detect_search_strategy("Ghost", "Prequelle [MESSAGE FROM THE CLERGY]", config)
        assert info.strategy == SearchStrategy.STRIP_BRACKETS
        assert info.modified_album == "Prequelle"

    def test_normal_brackets_not_stripped(self, config: dict) -> None:
        """Normal brackets like (Deluxe) should not trigger strip."""
        info = detect_search_strategy("Artist", "Album (Deluxe Edition)", config)
        assert info.strategy == SearchStrategy.NORMAL

    def test_empty_album_returns_normal(self, config: dict) -> None:
        """Empty album should return NORMAL."""
        info = detect_search_strategy("Artist", "", config)
        assert info.strategy == SearchStrategy.NORMAL

    def test_empty_config_uses_defaults(self) -> None:
        """Empty config should use default patterns."""
        info = detect_search_strategy("Hans Zimmer", "Inception (Original Soundtrack)", {})
        assert info.strategy == SearchStrategy.SOUNDTRACK
