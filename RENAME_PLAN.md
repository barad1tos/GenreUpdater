# File Rename Execution Plan

## Summary
- **Total files to rename**: 35
- **Estimated import updates**: ~100+
- **Approach**: Batch by impact level (imports count)

---

## Batch 1: Low-Impact (1 import each) — 12 files

Safe to start. Each file imported by only 1 other file.

| # | Current Path | New Name | Imports to Update |
|---|--------------|----------|-------------------|
| 1 | `core/tracks/base.py` | `track_base.py` | 1 |
| 2 | `core/tracks/filter.py` | `incremental_filter.py` | 1 |
| 3 | `core/tracks/genre.py` | `genre_manager.py` | 1 |
| 4 | `core/retry.py` | `retry_handler.py` | 1 |
| 5 | `services/apple/client.py` | `applescript_client.py` | 1 |
| 6 | `services/apple/executor.py` | `applescript_executor.py` | 1 |
| 7 | `services/cache/album.py` | `album_cache.py` | 1 |
| 8 | `services/cache/api.py` | `api_cache.py` | 1 |
| 9 | `services/cache/generic.py` | `generic_cache.py` | 1 |
| 10 | `services/pending.py` | `pending_verification.py` | 1 |
| 11 | `app/features/verify/database.py` | `database_verifier.py` | 1 |
| 12 | `app/features/batch/processor.py` | `batch_processor.py` | 1 |

**Commands for Batch 1:**
```bash
# Each rename: git mv + update imports
git mv src/core/tracks/base.py src/core/tracks/track_base.py
# Then update: from src.core.tracks.base → from src.core.tracks.track_base
```

---

## Batch 2: Medium-Impact (2-4 imports) — 9 files

| # | Current Path | New Name | Imports |
|---|--------------|----------|---------|
| 13 | `core/tracks/artist.py` | `artist_renamer.py` | 2 |
| 14 | `core/tracks/year.py` | `year_retriever.py` | 3 |
| 15 | `core/tracks/delta.py` | `track_delta.py` | 3 |
| 16 | `services/api/scoring.py` | `year_scoring.py` | 2 |
| 17 | `core/models/status.py` | `track_status.py` | 4 |
| 18 | `core/debug.py` | `debug_utils.py` | 4 |
| 19 | `services/cache/config.py` | `cache_config.py` | 4 |
| 20 | `services/deps.py` | `dependency_container.py` | 4 |
| 21 | `app/updater.py` | `music_updater.py` | 3 |

---

## Batch 3: High-Impact (5-8 imports) — 6 files

| # | Current Path | New Name | Imports |
|---|--------------|----------|---------|
| 22 | `core/config.py` | `core_config.py` | 5 |
| 23 | `services/cache/hash.py` | `hash_service.py` | 5 |
| 24 | `services/api/base.py` | `api_base.py` | 5 |
| 25 | `core/tracks/processor.py` | `track_processor.py` | 6 |
| 26 | `metrics/reports.py` | `change_reports.py` | 6 |
| 27 | `core/models/metadata.py` | `metadata_utils.py` | 8 |

---

## Batch 4: Critical (28 imports) — 1 file

⚠️ **HIGHEST RISK** - requires updating 28 import statements

| # | Current Path | New Name | Imports |
|---|--------------|----------|---------|
| 28 | `core/models/track.py` | `track_models.py` | 28 |

---

## Batch 5: Additional Files (0 direct imports, but referenced)

These files have no direct imports found but should be renamed for consistency:

| # | Current Path | New Name | Reason |
|---|--------------|----------|--------|
| 29 | `app/helpers.py` | `pipeline_helpers.py` | Clarify purpose |
| 30 | `app/config.py` | `app_config.py` | Resolve conflict |
| 31 | `metrics/errors.py` | `error_reports.py` | Clarify purpose |
| 32 | `core/models/repair.py` | `year_repair.py` | Domain context |

---

## Execution Strategy

### For Each File:

```bash
# 1. Rename file with git
git mv src/OLD_PATH.py src/NEW_PATH.py

# 2. Update all imports (sed or manual)
# Find files that import:
grep -rl "from src.OLD_MODULE import" src/ --include="*.py"

# Replace imports:
sed -i '' 's/from src.OLD_MODULE import/from src.NEW_MODULE import/g' FILE

# 3. Update __init__.py if module is re-exported

# 4. Run tests to verify
uv run pytest tests/unit/app/ -v --tb=short

# 5. Commit batch
git commit -m "refactor: rename X files in batch N"
```

### Verification After Each Batch:

```bash
# Type check
uv run mypy src/ --no-error-summary

# Lint
uv run ruff check src/

# Tests
uv run pytest tests/ -x --tb=short
```

---

## Risk Mitigation

1. **Create backup branch before starting**
   ```bash
   git checkout -b backup/before-file-renames
   git checkout refactor/directory-rename
   ```

2. **Rename in small batches** — commit after each batch

3. **Use IDE refactoring** if available (PyCharm, VS Code) for safer renames

4. **Most risky: track.py** (28 imports)
   - Consider using `git grep` to find all occurrences
   - Test thoroughly after this rename

---

## Estimated Time

| Batch | Files | Est. Time |
|-------|-------|-----------|
| 1 | 12 | 15 min |
| 2 | 9 | 15 min |
| 3 | 6 | 15 min |
| 4 | 1 | 10 min |
| 5 | 4 | 5 min |
| **Total** | **32** | **~60 min** |

---

## Files NOT Being Renamed (Acceptable)

These follow standard patterns or are already descriptive:

- `__init__.py` files (standard)
- `exceptions.py` (standard pattern)
- `protocols.py`, `validators.py`, `types.py` (standard)
- `discogs.py`, `lastfm.py`, `musicbrainz.py`, `applemusic.py` (provider names)
- `track_cleaning.py`, `year_update.py`, `pipeline_snapshot.py` (already good)
- `year_consistency.py`, `year_fallback.py`, `update_executor.py` (descriptive)
- `batch_fetcher.py`, `cache_manager.py` (descriptive)
- `fingerprint.py`, `snapshot.py`, `orchestrator.py` (clear in context)
