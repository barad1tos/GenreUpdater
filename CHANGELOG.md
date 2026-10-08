# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Daemon: moved to an XDG layout (`~/.config/genreupdater`, `~/.local/share/genreupdater`, `~/.local/state/genreupdater`), separate from the Swift app's Application Support directory
- Daemon: deploys the latest stable release tag (`vX.Y.Z`) instead of `origin/main`; config and secrets live outside the clone
- Daemon: runs the pipeline with `uv run --frozen --no-dev`, so a run no longer reinstalls the development packages that its own `uv sync --frozen --no-dev` removed
- Dependabot updates Python dependencies through the uv ecosystem, so `uv.lock` moves together with `pyproject.toml`, and CI installs with `uv sync --locked`, so a lock that drifts from `pyproject.toml` turns CI red instead of being relocked silently
- ty type-checks the whole repository, including the test suite, `scripts/`, `tools/` and `docs/`: CI and the pre-commit hook run `uv run ty check`, and the hook runs on every Python or stub change instead of only changes under `src/`
- CI runs pydoclint over all of `src/`, whereas the pre-commit hook checks only staged files, so a pydoclint update that flags existing docstrings fails its own PR into `main` instead of merging green (as #485 did)

### Removed

- basedpyright and its `[tool.pyright]` configuration; ty is the only type checker, so Pylance and basedpyright fall back to their own defaults, and ty's language server is how an editor shows CI's diagnostics
- Daemon: `sync-fixtures.sh` (pushes to the protected `main` branch were always rejected)
- `scripts/sync-diagnostics.sh`: no callers, and it pushed from the legacy daemon clone to the protected `main` branch
- `DatabaseRetryHandler.async_retry_operation`, with `RetryMetadata` and the `RetryOperationContext` fields only its callers could read: it could not retry, because a context manager cannot re-run its `async with` block, so the first transient error raised `RuntimeError`, and only its own tests called it; `execute_with_retry` remains the retry API

### Fixed

- A task canceled on its own while others ran beside it was mishandled: a track year update counted as done; an album's year processing, a MusicBrainz release fetch, a cache initialization or a cache save vanished without a log; and a genre update broke that artist's genre pass. Each now counts as a failure and is logged with its exception type, and a failed track update also logs its traceback
- When some of an album's track year updates failed, the failed tracks were still recorded in the change log as updated to the new year; only the tracks that were updated are recorded now
- A cache that failed to save at shutdown was followed by "All caches saved to disk"; shutdown now warns that the cache was not saved and names it
- Year-verification queue saves no longer fail with `No such file or directory` on `pending_year_verification.csv.tmp` when several albums are marked at once, and a stale queue snapshot can no longer overwrite a newer one
- `zip` misalignment in year_search_coordinator: filtered API tasks vs unfiltered api_order
- Naive `datetime.fromtimestamp()` calls missing timezone in analytics.py
- `logging.warning()` using root logger instead of module-level `_logger` in applescript_client
- `exception()` used without active exception in file_validator (caused misleading tracebacks)
- `str.replace(".csv", ...)` replaced with `Path.with_name()` in pending_verification
- Dead `allow_music_app` branch in sanitizer (no reserved words contained "music")
- ASCII art log separators replaced with structured single-line messages
- E2E test assertions for test_mode + dry_run scenarios
- Whitespace normalization in metadata cleaning comparisons
- AppleScripts were rejected when the scripts directory sat below a hidden directory such as `~/.local/share`
- Daemon: a run skipped because Music.app was not running exited successfully and showed an "Update completed successfully" notification; `main.py` now exits with `EX_TEMPFAIL` (75), and `run-daemon.sh` logs the skip without a notification
- Daemon: `uv sync` failures in `run-daemon.sh` were treated as success
- Daemon: a run triggered right after boot or wake, before the network was up, failed with a "Git fetch failed" notification; `run-daemon.sh` now retries the fetch for about a minute and, if there is still no network, skips the run without a notification; with a network present, the failure notification now names the cause, and each fetch attempt is capped at 30 s so a stalled transfer can no longer hold the daemon lock indefinitely
- Daemon: re-running `install.sh` left a development-checkout `apple_scripts_dir` in an existing config and did not restore missing state files
- A run that fetched no tracks from a library the last snapshot recorded as non-empty exited successfully; it now exits with an error, so the daemon reports the failure. A library that really became empty is recorded with `main.py --fresh`

### Security

- The Discogs token no longer appears in logs: startup no longer encrypts a plaintext token just to log the encrypted value, Discogs request headers hide the `Authorization` value, and debug config dumps replace the token with `<redacted>`

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

### Changed

- Architecture refactored to clean architecture (core/app/services/metrics layers)
- Configuration moved to YAML format
- Dependency injection via DependencyContainer
- Protocol-based interfaces for testability

### Fixed

- Race conditions in concurrent AppleScript operations
- Cache key collisions with normalized hashing
- Memory leaks in large library processing

## [1.0.0] - 2024-01-15

### Added

- Initial release
- Basic genre updating from external APIs
- Apple Music integration via AppleScript
- Simple file-based caching

[Unreleased]: https://github.com/barad1tos/GenreUpdater/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/barad1tos/GenreUpdater/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/barad1tos/GenreUpdater/releases/tag/v1.0.0
