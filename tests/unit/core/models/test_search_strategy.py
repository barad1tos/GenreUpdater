"""Tests for search strategy detection."""

from __future__ import annotations


from core.models.search_strategy import SearchStrategy, SearchStrategyInfo


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
