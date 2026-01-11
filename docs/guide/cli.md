# CLI Commands

Complete reference for all available command-line options.

## Global Options

These options work with any command:

| Option | Description |
|--------|-------------|
| `--fresh` | Clear all caches and snapshots before running |
| `--force` | Bypass incremental checks and cache |
| `--dry-run` | Simulate changes without applying them |
| `--test-mode` | Run only on artists from `development.test_artists` |
| `--verbose`, `-v` | Enable verbose logging |
| `--quiet`, `-q` | Suppress non-critical output |
| `--config PATH` | Path to configuration file |

## Commands

### Default (no command)

Run the full library update process:

```bash
uv run python main.py [options]
```

Examples:
```bash
# Standard run
uv run python main.py

# Preview changes without applying
uv run python main.py --dry-run

# Force full refresh
uv run python main.py --force --fresh
```

### update_years

Update album release years from external APIs (MusicBrainz, Discogs, iTunes).

```bash
uv run python main.py update_years [--artist NAME] [--force]
```

| Option | Description |
|--------|-------------|
| `--artist` | Process specific artist only |
| `--force` | Bypass cache and re-fetch all years |

Aliases: `years`

### update_genres

Update track genres from the dominant genre database.

```bash
uv run python main.py update_genres [--artist NAME]
```

| Option | Description |
|--------|-------------|
| `--artist` | Process specific artist only |

Aliases: `genres`

### clean_artist

Remove promotional text from track and album names.

```bash
uv run python main.py clean_artist --artist "Artist Name"
```

| Option | Required | Description |
|--------|----------|-------------|
| `--artist` | Yes | Artist name to process |

Aliases: `clean`

### revert_years

Revert previously applied year changes.

```bash
uv run python main.py revert_years --artist "Artist Name" [--album "Album"] [--backup-csv PATH]
```

| Option | Required | Description |
|--------|----------|-------------|
| `--artist` | Yes | Artist name to revert |
| `--album` | No | Specific album (default: all albums) |
| `--backup-csv` | No | Path to backup CSV with original years |

Aliases: `revert`

### restore_release_years

Restore years from Apple Music's read-only `release_year` field.

```bash
uv run python main.py restore_release_years [--artist NAME] [--album NAME] [--threshold N]
```

| Option | Description |
|--------|-------------|
| `--artist` | Process specific artist only |
| `--album` | Process specific album (requires `--artist`) |
| `--threshold` | Year difference threshold (default: 5) |

Aliases: `restore`

!!! tip "When to use"
    Use this command when albums received incorrect reissue years from APIs.
    It compares the writable `year` field with Apple's immutable `release_year`.

### verify_database

Verify track database integrity against Music.app.

```bash
uv run python main.py verify_database
```

Aliases: `verify-db`

### verify_pending

Re-verify albums that previously failed year fetching.

```bash
uv run python main.py verify_pending
```

Aliases: `pending`

### batch

Process multiple artists from a file.

```bash
uv run python main.py batch --file artists.txt
```

| Option | Required | Description |
|--------|----------|-------------|
| `--file` | Yes | Path to file with artist names (one per line) |

### rotate_keys

Rotate encryption keys for API credentials.

```bash
uv run python main.py rotate_keys
```

!!! warning "Important"
    This command doesn't require Music.app to be running, unlike other commands.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error occurred |
| 2 | Invalid arguments |

## Environment Variables

The application uses these environment variables:

| Variable | Description |
|----------|-------------|
| `DISCOGS_TOKEN` | Discogs API authentication token |
| `CONTACT_EMAIL` | Email for API user-agent headers |

Set them in a `.env` file or export directly:

```bash
export DISCOGS_TOKEN="your_token_here"
export CONTACT_EMAIL="your@email.com"
```
