# Design: Decompose God Classes (#218)

## Context

Three modules exceed reasonable size limits and handle too many responsibilities.
Five additional large files were analyzed; three (logger.py, year_scoring.py, request_executor.py)
have high cohesion and are excluded from this refactoring.

## Scope

| File | Lines | Clusters | Severity |
|------|-------|----------|----------|
| `services/api/orchestrator.py` | 1391 | 10 | High |
| `core/models/metadata_utils.py` | 803 | 6 | High |
| `app/music_updater.py` | 850 | 9 | Medium-High |
| `services/pending_verification.py` | 950 | 4 | Medium-High |
| `core/tracks/year_batch.py` | 1064 | 3 | Medium |

Excluded (high cohesion, low decomposition need):
- `core/logger.py` (1131 lines, 7 cohesive classes)
- `services/api/year_scoring.py` (1008 lines, single scoring algorithm)
- `services/api/request_executor.py` (782 lines, clean HTTP execution)

## Strategy

Incremental extract: one PR per decomposition target, ordered simple-to-complex.
Each PR is self-contained and testable independently.

## Quality Gates (mandatory per PR)

Every PR MUST actively apply these skills during implementation:
- **`strict-typing`** — full annotations on all new functions, concrete types, no bare `dict`/`list`
- **`docstring-conventions`** — Google-style, tiered by visibility, never repeat signature types
- **`error-design`** — action-based categories, context at module boundaries, `from exc` chains
- **`variable-naming`** — no shadowing, no abbreviations, verb_noun methods, descriptive names

## PR 1: metadata_utils.py -> 4 modules

### Current state
803 lines, 26 functions, 6 responsibility clusters, 15 importing modules.

### Decomposition

| New file | Contents | ~Lines |
|----------|----------|--------|
| `core/models/applescript_parser.py` | `AppleScriptFieldIndex`, `parse_tracks`, `_extract_optional_field`, `_validate_year_field`, `_create_track_from_fields` | 160 |
| `core/models/track_grouping.py` | `group_tracks_by_artist`, `determine_dominant_genre_for_artist` + 6 private helpers (date parsing, earliest track logic) | 170 |
| `core/models/name_cleaning.py` | `clean_names`, `remove_parentheses_with_keywords`, `reset_cleaning_exceptions_log` + 8 private helpers (parentheses, brackets, suffixes) | 300 |
| `services/apple/process_detection.py` | `is_music_app_running`, `_check_osascript_availability` | 70 |

### Migration

`metadata_utils.py` becomes a re-export barrel for backward compatibility:

```python
from core.models.applescript_parser import parse_tracks, AppleScriptFieldIndex
from core.models.track_grouping import group_tracks_by_artist, determine_dominant_genre_for_artist
from core.models.name_cleaning import clean_names, remove_parentheses_with_keywords, reset_cleaning_exceptions_log
from services.apple.process_detection import is_music_app_running
```

15 consumer files continue working without changes.

### Risk: LOW
No external API changes. Pure file reorganization with re-exports.

## PR 2: pending_verification.py -> extract CSV repository

### Current state
950 lines, 20+ methods. Mixes in-memory cache, CSV persistence, and reporting.

### Decomposition

| Component | Contents | ~Lines |
|-----------|----------|--------|
| `services/pending_verification_repository.py` **NEW** | `_blocking_load`, `_blocking_save`, `read_csv_data`, `process_csv_row`, CSV fieldnames, file locking | 200 |
| `services/pending_verification_report.py` **NEW** | `generate_problematic_albums_report` + formatting helpers | 100 |
| `services/pending_verification.py` **TRIMMED** | In-memory cache: `mark_for_verification`, `is_verification_needed`, `remove_from_pending`, query methods | 650 |

### Architecture
```
PendingVerificationService
  +-- uses --> PendingVerificationRepository (CSV read/write)
  +-- uses --> PendingVerificationReporter (report generation)
```

Repository injected via constructor (DI through DependencyContainer).

### Risk: MEDIUM
DependencyContainer needs update. Public API of PendingVerificationService unchanged.

## PR 3: year_batch.py -> extract prerelease + update logic

### Current state
1064 lines, 30+ methods. Single large batch processor.

### Decomposition

| Component | Contents | ~Lines |
|-----------|----------|--------|
| `core/tracks/prerelease_handler.py` **NEW** | Prerelease detection, future year stats computation, prerelease album handling | 120 |
| `core/tracks/track_update_batcher.py` **NEW** | Track collection, validation, bulk async updates with retry | 200 |
| `core/tracks/year_batch.py` **TRIMMED** | Batch orchestration: `process_albums_in_batches`, sequential/concurrent strategies, album filtering | 750 |

### Risk: MEDIUM
Internal refactoring within core/tracks/. No public API changes.

## PR 4: MusicUpdater -> extract pipeline runner

### Current state
850 lines, 24 methods, 9 responsibility clusters. Facade over 10+ services.

### Decomposition

| Component | Contents | ~Lines |
|-----------|----------|--------|
| `app/pipeline_runner.py` **NEW** | `run_main_pipeline`, `_fetch_tracks_for_pipeline_mode`, `_try_smart_delta_fetch`, `_compute_incremental_scope`, `_get_last_run_time`, `_update_all_genres`, `_update_all_years_with_logs`, `_save_pipeline_results` | 350 |
| `app/cache_invalidation.py` **NEW** | `_emit_removed_track_events`, `_emit_identity_change_events` | 60 |
| `app/music_updater.py` **TRIMMED** | Facade: `__init__`, `set_dry_run_context`, delegates to PipelineRunner + individual commands | 440 |

### Architecture
```
MusicUpdater (facade)
  +-- delegates --> PipelineRunner (main pipeline orchestration)
  +-- delegates --> CacheInvalidator (track removal/rename events)
  +-- directly handles --> run_update_years, run_verify_pending, etc. (thin pass-through)
```

### Risk: MEDIUM-HIGH
Orchestrator imports MusicUpdater. DependencyContainer update needed.

## PR 5: ExternalApiOrchestrator -> extract 4 classes

### Current state
1391 lines, ~45 methods, 10 responsibility clusters. Already had 2 extractions
(YearSearchCoordinator, YearScoreResolver).

### Decomposition

| Component | Contents | ~Lines |
|-----------|----------|--------|
| `services/api/token_manager.py` **NEW** | `_load_secure_token`, `_get_raw_token`, `_process_token_security`, `_decrypt_token`, `_encrypt_token_for_future_storage` | 100 |
| `services/api/verification_bridge.py` **NEW** | `_safe_mark_for_verification`, `_safe_remove_from_pending`, `_get_attempt_count` + fire-and-forget task management | 120 |
| `services/api/prerelease_detector.py` **NEW** | `_count_prerelease_tracks`, `_compute_future_year_stats`, `_is_prerelease_album`, `_handle_prerelease_album`, `_log_future_year_within_threshold` | 100 |
| `services/api/year_determination.py` **NEW** | `get_album_year`, `_initialize_year_search`, `_setup_artist_context`, `_fetch_all_api_results`, `_handle_no_results`, `_process_api_results`, contamination detection | 400 |
| `services/api/orchestrator.py` **TRIMMED** | Thin facade: `initialize`, `close`, config extraction, API client init + delegation | 500 |

### Architecture
```
ExternalApiOrchestrator (facade)
  +-- uses --> TokenManager (API key encryption/decryption)
  +-- uses --> VerificationBridge (pending verification queue)
  +-- uses --> PrereleaseDetector (prerelease album detection)
  +-- uses --> YearDeterminationService (main year-finding logic)
  +-- uses --> YearSearchCoordinator (already extracted)
  +-- uses --> YearScoreResolver (already extracted)
```

### Risk: HIGH
Most complex extraction. DependencyContainer update required.
Public API (get_album_year, initialize, close) unchanged.

## Totals

| Metric | Before | After |
|--------|--------|-------|
| God class lines | 5058 | ~2340 |
| New files | 0 | 14 |
| PRs | — | 5 |
| Public API changes | — | 0 |

## Verification per PR

1. `uv run ruff check src/ tests/` — 0 violations
2. `uv run pytest` — all tests pass
3. `prek run --all-files` — all checks pass
4. No new `# type: ignore` or `# noqa` suppressions
