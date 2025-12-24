# Alternative Search Strategies Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add fallback search strategies for albums that fail standard API matching (soundtracks, compilations, special editions).

**Architecture:** When standard API search returns 0 results, detect album type and try alternative query (e.g., search by movie name for soundtracks, strip brackets for special editions).

**Tech Stack:** Python 3.13, dataclasses, Enum, pytest, existing `album_type.py` patterns infrastructure.

---

## Task 1: Add Config Patterns

**Files:**
- Modify: `config.yaml:227-240`
- Modify: `my-config.yaml:332-400` (parallel structure)

**Step 1: Add soundtrack and various artists patterns to config.yaml**

Add after line 239 (before `# -----------------------------------------------------------------------`):

```yaml
  # Soundtrack patterns - for extracting movie name from album
  soundtrack_patterns:
    - soundtrack
    - original score
    - OST
    - motion picture
    - film score

  # Various Artists names - search by album only
  various_artists_names:
    - Various Artists
    - Various
    - VA
    - Різні виконавці
```

**Step 2: Verify YAML syntax**

Run: `uv run python -c "import yaml; yaml.safe_load(open('config.yaml'))"`
Expected: No output (success)

**Step 3: Commit**

```bash
git add config.yaml
git commit -m "feat(config): add soundtrack and various artists patterns"
```

---

## Task 2: Create SearchStrategy Enum and Dataclass

**Files:**
- Create: `src/core/models/search_strategy.py`
- Test: `tests/unit/core/models/test_search_strategy.py`

**Step 1: Write the failing test for SearchStrategy enum**

Create `tests/unit/core/models/test_search_strategy.py`:

```python
"""Tests for search strategy detection."""

from __future__ import annotations

import pytest

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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/core/models/test_search_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.models.search_strategy'`

**Step 3: Write minimal implementation**

Create `src/core/models/search_strategy.py`:

```python
"""Search strategy detection for alternative API queries.

This module provides detection of album types that require alternative
search strategies when standard API queries return no results.

Different from album_type.py:
- album_type: How to HANDLE year once found (skip, update, mark)
- search_strategy: How to FIND year in first place (modify query)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "SearchStrategy",
    "SearchStrategyInfo",
]


class SearchStrategy(Enum):
    """Search strategy for API queries."""

    NORMAL = "normal"  # No modification needed
    SOUNDTRACK = "soundtrack"  # Extract movie name from album
    VARIOUS_ARTISTS = "various"  # Search by album only
    STRIP_BRACKETS = "strip"  # Remove [SPECIAL TEXT] from album
    GREATEST_HITS = "hits"  # Try artist + "Greatest Hits"


@dataclass(frozen=True, slots=True)
class SearchStrategyInfo:
    """Information about detected search strategy."""

    strategy: SearchStrategy
    detected_pattern: str | None = None
    modified_artist: str | None = None
    modified_album: str | None = None
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/core/models/test_search_strategy.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/core/models/search_strategy.py tests/unit/core/models/test_search_strategy.py
git commit -m "feat(search): add SearchStrategy enum and SearchStrategyInfo dataclass"
```

---

## Task 3: Implement detect_search_strategy Function

**Files:**
- Modify: `src/core/models/search_strategy.py`
- Modify: `tests/unit/core/models/test_search_strategy.py`

**Step 1: Write failing tests for detection**

Add to `tests/unit/core/models/test_search_strategy.py`:

```python
from core.models.search_strategy import detect_search_strategy


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
        info = detect_search_strategy(
            "Hans Zimmer", "Inception (Original Soundtrack)", config
        )
        assert info.strategy == SearchStrategy.SOUNDTRACK
        assert info.detected_pattern == "soundtrack"

    def test_ost_pattern_detected(self, config: dict) -> None:
        """OST pattern should be detected."""
        info = detect_search_strategy("Various", "Interstellar OST", config)
        assert info.strategy == SearchStrategy.SOUNDTRACK
        assert info.detected_pattern == "OST"

    def test_various_artists_detected(self, config: dict) -> None:
        """Various Artists should be detected."""
        info = detect_search_strategy(
            "Various Artists", "Metal Hammer Presents", config
        )
        assert info.strategy == SearchStrategy.VARIOUS_ARTISTS
        assert info.modified_artist is None  # Search without artist

    def test_brackets_detected(self, config: dict) -> None:
        """Special bracket content should trigger strip strategy."""
        info = detect_search_strategy(
            "Ghost", "Prequelle [MESSAGE FROM THE CLERGY]", config
        )
        assert info.strategy == SearchStrategy.STRIP_BRACKETS
        assert info.modified_album == "Prequelle"

    def test_normal_brackets_not_stripped(self, config: dict) -> None:
        """Normal brackets like (Deluxe) should not trigger strip."""
        info = detect_search_strategy("Artist", "Album (Deluxe Edition)", config)
        # Deluxe is a reissue pattern, not unusual bracket content
        assert info.strategy == SearchStrategy.NORMAL

    def test_empty_album_returns_normal(self, config: dict) -> None:
        """Empty album should return NORMAL."""
        info = detect_search_strategy("Artist", "", config)
        assert info.strategy == SearchStrategy.NORMAL

    def test_empty_config_uses_defaults(self) -> None:
        """Empty config should use default patterns."""
        info = detect_search_strategy(
            "Hans Zimmer", "Inception (Original Soundtrack)", {}
        )
        assert info.strategy == SearchStrategy.SOUNDTRACK
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/models/test_search_strategy.py::TestDetectSearchStrategy -v`
Expected: FAIL with `cannot import name 'detect_search_strategy'`

**Step 3: Implement detect_search_strategy**

Add to `src/core/models/search_strategy.py`:

```python
import re
from typing import Any, Final

# Add to __all__
__all__ = [
    "SearchStrategy",
    "SearchStrategyInfo",
    "detect_search_strategy",
]

# Default patterns (used when config not provided)
_DEFAULT_SOUNDTRACK_PATTERNS: Final[frozenset[str]] = frozenset({
    "soundtrack",
    "original score",
    "OST",
    "motion picture",
    "film score",
})

_DEFAULT_VARIOUS_ARTISTS: Final[frozenset[str]] = frozenset({
    "Various Artists",
    "Various",
    "VA",
    "Різні виконавці",
})


def _get_patterns(config: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    """Get soundtrack and various artists patterns from config or defaults."""
    album_config = config.get("album_type_detection", {})

    soundtrack = frozenset(
        album_config.get("soundtrack_patterns", list(_DEFAULT_SOUNDTRACK_PATTERNS))
    )
    various = frozenset(
        album_config.get("various_artists_names", list(_DEFAULT_VARIOUS_ARTISTS))
    )
    return soundtrack, various


def _is_soundtrack(album: str, patterns: frozenset[str]) -> str | None:
    """Check if album matches soundtrack patterns. Returns matched pattern."""
    album_lower = album.lower()
    for pattern in patterns:
        if pattern.lower() in album_lower:
            return pattern
    return None


def _is_various_artists(artist: str, patterns: frozenset[str]) -> bool:
    """Check if artist is Various Artists."""
    artist_lower = artist.lower().strip()
    return any(p.lower() == artist_lower for p in patterns)


def _has_unusual_brackets(album: str) -> tuple[bool, str | None]:
    """Check for unusual bracket content like [MESSAGE FROM THE CLERGY].

    Returns (has_unusual, stripped_album).
    """
    # Match [CONTENT] where content is mostly uppercase or unusual
    bracket_match = re.search(r'\[([^\]]+)\]', album)
    if not bracket_match:
        return False, None

    content = bracket_match.group(1)
    # Unusual = mostly uppercase or contains unusual characters
    # Skip normal patterns like "Deluxe", "Remastered", etc.
    normal_patterns = {"deluxe", "remaster", "bonus", "disc", "cd", "version"}
    if content.lower() in normal_patterns or any(p in content.lower() for p in normal_patterns):
        return False, None

    # If mostly uppercase or long text, it's unusual
    if len(content) > 10 or content.isupper():
        stripped = re.sub(r'\s*\[[^\]]+\]\s*', '', album).strip()
        return True, stripped

    return False, None


def detect_search_strategy(
    artist: str,
    album: str,
    config: dict[str, Any],
) -> SearchStrategyInfo:
    """Detect which search strategy to use for API queries.

    Detection order (first match wins):
    1. Soundtrack patterns in album
    2. Various Artists as artist
    3. Unusual bracket content
    4. Default: NORMAL

    Args:
        artist: Artist name
        album: Album name
        config: Application configuration

    Returns:
        SearchStrategyInfo with strategy and modifications
    """
    if not album:
        return SearchStrategyInfo(strategy=SearchStrategy.NORMAL)

    soundtrack_patterns, various_patterns = _get_patterns(config)

    # 1. Check for soundtrack
    if pattern := _is_soundtrack(album, soundtrack_patterns):
        # Extract movie name (text before the soundtrack pattern)
        album_lower = album.lower()
        idx = album_lower.find(pattern.lower())
        if idx > 0:
            movie_name = album[:idx].strip().rstrip("([-–—")
            if movie_name:
                return SearchStrategyInfo(
                    strategy=SearchStrategy.SOUNDTRACK,
                    detected_pattern=pattern,
                    modified_artist=movie_name,
                    modified_album=movie_name,
                )
        return SearchStrategyInfo(
            strategy=SearchStrategy.SOUNDTRACK,
            detected_pattern=pattern,
        )

    # 2. Check for Various Artists
    if _is_various_artists(artist, various_patterns):
        return SearchStrategyInfo(
            strategy=SearchStrategy.VARIOUS_ARTISTS,
            detected_pattern=artist,
            modified_artist=None,  # Search without artist
            modified_album=album,
        )

    # 3. Check for unusual brackets
    has_unusual, stripped = _has_unusual_brackets(album)
    if has_unusual and stripped:
        return SearchStrategyInfo(
            strategy=SearchStrategy.STRIP_BRACKETS,
            detected_pattern="brackets",
            modified_artist=artist,
            modified_album=stripped,
        )

    # 4. Default: normal
    return SearchStrategyInfo(strategy=SearchStrategy.NORMAL)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/models/test_search_strategy.py -v`
Expected: PASS

**Step 5: Type check**

Run: `uv run ty check src/core/models/search_strategy.py --python .venv`
Expected: No errors

**Step 6: Commit**

```bash
git add src/core/models/search_strategy.py tests/unit/core/models/test_search_strategy.py
git commit -m "feat(search): implement detect_search_strategy function"
```

---

## Task 4: Add Fallback Logic to YearSearchCoordinator

**Files:**
- Modify: `src/services/api/year_search_coordinator.py:91-113`
- Test: `tests/integration/test_alternative_search.py`

**Step 1: Write failing integration test**

Create `tests/integration/test_alternative_search.py`:

```python
"""Integration tests for alternative search fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.api.year_search_coordinator import YearSearchCoordinator


class TestAlternativeSearchFallback:
    """Tests for alternative search fallback mechanism."""

    @pytest.fixture
    def mock_coordinator(self) -> YearSearchCoordinator:
        """Create coordinator with mocked dependencies."""
        mock_logger = MagicMock()
        config = {
            "year_retrieval": {
                "preferred_api": "musicbrainz",
                "api_auth": {"use_lastfm": False},
            },
            "album_type_detection": {
                "soundtrack_patterns": ["soundtrack", "OST"],
                "various_artists_names": ["Various Artists"],
            },
        }

        coordinator = YearSearchCoordinator(
            console_logger=mock_logger,
            error_logger=mock_logger,
            config=config,
            musicbrainz_client=AsyncMock(),
            discogs_client=AsyncMock(),
            lastfm_client=None,
            applemusic_client=AsyncMock(),
            release_scorer=MagicMock(),
        )
        return coordinator

    @pytest.mark.asyncio
    async def test_fallback_not_triggered_when_results_exist(
        self, mock_coordinator: YearSearchCoordinator
    ) -> None:
        """No fallback when standard search returns results."""
        # Mock standard search returning results
        mock_coordinator._execute_standard_api_search = AsyncMock(
            return_value=[{"year": "2020", "score": 90}]
        )

        results = await mock_coordinator.fetch_all_api_results(
            artist_norm="ghost",
            album_norm="prequelle",
            artist_region=None,
            log_artist="Ghost",
            log_album="Prequelle",
        )

        assert len(results) == 1
        # Standard search called once
        mock_coordinator._execute_standard_api_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_triggered_for_soundtrack(
        self, mock_coordinator: YearSearchCoordinator
    ) -> None:
        """Fallback triggered for soundtrack albums."""
        call_count = 0

        async def mock_search(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []  # First call: empty
            return [{"year": "2010", "score": 85}]  # Second call: results

        mock_coordinator._execute_standard_api_search = AsyncMock(side_effect=mock_search)

        results = await mock_coordinator.fetch_all_api_results(
            artist_norm="hans zimmer",
            album_norm="inception original soundtrack",
            artist_region=None,
            log_artist="Hans Zimmer",
            log_album="Inception (Original Soundtrack)",
        )

        assert len(results) == 1
        assert call_count == 2  # Called twice: standard + fallback
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_alternative_search.py -v`
Expected: FAIL (fallback not implemented)

**Step 3: Modify fetch_all_api_results with fallback**

In `src/services/api/year_search_coordinator.py`, modify `fetch_all_api_results`:

```python
# Add import at top of file
from core.models.search_strategy import SearchStrategy, detect_search_strategy

# Replace fetch_all_api_results method (lines 91-113):
async def fetch_all_api_results(
    self,
    artist_norm: str,
    album_norm: str,
    artist_region: str | None,
    log_artist: str,
    log_album: str,
) -> list[ScoredRelease]:
    """Fetch scored releases from all API providers with script-aware logic."""
    self._log_api_search_start(artist_norm, album_norm, artist_region, log_artist, log_album)

    # Try script-optimized search first
    artist_script = detect_primary_script(log_artist)
    album_script = detect_primary_script(log_album)
    primary_script = artist_script if artist_script != ScriptType.UNKNOWN else album_script

    if primary_script not in (ScriptType.LATIN, ScriptType.UNKNOWN):
        script_results = await self._try_script_optimized_search(primary_script, artist_norm, album_norm, artist_region)
        if script_results:
            return script_results

    # Standard API search (all providers concurrently)
    results = await self._execute_standard_api_search(artist_norm, album_norm, artist_region, log_artist, log_album)

    if results:
        return results

    # Fallback: try alternative search strategy
    return await self._try_alternative_search(
        artist_norm, album_norm, artist_region, log_artist, log_album
    )
```

**Step 4: Add _try_alternative_search method**

Add after `_execute_standard_api_search` method:

```python
async def _try_alternative_search(
    self,
    artist_norm: str,
    album_norm: str,
    artist_region: str | None,
    log_artist: str,
    log_album: str,
) -> list[ScoredRelease]:
    """Try alternative search strategy when standard search fails.

    Detects album type and modifies query accordingly:
    - Soundtrack: Search by movie name
    - Various Artists: Search by album only
    - Bracket content: Strip unusual brackets
    """
    strategy_info = detect_search_strategy(log_artist, log_album, self.config)

    if strategy_info.strategy == SearchStrategy.NORMAL:
        return []  # No alternative available

    # Build alternative query
    alt_artist = strategy_info.modified_artist
    alt_album = strategy_info.modified_album

    # Normalize the alternative values
    if alt_artist:
        alt_artist_norm = alt_artist.lower().strip()
    else:
        alt_artist_norm = ""

    if alt_album:
        alt_album_norm = alt_album.lower().strip()
    else:
        alt_album_norm = album_norm

    self.console_logger.info(
        "Alternative search: %s - %s -> strategy=%s, query=(%s, %s)",
        log_artist,
        log_album,
        strategy_info.strategy.value,
        alt_artist or "(none)",
        alt_album or log_album,
    )

    # Execute alternative search
    return await self._execute_standard_api_search(
        alt_artist_norm,
        alt_album_norm,
        artist_region,
        alt_artist or log_artist,
        alt_album or log_album,
    )
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_alternative_search.py -v`
Expected: PASS

**Step 6: Type check**

Run: `uv run ty check src/services/api/year_search_coordinator.py --python .venv`
Expected: No errors

**Step 7: Commit**

```bash
git add src/services/api/year_search_coordinator.py tests/integration/test_alternative_search.py
git commit -m "feat(search): add alternative search fallback to YearSearchCoordinator"
```

---

## Task 5: Add Edge Case Tests

**Files:**
- Modify: `tests/unit/core/models/test_search_strategy.py`

**Step 1: Add edge case tests**

Add to `tests/unit/core/models/test_search_strategy.py`:

```python
class TestEdgeCases:
    """Tests for edge cases and Unicode handling."""

    @pytest.fixture
    def config(self) -> dict:
        return {
            "album_type_detection": {
                "soundtrack_patterns": ["soundtrack", "OST"],
                "various_artists_names": ["Various Artists", "Різні виконавці"],
            }
        }

    def test_unicode_various_artists(self, config: dict) -> None:
        """Ukrainian Various Artists should be detected."""
        info = detect_search_strategy("Різні виконавці", "Ukrainian Hits", config)
        assert info.strategy == SearchStrategy.VARIOUS_ARTISTS

    def test_case_insensitive_patterns(self, config: dict) -> None:
        """Pattern matching should be case insensitive."""
        info = detect_search_strategy("Artist", "Album SOUNDTRACK", config)
        assert info.strategy == SearchStrategy.SOUNDTRACK

    def test_whitespace_handling(self, config: dict) -> None:
        """Whitespace should be handled gracefully."""
        info = detect_search_strategy("  Various Artists  ", "Album", config)
        assert info.strategy == SearchStrategy.VARIOUS_ARTISTS

    def test_detection_priority(self, config: dict) -> None:
        """Soundtrack takes priority over Various Artists."""
        # This is "Various Artists" with soundtrack in album name
        info = detect_search_strategy(
            "Various Artists", "Movie Soundtrack", config
        )
        # Soundtrack should win (first in priority order)
        assert info.strategy == SearchStrategy.SOUNDTRACK
```

**Step 2: Run all tests**

Run: `uv run pytest tests/unit/core/models/test_search_strategy.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/unit/core/models/test_search_strategy.py
git commit -m "test(search): add edge case tests for search strategy detection"
```

---

## Task 6: Run Full Test Suite

**Step 1: Run all unit tests**

Run: `uv run pytest tests/unit/ -v`
Expected: PASS

**Step 2: Run integration tests**

Run: `uv run pytest tests/integration/ -v --ignore=tests/integration/test_real_api.py`
Expected: PASS

**Step 3: Run type check on all source**

Run: `uv run ty check src/ --python .venv`
Expected: No errors

**Step 4: Run pre-commit**

Run: `uv run pre-commit run --all-files`
Expected: PASS

**Step 5: Final commit if any fixes**

```bash
git add -A
git commit -m "fix: address any linting/type issues"
```

---

## Task 7: Push and Create PR

**Step 1: Push to remote**

```bash
git push origin dev
```

**Step 2: Comment on issue**

```bash
/opt/homebrew/bin/gh issue comment 108 --body "Implemented alternative search strategies:

- Added \`SearchStrategy\` enum with 5 strategies (NORMAL, SOUNDTRACK, VARIOUS_ARTISTS, STRIP_BRACKETS, GREATEST_HITS)
- Added \`detect_search_strategy()\` function for pattern-based detection
- Modified \`YearSearchCoordinator.fetch_all_api_results()\` with fallback mechanism
- Added config patterns for soundtracks and Various Artists

Commits on dev branch. Ready for testing."
```

---

## Summary

| Task | Files | Commits |
|------|-------|---------|
| 1 | config.yaml | `feat(config): add soundtrack and various artists patterns` |
| 2 | search_strategy.py, test_search_strategy.py | `feat(search): add SearchStrategy enum` |
| 3 | search_strategy.py, test_search_strategy.py | `feat(search): implement detect_search_strategy` |
| 4 | year_search_coordinator.py, test_alternative_search.py | `feat(search): add alternative search fallback` |
| 5 | test_search_strategy.py | `test(search): add edge case tests` |
| 6 | - | Run test suite |
| 7 | - | Push and comment on issue |
