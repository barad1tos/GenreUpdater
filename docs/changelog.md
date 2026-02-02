# Changelog

All notable changes to Music Genre Updater.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Test fixture deduplication: shared logger fixtures in root conftest
- Unified track factory and mock fixtures in tracks conftest
- Migrated year_batch test files to shared fixtures (-409/+208 lines)
- Fixed TC001 lint: moved type-only imports to TYPE_CHECKING blocks
- Consolidated year_determinator mock into shared `create_year_determinator_mock()` helper
- Pinned hypothesis==6.151.4 for reproducible test runs
- Applied ruff format to all new test files

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
