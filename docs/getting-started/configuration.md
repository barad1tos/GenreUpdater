# Configuration

Music Genre Updater uses YAML configuration files.

## Config File Priority

The application looks for config files in this order:

1. `--config PATH` flag (if specified)
2. `my-config.yaml` (user-specific, gitignored)
3. `config.yaml` (default template)

!!! tip "Recommended Setup"
Copy `config.yaml` to `my-config.yaml` and edit with your paths. This keeps your settings separate from the default
template.

## Required Settings

These paths must be absolute:

```yaml
# Path to your Music Library
music_library_path: /Users/username/Music/Music/Music Library.musiclibrary

# Path to AppleScript files (included in repo)
apple_scripts_dir: /path/to/GenreUpdater/applescripts

# Where to store logs
logs_base_dir: /path/to/logs
```

## Full Configuration Reference

```yaml
# ═══════════════════════════════════════════════════════════════
# CORE PATHS (required)
# ═══════════════════════════════════════════════════════════════
music_library_path: /Users/username/Music/Music/Music Library.musiclibrary
apple_scripts_dir: /path/to/GenreUpdater/applescripts
logs_base_dir: /path/to/logs

# ═══════════════════════════════════════════════════════════════
# APPLESCRIPT SETTINGS
# ═══════════════════════════════════════════════════════════════
apple_script_concurrency: 2  # Max parallel AppleScript calls (2-3 safe, >5 causes race conditions)

applescript_timeouts:
  single_artist_fetch: 600   # 10 min — timeout for fetching one artist's tracks
  full_library_fetch: 3600   # 1 hour — timeout for full library scan
  batch_update: 60           # 1 min — timeout per batch update operation

# ═══════════════════════════════════════════════════════════════
# BATCH PROCESSING
# ═══════════════════════════════════════════════════════════════
batch_processing:
  ids_batch_size: 200        # Tracks per batch when fetching by ID list
  enabled: true              # Enable batch mode

# ═══════════════════════════════════════════════════════════════
# LIBRARY SNAPSHOT (for 30K+ libraries)
# ═══════════════════════════════════════════════════════════════
library_snapshot:
  enabled: true              # Cache full library state to disk
  snapshot_dir: cache/snapshots
  delta_enabled: true        # Only fetch changed tracks on subsequent runs
  hash_algorithm: sha256     # Integrity verification

# ═══════════════════════════════════════════════════════════════
# INCREMENTAL UPDATES
# ═══════════════════════════════════════════════════════════════
incremental_interval_minutes: 15  # Skip tracks not modified in last N minutes

# ═══════════════════════════════════════════════════════════════
# RETRY CONFIGURATION
# ═══════════════════════════════════════════════════════════════
max_retries: 2               # Retry failed track updates
retry_delay_seconds: 1       # Delay between retries

# ═══════════════════════════════════════════════════════════════
# YEAR RETRIEVAL
# ═══════════════════════════════════════════════════════════════
year_retrieval:
  enabled: true
  preferred_api: musicbrainz

  rate_limits:
    discogs_requests_per_minute: 55
    musicbrainz_requests_per_second: 1
    concurrent_api_calls: 2

  processing:
    batch_size: 25
    cache_ttl_days: 36500  # ~100 years (permanent)

# ═══════════════════════════════════════════════════════════════
# ANALYTICS & LOGGING
# ═══════════════════════════════════════════════════════════════
analytics:
  enabled: true
  max_events: 1000
  duration_thresholds:
    short_max: 2             # <2s = green (fast)
    medium_max: 5            # 2-5s = gray (normal)
    long_max: 10             # 5-10s = pink (slow), >10s = needs optimization

logging:
  max_bytes: 5000000         # 5MB per log file
  backup_count: 1            # Keep 1 backup
  main_log_file: main/main.log
  year_changes_log_file: main/year_changes.log
  csv_output_file: csv/track_list.csv
  changes_report_file: csv/changes_report.csv
  analytics_log_file: analytics/analytics.log

# ═══════════════════════════════════════════════════════════════
# METADATA CLEANING
# ═══════════════════════════════════════════════════════════════
cleaning:
  remaster_keywords:
    - remaster
    - remastered
    - Re-recording
    - Redux
    - Expanded
    - Special Edition
    - Deluxe Edition
  album_suffixes_to_remove:
    - " - EP"
    - " - Single"

# ═══════════════════════════════════════════════════════════════
# EXCEPTIONS (skip these from processing)
# ═══════════════════════════════════════════════════════════════
exceptions:
  track_cleaning:
    - artist: "Example Artist"
      album: "Example Album"

# ═══════════════════════════════════════════════════════════════
# DEVELOPMENT / TESTING
# ═══════════════════════════════════════════════════════════════
test_artists: [ ]             # Artists to process in --test-mode

experimental:
  batch_updates_enabled: false  # Experimental: batch AppleScript updates (~10x faster)
  max_batch_size: 5
```

## Performance Settings

### AppleScript Concurrency

```yaml
# Max parallel AppleScript calls (2-3 safe, >5 causes race conditions)
apple_script_concurrency: 2

applescript_timeouts:
  single_artist_fetch: 600   # 10 min
  full_library_fetch: 3600   # 1 hour
  batch_update: 60           # 1 min per batch
```

!!! warning "Don't exceed 5 concurrent calls"
Music.app can't handle more than 5 concurrent AppleScript operations reliably. Values above 3 may cause race conditions.

### Library Snapshot (for 30K+ libraries)

```yaml
library_snapshot:
  enabled: true              # Cache full library state to disk
  snapshot_dir: cache/snapshots
  delta_enabled: true        # Only fetch changed tracks
```

### Incremental Updates

```yaml
# Skip tracks not modified in last N minutes
incremental_interval_minutes: 15
```

## Metadata Cleaning

```yaml
cleaning:
  remaster_keywords:
    - remaster
    - remastered
    - Deluxe Edition
    - Special Edition
  album_suffixes_to_remove:
    - " - EP"
    - " - Single"
```

## Exceptions

Skip specific artists or albums:

```yaml
exceptions:
  track_cleaning:
    - artist: "Weird Al Yankovic"  # Skip all albums
    - artist: "Pink Floyd"
      album: "The Wall"            # Skip specific album
```

## Logging

```yaml
logging:
  max_bytes: 5000000         # 5MB per log file
  backup_count: 1
  main_log_file: main/main.log
  changes_report_file: csv/changes_report.csv

analytics:
  enabled: true
  max_events: 1000
```

## Environment Variables

Some settings can be overridden via environment:

| Variable        | Description                      |
|-----------------|----------------------------------|
| `DISCOGS_TOKEN` | Discogs API authentication token |
| `CONTACT_EMAIL` | Email for API user-agent headers |

```bash
export DISCOGS_TOKEN="your_token_here"
export CONTACT_EMAIL="your@email.com"
```

## See Also

- [config.yaml](https://github.com/barad1tos/GenreUpdater/blob/main/config.yaml) — Default template with all options
- [Troubleshooting](../guide/troubleshooting.md) — Common configuration issues
