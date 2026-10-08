# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Daemon: runs the pipeline with `uv run --frozen --no-dev`, so a run no longer reinstalls the development packages that its own `uv sync --frozen --no-dev` removed
- Dependabot updates Python dependencies through the uv ecosystem, so `uv.lock` moves together with `pyproject.toml`, and CI installs with `uv sync --locked`, so a lock that drifts from `pyproject.toml` turns CI red instead of being relocked silently
- ty type-checks the whole repository, including the test suite, `scripts/`, `tools/` and `docs/`: CI and the pre-commit hook run `uv run ty check`, and the hook runs on every Python or stub change instead of only changes under `src/`
- CI runs pydoclint over all of `src/`, whereas the pre-commit hook checks only staged files, so a pydoclint update that flags existing docstrings fails its own PR into `main` instead of merging green (as #485 did)
- The documentation site and `CHANGELOG.md` kept two different changelogs; the site now shows `CHANGELOG.md`, which holds the entries of both, each under the release that first shipped it

### Removed

- basedpyright and its `[tool.pyright]` configuration; ty is the only type checker, so Pylance and basedpyright fall back to their own defaults, and ty's language server is how an editor shows CI's diagnostics
- `DatabaseRetryHandler.async_retry_operation`, with `RetryMetadata` and the `RetryOperationContext` fields only its callers could read: it could not retry, because a context manager cannot re-run its `async with` block, so the first transient error raised `RuntimeError`, and only its own tests called it; `execute_with_retry` remains the retry API

### Fixed

- A task canceled on its own while others ran beside it was mishandled: a track year update counted as done; an album's year processing, a MusicBrainz release fetch, a cache initialization or a cache save vanished without a log; and a genre update broke that artist's genre pass. Each now counts as a failure and is logged with its exception type, and a failed track update also logs its traceback
- When some of an album's track year updates failed, the failed tracks were still recorded in the change log as updated to the new year; only the tracks that were updated are recorded now
- A cache that failed to save at shutdown was followed by "All caches saved to disk"; shutdown now warns that the cache was not saved and names it
- A year lookup that failed with an error was logged only with year debugging on, so the album looked like one no API knows; the error log now records the failure with its traceback, and the console says the lookup failed
- A year provider that failed while searching a non-Latin name was logged only with API debugging on, and a failed provider or artist lookup reached the error log without its traceback; all of them are now logged with it
- An album whose year processing failed with an unexpected error, or a year update that stopped on an error, showed up only in the error log; the console now says which album failed or that the update stopped
- A retried AppleScript operation whose next backoff would end past its total timeout waited out the backoff before timing out; it now times out at once, and the timeout error and its log line name the failure that led to it. An `applescript_retry.operation_timeout_seconds` of 0, which stopped every operation before its first attempt, is now rejected when the config loads
- A year provider request that failed (a timeout, a rate limit, a server error) was cached as an answer with no data for `year_retrieval.processing.cache_ttl_days`, which the shipped config sets to 36500 days, so the album never got that provider's data again; failed requests are no longer cached, and the ones earlier versions cached are retried on their next lookup (Discogs ones once their 30-day entries expire)
- Daemon: a run triggered right after boot or wake, before the network was up, failed with a "Git fetch failed" notification; `run-daemon.sh` now retries the fetch for about a minute and, if there is still no network, skips the run without a notification; with a network present, the failure notification now names the cause, and each fetch attempt is capped at 30 s so a stalled transfer can no longer hold the daemon lock indefinitely
- Daemon error notifications no longer vanish when the error text contains quotes or backslashes: `notify.sh` passes title, message and sound to `osascript` as arguments instead of splicing them into the AppleScript source

## [3.0.2] - 2026-10-03

### Removed

- `scripts/sync-diagnostics.sh`: no callers, and it pushed from the legacy daemon clone to the protected `main` branch

### Fixed

- Year-verification queue saves no longer fail with `No such file or directory` on `pending_year_verification.csv.tmp` when several albums are marked at once, and a stale queue snapshot can no longer overwrite a newer one
- Daemon: a run skipped because Music.app was not running exited successfully and showed an "Update completed successfully" notification; `main.py` now exits with `EX_TEMPFAIL` (75), and `run-daemon.sh` logs the skip without a notification
- Daemon: re-running `install.sh` left a development-checkout `apple_scripts_dir` in an existing config and did not restore missing state files
- A run that fetched no tracks from a library the last snapshot recorded as non-empty exited successfully; it now exits with an error, so the daemon reports the failure. A library that really became empty is recorded with `main.py --fresh`

### Security

- The Discogs token no longer appears in logs: startup no longer encrypts a plaintext token just to log the encrypted value, Discogs request headers hide the `Authorization` value, and debug config dumps replace the token with `<redacted>`

## [3.0.1] - 2026-10-03

### Changed

- Daemon: moved to an XDG layout (`~/.config/genreupdater`, `~/.local/share/genreupdater`, `~/.local/state/genreupdater`), separate from the Swift app's Application Support directory
- Daemon: deploys the latest stable release tag (`vX.Y.Z`) instead of `origin/main`; config and secrets live outside the clone

### Removed

- Daemon: `sync-fixtures.sh` (pushes to the protected `main` branch were always rejected)

### Fixed

- AppleScripts were rejected when the scripts directory sat below a hidden directory such as `~/.local/share`
- Daemon: `uv sync` failures in `run-daemon.sh` were treated as success

## [3.0.0] - 2026-10-03

### Added

- CodeQL security scanning workflow
- Integration & E2E tests in GitHub Actions (nightly)
- Dependabot for automated dependency updates
- CHANGELOG.md following Keep a Changelog format
- SECURITY.md with vulnerability reporting process
- CONTRIBUTING.md with development guidelines
- CODE_OF_CONDUCT.md (Contributor Covenant v2.1)
- Bandit security scanning in CI workflow
- Pull request template with checklist
- Issue templates (bug report, feature request)
- CODEOWNERS file for automatic reviewers
- Pre-commit hooks configuration (ruff, ty)
- Test coverage enforcement (--cov-fail-under=70)
- Tests for lazy `%` logging paths across discogs, musicbrainz, year_search_coordinator, analytics (#216)
- Tests for 39 uncovered patch lines: except-branches, UTC timezone, isinstance guards, file_validator (#241)
- Tests for remaining Codecov patch gaps: fingerprint except-handler, cache shutdown OSError, fetch_all_track_ids, _log_apple_scripts_dir OSError (#241)
- Swift fixture generator (`tools/generate_swift_fixtures.py`) — 91 test cases across 6 fixture files for Swift port parity testing
- 5 boundary test cases to Swift fixture generator: verification threshold, confidence threshold, definitive-with-existing, year-diff-one, CJK-Latin cross-script (total: 96 cases)
- 4 edge-case tests for new `except` branches: unparseable year in re-recording check, dominant year parse failure, future-year stats with non-integer years, reissue detection with invalid release year
- Missing-year branch coverage for `_compute_future_year_stats` (track without `year` key)
- Tests for 4 previously untested modules (#219): comprehensive coverage for `year_utils.py` (100%) and `track_base.py`, smoke tests for `json_utils.py` (93%) and `applescript_executor.py` (pure methods only)
- 13 scoring branch coverage tests (`TestScoringBranchCoverage`): EP/single penalty, bootleg/promo status, reissue, RG first date match, artist period penalties/bonuses, year diff, future/current year, invalid RG date
- Batch error handling tests: sequential processing, CancelledError, config validation
- Track ID validation and bulk update mixed-results tests
- Retry exhaustion behavior tests
- API client boundary security tests (MusicBrainz, Discogs, iTunes)
- Hash service adversarial input and collision resistance tests
- CLI argument security boundary tests
- Property-based tests for validators (Hypothesis): year validation, string sanitization
- Property-based tests for hash service (Hypothesis): determinism, format invariants, collision resistance
- MkDocs documentation with mkdocstrings
- Full API reference documentation
- Architecture documentation with Mermaid diagrams

### Changed

- Hardened type safety across test suite (17 files): typed MockCacheService, async protocol conformance, explicit annotations, `_ = param` discard pattern, factory-based config creation, centralized logger fixtures
- Narrowed 22 broad `except Exception` blocks to specific exception types across 12 files
- Replaced `contextlib.suppress` with explicit try/except + DEBUG logging (3 files)
- Replaced `Any` types with concrete types and Protocols (cli, discogs, rate_limiter, year_repair)
- Extracted magic numbers as named constants (applescript_client, executor, snapshot)
- Removed unused TypeDicts, TypeVar, and Protocol definitions (orchestrator, track_models, pending_verification)
- Upgraded GitHub Actions to latest versions (checkout v6, setup-uv v7, upload-artifact v5, codeql v4)
- README Python badge updated to 3.13+
- Converted 50 f-string logging calls to lazy `%` formatting for deferred evaluation (#216)
- Migrated `print()` calls to structured logger in full_sync post-initialization
- Removed ~44 lines of ASCII art separators (`# ===`, `# ---`) across 9 files per CLAUDE.md formatting rules
- Migrated 5 `print()` calls to `console_logger.info()` in `full_sync.py` (post-init phase where logger is available)
- **Decompose `year_batch.py` god class (#218)**: extracted `PrereleaseHandler` (prerelease detection/handling) and `TrackUpdater` (track-level year updates with retry) into focused modules; `YearBatchProcessor` reduced from 1,021 to ~530 LOC while preserving public API; updated test docstrings to reflect new module locations
- **Consolidate duplicate types (#212)**: removed dead `ScriptType` enum, `DiscogsRelease`, `DiscogsSearchResult` from `track_models.py`; renamed `EnhancedRateLimiter` → `ApiRateLimiter` (API) and `AppleScriptRateLimiter` (Apple); standardized `get_stats()` keys and `window_size` → `window_seconds` across both rate limiters; fixed Pyright type errors in integration tests
- **Config type safety (C3)**: removed `_resolve_config_dict()` bridge from `logger.py`; changed all 12 logger function signatures from `AppConfig | dict[str, Any]` to `AppConfig`; replaced dict `.get()` lookups with typed `config.logging.*` attribute access; moved `AppConfig` import to `TYPE_CHECKING`; added `LogLevelsConfig.normalize_log_level` validator for case-insensitive log level names; migrated 5 test files from dict configs to `create_test_app_config()` factory; removed last `model_dump()` round-trip in `validate_api_auth` — now accepts `ApiAuthConfig` directly
- **Config type safety (C2)**: removed `DependencyContainer.config` dict property and `Config._config` dict storage — all config access now goes through typed `AppConfig`; migrated `MusicUpdater` from `deps.config` dict to `deps.app_config`; removed dict branches from `search_strategy`, `album_type`, `html_reports`; deleted 155 lines of dead accessor methods (`.get()`, `.get_path()`, `.get_list()`, `.get_dict()`, `.get_bool()`, `.get_int()`, `.get_float()`) from `Config` class; migrated 12 test files from dict configs to `create_test_app_config()` factory
- **Config type safety (C1)**: migrated `ReleaseScorer` from `dict[str, Any]` to typed `ScoringConfig` Pydantic model; replaced 33 `.get()` calls with typed attribute access; added 3 missing scoring fields (`artist_substring_penalty`, `artist_mismatch_penalty`, `current_year_penalty`); changed all scoring fields from `float` to `int` to match config.yaml and downstream usage; removed dead `ScoringConfig` TypedDict and `_get_default_scoring_config()`; updated orchestrator to pass `ScoringConfig` object directly instead of `model_dump()` dict
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

### Removed

- Dead code cleanup (#220): `pipeline_helpers.py` module, unused `__init__.py` re-exports (3 packages), singleton ClassVars from DependencyContainer, `clear_dry_run_actions()` method

### Fixed

- `zip` misalignment in year_search_coordinator: filtered API tasks vs unfiltered api_order
- Naive `datetime.fromtimestamp()` calls missing timezone in analytics.py
- `logging.warning()` using root logger instead of module-level `_logger` in applescript_client
- `exception()` used without active exception in file_validator (caused misleading tracebacks)
- `str.replace(".csv", ...)` replaced with `Path.with_name()` in pending_verification
- Dead `allow_music_app` branch in sanitizer (no reserved words contained "music")
- ASCII art log separators replaced with structured single-line messages
- E2E test assertions for test_mode + dry_run scenarios
- Whitespace normalization in metadata cleaning comparisons
- `splitlines()` bug in `parse_tracks()` and `parse_osascript_output()` — Python treats `\x1e` (field separator) as a line boundary, breaking single-track AppleScript responses into per-field rows; now uses `split(LINE_SEPARATOR)` in ASCII mode
- Replaced 4 `contextlib.suppress` blocks with explicit `try/except` + warning logs in year processing and API modules — parse failures now leave audit trail instead of silently altering control flow
- Added meaningful assertions to 16 assertion-free unit/regression tests (#219): `test_analytics_core` (report dir + GC mock), `test_html_reports` (default loggers file check), `test_pipeline_snapshot` (early-return + skip guards), `test_track_processor_coverage` (skip-when-no-renamer), `test_request_executor` (response parsing), `test_year_retriever_coverage` (API call + prerelease skip), `test_track_sync` (date_added + save_csv mocks); converted 3 regression tests to `pytest.skip` informational pattern
- Removed 2 empty test stubs from integration tests (#219): `test_graceful_handling_of_provider_timeout` and `test_graceful_handling_of_rate_limit_response` (mock setup only, never invoked code under test); cleaned dead variables from 2 e2e pipeline smoke tests
- Removed dead test infrastructure (#219): empty monitoring test dir, unused `unique_artists`/`unique_albums` fixtures, placeholder test bodies
- Narrowed 16 overly broad exception handlers (#214): replaced `except Exception` with specific types in 8 cache/persistence/pipeline locations, replaced `contextlib.suppress(Exception)` with specific types in logger and encryption, added missing `IndexError` catch in MusicBrainz response parsing; kept justified safety nets in API orchestrator verification wrappers and artist context setup; added 5 error-path tests and fixed 1 vacuous test for full patch coverage
- Flaky `test_get_activity_period_classic_band` — skip gracefully when MusicBrainz returns `(None, None)` due to rate limiting
- Ruff format: missing blank line in `track_models.py` after Sourcery walrus operator refactor
- Sourcery warnings in `test_year_update.py`: extracted duplicate assertion helper, removed default-value arguments, added sourcery skip for test factory import
- Type mismatches in `test_scoring_comprehensive.py`: annotated `ArtistPeriodContext` dicts to match `set_artist_period_context` signature
- Missing `logger` kwarg in `test_custom_logger` after rate limiter rename — test was asserting `limiter.logger is custom_logger` without passing the logger
- Type mismatch in `test_incremental_filter.py`: replaced `dict[str, Any]` with `create_test_app_config()` to match `IncrementalFilterService(config: AppConfig)` signature
- `AlbumTypeDetectionConfig` pattern fields now use `None` vs `[]` semantics (`None` = defaults, `[]` = disabled)
- Dependabot PRs failing CI due to missing env vars in `load_config()` validation
- `DiscogsClient` received empty dict instead of typed `AppConfig`/`YearRetrievalConfig` — latent runtime crash on `_get_reissue_keywords()`
- Test cast mismatch: `cast(Analytics, ...)` → `cast(AnalyticsProtocol, ...)` to match `GenreManager` signature
- CI failures since B1: `full_sync.py` ruff format violation; `test_external_api_real.py` fixtures returning `dict` instead of `AppConfig`
- Legacy top-level `test_artists` now emits `DeprecationWarning` when migrated or silently ignored
- Path expansion bug when `HOME` environment variable is not set
- Config loader now uses `Path.expanduser()` for robust home directory resolution

## [2.0.0] - 2025-09-04

### Added

- Complete async/await rewrite for all I/O operations
- Multi-tier caching system (Memory → Disk → Snapshot)
- Library snapshot with SHA-256 verification for delta updates
- Batch processing for 30K+ track libraries
- External API integration (MusicBrainz, Discogs, Last.fm)
- Year scoring system with multi-API confidence scoring
- Contextual logging with artist | album | track context
- HTML analytics reports with function timing
- Allure test reporting integration
- AppleScript concurrency control (rate limiting)
- Encrypted API key storage with key rotation
- Pending verification service for year changes
- **Year Retrieval System**: Automatic album year fetching from MusicBrainz, Discogs, and iTunes
- **Scoring System**: Confidence-based year selection with reissue detection
- **Library Snapshots**: Fast startup with compressed track cache
- **Incremental Updates**: Process only recently modified tracks
- **LaunchAgent Support**: Background daemon operation
- **Pending Verification**: Queue for manual verification of uncertain years
- **Restore Command**: Fix albums with wrong reissue years

### Changed

- Architecture refactored to clean architecture (core/app/services/metrics layers)
- Configuration moved to YAML format
- Dependency injection via DependencyContainer
- Protocol-based interfaces for testability
- Complete async/await rewrite for better performance
- Pydantic v2 for all data validation
- Python 3.13 minimum requirement
- Protocol-based dependency injection

### Fixed

- Race conditions in concurrent AppleScript operations
- Cache key collisions with normalized hashing
- Memory leaks in large library processing
- Rate limiting for all external APIs
- Memory management for large libraries (30K+ tracks)
- AppleScript timeout handling

## [1.0.0] - 2024-01-15

### Added

- Initial release
- Basic genre updating from external APIs
- Apple Music integration via AppleScript
- Simple file-based caching
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

[Unreleased]: https://github.com/barad1tos/GenreUpdater/compare/v3.0.2...HEAD
[3.0.2]: https://github.com/barad1tos/GenreUpdater/compare/v3.0.1...v3.0.2
[3.0.1]: https://github.com/barad1tos/GenreUpdater/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/barad1tos/GenreUpdater/releases/tag/v3.0.0
