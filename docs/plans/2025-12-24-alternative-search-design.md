# Alternative Search Strategies for Special Albums

**Issue**: #108
**Date**: 2025-12-24
**Status**: Approved

## Problem

Albums that fail API matching due to non-standard metadata:
- **Soundtracks**: credited to composer, not movie name
- **Label compilations**: "Various Artists" as artist
- **Greatest Hits**: compilation naming patterns
- **Special editions**: unique brackets like `[MESSAGE FROM THE CLERGY]`

## Goal

Find correct release year through alternative search strategies (not skip).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  YearSearchCoordinator                       │
├─────────────────────────────────────────────────────────────┤
│  search_all_apis(artist, album)                             │
│    │                                                         │
│    ├─► 1. Standard search ──► results? ──► return           │
│    │                              │                          │
│    │                              ▼ (empty)                  │
│    ├─► 2. detect_search_strategy(artist, album, config)     │
│    │         │                                               │
│    │         ▼                                               │
│    │    SearchStrategyInfo                                   │
│    │    ├── strategy: SearchStrategy                         │
│    │    ├── detected_pattern: str | None                     │
│    │    ├── modified_artist: str | None                      │
│    │    └── modified_album: str | None                       │
│    │                                                         │
│    ├─► 3. Build alternative query                           │
│    │                                                         │
│    └─► 4. Alternative search ──► return                      │
└─────────────────────────────────────────────────────────────┘
```

## Components

### SearchStrategy Enum

New file: `src/core/models/search_strategy.py`

```python
class SearchStrategy(Enum):
    NORMAL = "normal"           # No modification needed
    SOUNDTRACK = "soundtrack"   # Extract movie name from album
    VARIOUS_ARTISTS = "various" # Search by album only
    STRIP_BRACKETS = "strip"    # Remove [SPECIAL TEXT] from album
    GREATEST_HITS = "hits"      # Try artist + "Greatest Hits"

@dataclass
class SearchStrategyInfo:
    strategy: SearchStrategy
    detected_pattern: str | None = None
    modified_artist: str | None = None
    modified_album: str | None = None
```

### Detection Function

```python
def detect_search_strategy(
    artist: str,
    album: str,
    config: dict[str, Any],
) -> SearchStrategyInfo:
    """Detect which alternative search strategy to use.

    Separate from detect_album_type() - different concerns:
    - album_type: How to HANDLE year once found
    - search_strategy: How to FIND year in first place
    """
```

Detection order (first match wins):
1. Soundtrack patterns in album (`OST`, `Soundtrack`, `Score`)
2. Various Artists check (exact artist match)
3. Bracket patterns (`[...]` with unusual content)
4. Greatest Hits patterns (`Best of`, `Greatest`, etc.)
5. Default: NORMAL

### Config Patterns

Uses existing `album_type_detection` section in config:

```yaml
album_type_detection:
  soundtrack_patterns:
    - "soundtrack"
    - "original score"
    - "OST"
    - "motion picture"
    - "film score"

  various_artists_names:
    - "Various Artists"
    - "Various"
    - "VA"
    - "Різні виконавці"
```

## Alternative Query Building

| Strategy | Artist Modification | Album Modification |
|----------|--------------------|--------------------|
| SOUNDTRACK | Extract movie name from album | Remove "OST/Soundtrack" suffix |
| VARIOUS_ARTISTS | `None` (search album only) | Keep original |
| STRIP_BRACKETS | Keep original | Remove `[...]` content |
| GREATEST_HITS | Keep original | Try "Greatest Hits" |

## Edge Cases

### Detection Priority

Strict order prevents conflicts:
```python
if _is_soundtrack(album, config):
    return SearchStrategyInfo(strategy=SearchStrategy.SOUNDTRACK, ...)
elif _is_various_artists(artist, config):
    return SearchStrategyInfo(strategy=SearchStrategy.VARIOUS_ARTISTS, ...)
# etc.
```

### No Infinite Loops

Alternative search runs only once:
```python
async def search_all_apis(self, artist: str, album: str) -> list[ScoredRelease]:
    results = await self._search_all_apis_internal(artist, album)
    if results:
        return results

    strategy_info = detect_search_strategy(artist, album, self.config)
    if strategy_info.strategy == SearchStrategy.NORMAL:
        return []  # No alternative available

    alt_artist, alt_album = self._build_alternative_query(strategy_info)
    return await self._search_all_apis_internal(alt_artist, alt_album)
    # ↑ No further recursion
```

### Logging

Every decision logged for debugging:
```python
self.logger.info(
    "Alternative search: %s -> strategy=%s, query=(%s, %s)",
    f"{artist} - {album}",
    strategy_info.strategy.value,
    alt_artist,
    alt_album,
)
```

## Testing Strategy

### Unit Tests

`tests/unit/core/models/test_search_strategy.py`:
- Pattern detection for each strategy type
- Edge cases (empty strings, Unicode)
- Config-driven pattern matching

### Integration Tests

`tests/integration/test_alternative_search.py`:
- Fallback triggered on empty results
- No fallback when results exist
- Correct query transformation

### Test Fixtures

`tests/fixtures/alternative_search_cases.yaml`:
```yaml
soundtracks:
  - artist: "Hans Zimmer"
    album: "Inception (Original Soundtrack)"
    expected_strategy: soundtrack
    expected_search_artist: "Inception"

special_brackets:
  - artist: "Ghost"
    album: "Prequelle [MESSAGE FROM THE CLERGY]"
    expected_strategy: strip_brackets
    expected_search_album: "Prequelle"
```

## Files to Create/Modify

| File | Action |
|------|--------|
| `src/core/models/search_strategy.py` | **Create** - Enum + dataclass + detect function |
| `src/services/api/year_search_coordinator.py` | **Modify** - Add fallback logic |
| `config.yaml` | **Modify** - Add soundtrack/VA patterns |
| `tests/unit/core/models/test_search_strategy.py` | **Create** |
| `tests/integration/test_alternative_search.py` | **Create** |

## Implementation Order

1. Add config patterns (soundtrack, various artists)
2. Create `SearchStrategy` enum and `SearchStrategyInfo` dataclass
3. Implement `detect_search_strategy()` function
4. Add `_build_alternative_query()` to coordinator
5. Modify `search_all_apis()` with fallback logic
6. Write unit tests
7. Write integration tests
