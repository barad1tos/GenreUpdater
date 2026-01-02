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
- Pre-commit hooks configuration (ruff, mypy)
- Test coverage enforcement (--cov-fail-under=70)

### Changed

- Upgraded GitHub Actions to latest versions (checkout v6, setup-uv v7, upload-artifact v5, codeql v4)
- README Python badge updated to 3.13+

### Fixed

- E2E test assertions for test_mode + dry_run scenarios
- Whitespace normalization in metadata cleaning comparisons

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
