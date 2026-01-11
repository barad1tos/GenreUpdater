# Music Genre Updater

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/barad1tos/GenreUpdater/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple)](https://www.apple.com/macos/)
[![CI](https://github.com/barad1tos/GenreUpdater/actions/workflows/ci.yml/badge.svg)](https://github.com/barad1tos/GenreUpdater/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/barad1tos/GenreUpdater/graph/badge.svg)](https://codecov.io/gh/barad1tos/GenreUpdater)

**Automatically update genres and release years for your Apple Music tracks.**

---

## What It Does

Music Genre Updater solves common problems with Apple Music metadata:

| Feature                      | Description                                                                                           |
|------------------------------|-------------------------------------------------------------------------------------------------------|
| **Fixes messy genres**       | Takes the genre from your **earliest added album** for each artist and applies it to all their tracks |
| **Fills in missing years**   | Looks up actual release years from MusicBrainz, Discogs, and iTunes Search API                        |
| **Cleans up metadata**       | Removes "Remastered", "Deluxe Edition", and other clutter from track names                            |
| **Previews before changing** | Run with `--dry-run` to see what would change without touching your library                           |

!!! tip "How genre determination works"
The app uses the **"dominant genre"** concept: for each artist, it finds the **earliest added album** in your library
and applies that album's genre to all tracks by that artist. Make sure your first album for each artist has the correct
genre!

## Quick Example

```bash
# Preview changes first
uv run python main.py --dry-run

# Apply changes
uv run python main.py

# Update only release years
uv run python main.py update_years --force

# Clean specific artist metadata
uv run python main.py clean_artist --artist "Pink Floyd"
```

## Key Features

### Performance

Built for large libraries (30K+ tracks):

- **Library Snapshot Caching** — Load 30,000+ tracks in under 1 second from disk cache
- **Incremental Delta Updates** — Process only tracks changed since last run (based on `date_modified`)
- **Multi-Tier Caching** — Three levels: Memory (L1, <1ms) → Disk JSON (L2, 10-50ms) → Snapshot (L3, <1s for 30K)
- **Async/Await Architecture** — All I/O operations are non-blocking (aiohttp, aiofiles)
- **Parse Failure Tolerance** — Automatically recovers from up to 3 consecutive AppleScript parse failures

### Security

Your API keys and library are protected:

- **Encrypted Configuration** — API keys stored using Fernet symmetric encryption
- **Key Rotation** — Built-in `rotate_keys` command to rotate encryption keys
- **Database Verification** — `verify_database` command checks track database integrity against Music.app
- **Input Validation** — All AppleScript inputs sanitized to prevent injection attacks

### External APIs

Year lookup queries three sources and uses scoring to pick the most accurate:

| API               | What it provides                                        |
|-------------------|---------------------------------------------------------|
| **MusicBrainz**   | Original release year, high accuracy, community-curated |
| **Discogs**       | Release year, excellent for vinyl and reissues          |
| **iTunes Search** | Apple's own release data                                |

## Requirements

- macOS 10.15+
- Python 3.13+
- Apple Music app

## Documentation

<div class="grid cards" markdown>

- :material-rocket-launch:{ .lg .middle } **Getting Started**

  ---

  Install and run your first genre update in 2 minutes

  [:octicons-arrow-right-24: Installation](getting-started/installation.md)

- :material-console:{ .lg .middle } **User Guide**

  ---

  CLI commands, automation with launchctl, and advanced usage

  [:octicons-arrow-right-24: CLI Commands](guide/cli.md)

- :material-cog:{ .lg .middle } **Configuration**

  ---

  Full YAML reference with all options explained

  [:octicons-arrow-right-24: Configuration](getting-started/configuration.md)

- :material-graph:{ .lg .middle } **Architecture**

  ---

  C4 diagrams, data flow, and design patterns

  [:octicons-arrow-right-24: Architecture](architecture/overview.md)

- :material-api:{ .lg .middle } **API Reference**

  ---

  Auto-generated documentation from source code

  [:octicons-arrow-right-24: API Docs](api/index.md)

- :material-frequently-asked-questions:{ .lg .middle } **Troubleshooting**

  ---

  Common issues, diagnostic commands, and FAQ

  [:octicons-arrow-right-24: Troubleshooting](guide/troubleshooting.md)

</div>

## Quick Links

- [GitHub Repository](https://github.com/barad1tos/GenreUpdater)
- [Issue Tracker](https://github.com/barad1tos/GenreUpdater/issues)
- [Changelog](changelog.md)
- [Contributing Guide](contributing.md)

---

!!! warning "Important"
Changes sync to iCloud Music Library immediately and cannot be easily reverted. Always use `--dry-run` first!
