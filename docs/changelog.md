# Changelog

All notable changes to Music Genre Updater.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Flaky `test_get_activity_period_classic_band` — skip gracefully when MusicBrainz returns `(None, None)` due to rate limiting
- Ruff format: missing blank line in `track_models.py` after Sourcery walrus operator refactor

### Changed
- **Config type safety (C3)**: removed `_resolve_config_dict()` bridge from `logger.py`; changed all 12 logger function signatures from `AppConfig | dict[str, Any]` to `AppConfig`; replaced dict `.get()` lookups with typed `config.logging.*` attribute access; moved `AppConfig` import to `TYPE_CHECKING`; added `LogLevelsConfig.normalize_log_level` validator for case-insensitive log level names; migrated 5 test files from dict configs to `create_test_app_config()` factory; removed last `model_dump()` round-trip in `validate_api_auth` — now accepts `ApiAuthConfig` directly
- **Config type safety (C2)**: removed `DependencyContainer.config` dict property and `Config._config` dict storage — all config access now goes through typed `AppConfig`; migrated `MusicUpdater` from `deps.config` dict to `deps.app_config`; removed dict branches from `search_strategy`, `album_type`, `html_reports`; deleted 155 lines of dead accessor methods (`.get()`, `.get_path()`, `.get_list()`, `.get_dict()`, `.get_bool()`, `.get_int()`, `.get_float()`) from `Config` class; migrated 12 test files from dict configs to `create_test_app_config()` factory
- **Config type safety (C1)**: migrated `ReleaseScorer` from `dict[str, Any]` to typed `ScoringConfig` Pydantic model; replaced 33 `.get()` calls with typed attribute access; added 3 missing scoring fields (`artist_substring_penalty`, `artist_mismatch_penalty`, `current_year_penalty`); changed all scoring fields from `float` to `int` to match config.yaml and downstream usage; removed dead `ScoringConfig` TypedDict and `_get_default_scoring_config()`; updated orchestrator to pass `ScoringConfig` object directly instead of `model_dump()` dict

### Added
- Batch error handling tests: sequential processing, CancelledError, config validation
- Track ID validation and bulk update mixed-results tests
- Retry exhaustion behavior tests
- API client boundary security tests (MusicBrainz, Discogs, iTunes)
- Hash service adversarial input and collision resistance tests
- CLI argument security boundary tests
- Property-based tests for validators (Hypothesis): year validation, string sanitization
- Property-based tests for hash service (Hypothesis): determinism, format invariants, collision resistance

### Changed
- **Config type safety (B4)**: migrated `Analytics`, `PendingVerificationService`, `DatabaseVerifier`, `GenreManager` from `dict[str, Any]` to typed `AppConfig`; added missing `AnalyticsConfig` fields; fixed `definitive_score_threshold/diff` model types (`float` → `int`); added `model_validator` to migrate legacy top-level `test_artists` into `development.test_artists`; fixed `max_events=0` being silently overridden (falsy `or` → `is None` check)
- **Config type safety (B3)**: migrated core year pipeline (`ExternalApiOrchestrator`, `YearRetriever`, `YearBatchProcessor`, `TrackUpdateExecutor`, `YearDetermination`, `TrackProcessor`) from `dict[str, Any]` to typed `AppConfig`; removed dead dict-validation methods replaced by Pydantic
- **Config type safety (B2)**: migrated `LibrarySnapshotService` and `AppleScriptClient` from `dict[str, Any]` to typed `AppConfig`; loc-based validation assertions per Sourcery
- **Config type safety (B1)**: migrated 24 services from `dict[str, Any]` to typed `AppConfig` for config access (cache, tracks, API, orchestrator modules)
- Removed dead coercion helpers (`_coerce_*`, `_resolve_*`) and unreachable try/except after AppConfig migration
- Removed dead temp-file execution infrastructure from AppleScript executor (superseded by bulk verification)
- Added `from __future__ import annotations` to 33 source files, moved type-only imports to `TYPE_CHECKING` blocks
- Test fixture deduplication: shared logger fixtures in root conftest
- Unified track factory and mock fixtures in tracks conftest
- Migrated year_batch test files to shared fixtures (-409/+208 lines)
- Fixed TC001 lint: moved type-only imports to TYPE_CHECKING blocks
- Consolidated year_determinator mock into shared `create_year_determinator_mock()` helper
- Pinned hypothesis==6.151.4 for reproducible test runs
- Applied ruff format to all new test files

### Fixed
- `AlbumTypeDetectionConfig` pattern fields now use `None` vs `[]` semantics (`None` = defaults, `[]` = disabled)
- Dependabot PRs failing CI due to missing env vars in `load_config()` validation
- `DiscogsClient` received empty dict instead of typed `AppConfig`/`YearRetrievalConfig` — latent runtime crash on `_get_reissue_keywords()`
- Test cast mismatch: `cast(Analytics, ...)` → `cast(AnalyticsProtocol, ...)` to match `GenreManager` signature
- CI failures since B1: `full_sync.py` ruff format violation; `test_external_api_real.py` fixtures returning `dict` instead of `AppConfig`
- Legacy top-level `test_artists` now emits `DeprecationWarning` when migrated or silently ignored

## [3.0.0] - 2026-01-12

### Added
- MkDocs documentation with mkdocstrings
- Full API reference documentation
- Architecture documentation with Mermaid diagrams

### Fixed
- Path expansion bug when `HOME` environment variable is not set
- Config loader now uses `Path.expanduser()` for robust home directory resolution

## [2.0.0] - 2025-01-09

### Added
- **Year Retrieval System**: Automatic album year fetching from MusicBrainz, Discogs, and iTunes
- **Scoring System**: Confidence-based year selection with reissue detection
- **Library Snapshots**: Fast startup with compressed track cache
- **Incremental Updates**: Process only recently modified tracks
- **LaunchAgent Support**: Background daemon operation
- **Pending Verification**: Queue for manual verification of uncertain years
- **Restore Command**: Fix albums with wrong reissue years

### Changed
- Complete async/await rewrite for better performance
- Pydantic v2 for all data validation
- Python 3.13 minimum requirement
- Protocol-based dependency injection

### Fixed
- Rate limiting for all external APIs
- Memory management for large libraries (30K+ tracks)
- AppleScript timeout handling

## [1.0.0] - 2024-06-15

### Added
- Initial release
- Genre calculation based on artist's track library
- AppleScript integration with Music.app
- Basic CLI interface
- CSV export of track data

---

## Migration Guide

### From 1.x to 2.0

1. **Python Version**: Upgrade to Python 3.13+

2. **Configuration**: New YAML structure required
   ```yaml
   # Old (1.x)
   api_key: xxx

   # New (2.0)
   year_retrieval:
     api_auth:
       discogs_token: ${DISCOGS_TOKEN}
   ```

3. **Environment Variables**: Now required
   - `DISCOGS_TOKEN`
   - `CONTACT_EMAIL`

4. **Cache Location**: Clear old cache
   ```bash
   rm -rf cache/
   ```

5. **Commands**: Some renamed
   - `update` → `update_genres`
   - New: `update_years`, `restore_release_years`
