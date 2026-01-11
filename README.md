# Music Genre Updater

![image](images/logo.png)

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.13%2B-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple" alt="macOS">
  <a href="https://github.com/barad1tos/GenreUpdater/actions/workflows/ci.yml"><img src="https://github.com/barad1tos/GenreUpdater/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/barad1tos/GenreUpdater"><img src="https://codecov.io/gh/barad1tos/GenreUpdater/graph/badge.svg" alt="codecov"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/badge/linter-ruff-blue?logo=ruff" alt="Ruff"></a>
</p>

Automatically updates **genres** and **release years** for your Apple Music tracks.

## What It Does

1. **Fixes messy genres** — Takes the genre from your **earliest added album** for each artist
2. **Fills in missing years** — Looks up actual release years from MusicBrainz, Discogs, and Last.fm
3. **Cleans up metadata** — Removes "Remastered", "Deluxe Edition", and other clutter
4. **Previews before changing** — Run with `--dry-run` to see what would change

## Quick Start

**Requirements:** macOS 10.15+, Python 3.13+, Apple Music app

```bash
# Install
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/barad1tos/GenreUpdater.git
cd GenreUpdater && uv sync

# Configure
cp config.yaml my-config.yaml
# Edit my-config.yaml with your paths

# Run
uv run python main.py --dry-run  # Preview first
uv run python main.py            # Apply changes
```

## Usage

```bash
# Basic commands
uv run python main.py                    # Full update (genres + years)
uv run python main.py --dry-run          # Preview without changing
uv run python main.py --force            # Bypass cache, process everything

# Specific operations
uv run python main.py clean_artist --artist "Pink Floyd"
uv run python main.py update_years --artist "Otep"
uv run python main.py update_genres --artist "Radiohead"
uv run python main.py verify_database

# Revert and restore
uv run python main.py revert_years --artist "Pink Floyd" --album "The Wall"
uv run python main.py restore_release_years --artist "Nirvana" --threshold 5

# Maintenance
uv run python main.py verify_pending          # Re-verify failed year lookups
uv run python main.py batch --file artists.txt --operation full
uv run python main.py rotate_keys             # Rotate API key encryption
```

## Configuration

Edit `my-config.yaml` with your paths:

```yaml
music_library_path: /Users/you/Music/Music/Music Library.musiclibrary
apple_scripts_dir: /path/to/GenreUpdater/applescripts
logs_base_dir: /path/to/logs
```

See [Configuration Reference](https://barad1tos.github.io/GenreUpdater/getting-started/configuration/) for all options.

## Documentation

Full documentation is available at **[barad1tos.github.io/GenreUpdater](https://barad1tos.github.io/GenreUpdater/)**

- [Installation](https://barad1tos.github.io/GenreUpdater/getting-started/installation/)
- [CLI Commands](https://barad1tos.github.io/GenreUpdater/guide/cli/)
- [Configuration](https://barad1tos.github.io/GenreUpdater/getting-started/configuration/)
- [Automation](https://barad1tos.github.io/GenreUpdater/guide/automation/)
- [Architecture](https://barad1tos.github.io/GenreUpdater/architecture/overview/)
- [Troubleshooting](https://barad1tos.github.io/GenreUpdater/guide/troubleshooting/)
- [FAQ](https://barad1tos.github.io/GenreUpdater/guide/faq/)

## Performance & Security

### Performance

- **Library Snapshot Caching** — Load 30,000+ tracks in under 1 second
- **Incremental Delta Updates** — Process only changed tracks
- **Multi-Tier Caching** — Memory → Disk → Snapshot
- **Async/Await Architecture** — Non-blocking I/O

### Security

- **Encrypted Configuration** — API keys stored with Fernet encryption
- **Key Rotation** — Built-in `rotate_keys` command
- **Input Validation** — AppleScript inputs sanitized

## Troubleshooting

| Problem                    | Solution                                  |
|----------------------------|-------------------------------------------|
| "Music app is not running" | Launch Music.app before running           |
| AppleScript timeout        | Increase `applescript_timeouts` in config |
| Cache corruption           | Delete `cache/` directory and re-run      |

See [Troubleshooting Guide](https://barad1tos.github.io/GenreUpdater/guide/troubleshooting/) for more.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

```bash
git clone https://github.com/barad1tos/GenreUpdater.git
cd GenreUpdater && uv sync
uv run pytest tests/unit/ -v --cov=src
uv run ruff check src/ tests/
```

## Links

- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [License](LICENSE) — MIT

## Contact

**Author:** Roman Borodavkin

- Email: [roman.borodavkin@gmail.com](mailto:roman.borodavkin@gmail.com)
- GitHub: [@barad1tos](https://github.com/barad1tos)

---

> **Warning:** Changes sync to iCloud immediately and cannot be easily reverted. Always use `--dry-run` first!
