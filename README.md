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

1. **Fixes messy genres** — Takes the genre from your **earliest added album** for each artist and applies it to all their tracks (so make sure your first album has the correct genre!)
2. **Fills in missing years** — Looks up actual release years from MusicBrainz, Discogs, and Last.fm, then writes them
   to your library
3. **Cleans up metadata** — Removes "Remastered", "Deluxe Edition", and other clutter from track names
4. **Previews before changing** — Run with `--dry-run` to see what would change without touching your library

<details>
<summary>Technical details: Performance & Security (v2.0)</summary>

### Performance

- **Library Snapshot Caching** — Load 30,000+ tracks in under 1 second from disk cache
- **Incremental Delta Updates** — Process only tracks changed since last run (based on `date_modified`)
- **Multi-Tier Caching** — Three levels: Memory (L1, <1ms) → Disk JSON (L2, 10-50ms) → Snapshot (L3, <1s for 30K)
- **Async/Await Architecture** — All I/O operations are non-blocking (aiohttp, aiofiles)
- **Parse Failure Tolerance** — Automatically recovers from up to 3 consecutive AppleScript parse failures

### Security

- **Encrypted Configuration** — API keys stored using Fernet symmetric encryption (cryptography library)
- **Key Rotation** — Built-in `rotate_keys` command to rotate encryption keys
- **Database Verification** — `verify_database` command checks track database integrity against Music.app
- **Input Validation** — All AppleScript inputs sanitized to prevent injection attacks

</details>

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

<details>
<summary>Alternative: Traditional pip/venv installation</summary>

```bash
git clone https://github.com/barad1tos/GenreUpdater.git
cd GenreUpdater

# Create and activate virtual environment
python3.13 -m venv .venv
source .venv/bin/activate

# Install package in editable mode
pip install -e .

# Verify
python main.py --help
```

**Dependencies** (from pyproject.toml):

- pydantic 2.x — Data validation
- aiohttp — Async HTTP client
- aiofiles — Async file I/O
- cryptography — API key encryption
- orjson — Fast JSON parsing
- rich — Terminal formatting

</details>

## Usage

```bash
# Basic commands
uv run python main.py                    # Full update (genres + years)
uv run python main.py --dry-run          # Preview without changing
uv run python main.py --force            # Bypass cache, process everything

# Specific operations
uv run python main.py clean_artist --artist "Pink Floyd"
uv run python main.py update_years --artist "Otep"
uv run python main.py verify_database
```

<details>
<summary>All commands and flags reference</summary>

### Global Flags

| Flag              | Description                                                        |
|-------------------|--------------------------------------------------------------------|
| `--force`         | Bypass incremental checks and cache, process all tracks            |
| `--dry-run`       | Preview changes without writing to Music.app                       |
| `--test-mode`     | Run only on artists listed in `test_artists` config                |
| `--verbose`, `-v` | Enable debug-level logging                                         |
| `--quiet`, `-q`   | Suppress non-critical console output                               |
| `--config PATH`   | Use custom config file (default: `my-config.yaml` → `config.yaml`) |

### Commands

| Command           | Alias         | Description                                                       |
|-------------------|---------------|-------------------------------------------------------------------|
| _(default)_       | —             | Full library update: fetch all tracks, update genres and years    |
| `clean_artist`    | `clean`       | Remove "Remastered", "Deluxe" etc. from specific artist's tracks  |
| `update_years`    | `years`       | Fetch release years from APIs without updating genres             |
| `revert_years`    | `revert`      | Rollback year changes using `changes_report.csv` or custom backup |
| `verify_database` | `verify-db`   | Check that cached track IDs still exist in Music.app              |
| `verify_pending`  | `pending`     | Retry year lookups for albums that previously failed              |
| `batch`           | —             | Process multiple artists from a text file                         |
| `rotate_keys`     | `rotate-keys` | Generate new encryption key and re-encrypt all API tokens         |

### Command Examples

```bash
# Revert year changes for specific album
uv run python main.py revert_years --artist "Otep" --album "The God Slayer"

# Revert using backup CSV
uv run python main.py revert_years --artist "Otep" --backup-csv /path/to/backup.csv

# Batch process artists from file (one artist per line)
uv run python main.py batch --file artists.txt --operation full

# Available operations: clean, years, full
uv run python main.py batch --file artists.txt --operation years

# Rotate encryption keys (doesn't require Music.app)
uv run python main.py rotate_keys --new-password "optional-custom-password"
```

</details>

## Configuration

Edit `my-config.yaml` with your paths:

```yaml
music_library_path: /Users/you/Music/Music/Music Library.musiclibrary
apple_scripts_dir: /path/to/GenreUpdater/applescripts
logs_base_dir: /path/to/logs
```

<details>
<summary>Full configuration reference</summary>

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

</details>

<details>
<summary>Automation with launchctl (run on schedule)</summary>

Create `~/Library/LaunchAgents/com.barad1tos.MusicGenreUpdater.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
    <dict>
        <key>Label</key>
        <string>com.barad1tos.MusicGenreUpdater</string>

        <key>ProgramArguments</key>
        <array>
            <string>/path/to/GenreUpdater/.venv/bin/python</string>
            <string>/path/to/GenreUpdater/main.py</string>
        </array>

        <key>StartInterval</key>
        <integer>1800</integer>  <!-- Run every 30 minutes (1800 seconds) -->

        <key>WorkingDirectory</key>
        <string>/path/to/GenreUpdater</string>

        <key>StandardOutPath</key>
        <string>/path/to/logs/launchctl_stdout.log</string>

        <key>StandardErrorPath</key>
        <string>/path/to/logs/launchctl_stderr.log</string>

        <key>EnvironmentVariables</key>
        <dict>
            <key>PATH</key>
            <string>/usr/local/bin:/usr/bin:/bin</string>
        </dict>
    </dict>
</plist>
```

**Commands:**

```bash
# Load (start running on schedule)
launchctl load ~/Library/LaunchAgents/com.barad1tos.MusicGenreUpdater.plist

# Check if running
launchctl list | grep com.barad1tos.MusicGenreUpdater

# Unload (stop)
launchctl unload ~/Library/LaunchAgents/com.barad1tos.MusicGenreUpdater.plist

# Run immediately (for testing)
launchctl start com.barad1tos.MusicGenreUpdater
```

</details>

## Architecture

The code is organized into four layers. You don't need to understand this to use the tool.

<details>
<summary>Architecture diagram and code structure</summary>

### System Context (C4 Level 1)

Shows how the system interacts with external actors and systems.

```mermaid
graph LR
    User((User))

    subgraph System["Music Genre Updater"]
        MGU[Application]
    end

    MusicApp[(Music.app)]
    MB[(MusicBrainz API)]
    DG[(Discogs API)]
    LF[(Last.fm API)]
    FS[(File System)]
    User -->|commands| MGU
    MGU -->|read tracks| MusicApp
    MGU -->|write updates| MusicApp
    MGU -->|query metadata| MB
    MGU -->|query metadata| DG
    MGU -->|query metadata| LF
    MGU -->|cache/reports| FS
    classDef external fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef system fill:#73D0FF, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef user fill:#BAE67E, stroke:#1F2430, stroke-width:2px, color:#1F2430
class MusicApp,MB,DG,LF,FS external
class MGU system
class User user
```

---

### Container Diagram (C4 Level 2)

Shows the main containers inside the system and data flow between them.

```mermaid
graph TB
    User((User))
    MusicApp[(Music.app)]
    ExtAPIs[(External APIs)]
    FileSystem[(File System)]

    subgraph System["Music Genre Updater"]
        CLI[CLI Parser]
        Orch[Orchestrator]
        Pipes[Pipelines]
        Core[Track Processor]
        Apple[AppleScript Client]
        Cache[Cache Service]
        APIs[API Clients]
        Metrics[Reports]
    end

    User -->|" --dry-run, --force "| CLI
    CLI -->|parsed args| Orch
    Orch -->|route command| Pipes
    Pipes -->|process tracks| Core
    Core -->|fetch/update| Apple
    Core -->|get metadata| APIs
    Core -->|read/write| Cache
    Pipes -->|generate| Metrics
    Apple <-->|AppleScript| MusicApp
    APIs -->|HTTP| ExtAPIs
    Cache <-->|JSON/pickle| FileSystem
    Metrics -->|HTML/CSV| FileSystem
    classDef external fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef internal fill:#73D0FF, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef user fill:#BAE67E, stroke:#1F2430, stroke-width:2px, color:#1F2430
class MusicApp,ExtAPIs,FileSystem external
class CLI,Orch,Pipes,Core,Apple,Cache,APIs,Metrics internal
class User user
```

---

### Data Flow: Genre Update

```mermaid
sequenceDiagram
    participant U as User
    participant CLI as CLI
    participant O as Orchestrator
    participant P as Pipeline
    participant A as AppleScript
    participant M as Music.app
    participant C as Cache
    U ->> CLI: uv run python main.py
    CLI ->> O: parsed arguments
    O ->> A: fetch all tracks
    A ->> M: AppleScript query
    M -->> A: track data (30K+)
    A -->> O: List[TrackDict]
    O ->> C: check snapshot
    C -->> O: delta (changed tracks)
    O ->> P: process tracks
    Note over P: Dominant genre = genre from<br/>earliest added album
    P ->> A: update genre
    A ->> M: AppleScript set
    M -->> A: success
    P -->> O: changes made
    O -->> CLI: summary report
```

---

### Data Flow: Year Update

```mermaid
sequenceDiagram
    participant P as Pipeline
    participant API as API Orchestrator
    participant MB as MusicBrainz
    participant DG as Discogs
    participant LF as Last.fm
    participant C as Cache
    participant A as AppleScript
    P ->> C: check cached year
    alt cache hit
        C -->> P: cached year + confidence
    else cache miss
        P ->> API: fetch year (artist, album)
        par query all APIs
            API ->> MB: search release
            API ->> DG: search release
            API ->> LF: get album info
        end
        MB -->> API: year + score
        DG -->> API: year + score
        LF -->> API: year + score
        API ->> API: resolve best year (scoring)
        API -->> P: year + confidence
        P ->> C: store in cache
    end
    P ->> A: update year
```

---

### Component: App Layer (`src/app/`)

```mermaid
graph LR
    subgraph Entry["Entry Point"]
        CLI[cli.py]
        Orch[orchestrator.py]
    end

    subgraph Pipelines["Processing Pipelines"]
        MU[music_updater]
        FS[full_sync]
        YU[year_update]
        TC[track_cleaning]
    end

    subgraph Features["Feature Modules"]
        Batch[batch/processor]
        Crypto[crypto/encryption]
        Verify[verify/database]
    end

    CLI -->|args| Orch
    Orch -->|genre+year| MU
    Orch -->|full library| FS
    Orch -->|years only| YU
    Orch -->|clean metadata| TC
    Orch -->|batch/crypto/verify| Features
    classDef entry fill:#73D0FF, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef pipeline fill:#5BC0EB, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef feature fill:#95E6CB, stroke:#1F2430, stroke-width:2px, color:#1F2430
class CLI,Orch entry
class MU,FS,YU,TC pipeline
class Batch,Crypto,Verify feature
```

---

### Component: Core Layer (`src/core/`)

```mermaid
graph TB
    subgraph Input["Input"]
        IN[TrackDict from AppleScript]
    end

    subgraph Processing["tracks/"]
        TP[track_processor]
        GM[genre_manager]
        YR[year_retriever]
        AR[artist_renamer]
        IF[incremental_filter]
        UE[update_executor]
    end

    subgraph Output["Output"]
        OUT[Updated TrackDict]
    end

    IN -->|raw tracks| IF
    IF -->|filtered delta| TP
    TP -->|artist tracks| GM
    GM -->|dominant genre| TP
    TP -->|album info| YR
    YR -->|release year| TP
    TP -->|dirty names| AR
    AR -->|clean names| TP
    TP -->|changes| UE
    UE -->|execute| OUT
    classDef io fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef proc fill:#FFD580, stroke:#1F2430, stroke-width:2px, color:#1F2430
class IN,OUT io
class TP,GM,YR,AR,IF,UE proc
```

---

### Component: Services Layer (`src/services/`)

```mermaid
graph TB
    subgraph Callers["From Core Layer"]
        Core[Track Processor]
    end

    subgraph Apple["apple/"]
        AC[applescript_client]
        AE[executor]
        RL[rate_limiter]
    end

    subgraph Cache["cache/"]
        CO[orchestrator]
        SS[snapshot]
        ALB[album_cache]
        API_C[api_cache]
    end

    subgraph APIs["api/"]
        AO[orchestrator]
        MB[musicbrainz]
        DG[discogs]
        LF[lastfm]
        YS[year_scoring]
    end

    subgraph External["External Systems"]
        MusicApp[(Music.app)]
        ExtAPI[(HTTP APIs)]
        Files[(File System)]
    end

    Core -->|fetch/update tracks| AC
    AC --> AE --> RL
    RL -->|AppleScript| MusicApp
    Core -->|get/set cache| CO
    CO --> SS & ALB & API_C
    SS & ALB & API_C -->|read/write| Files
    Core -->|query metadata| AO
    AO --> MB & DG & LF
    AO --> YS
    MB & DG & LF -->|HTTP| ExtAPI
    classDef caller fill:#FFD580, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef apple fill:#D4BFFF, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef cache fill:#CE93D8, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef api fill:#BA68C8, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef external fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    class Core caller
class AC,AE,RL apple
class CO,SS,ALB,API_C cache
class AO,MB,DG,LF,YS api
class MusicApp,ExtAPI,Files external
```

---

### Component: Metrics Layer (`src/metrics/`)

```mermaid
graph LR
    subgraph Input["From Pipelines"]
        Data[Processing Results]
    end

    subgraph Analytics["Tracking"]
        AN[analytics]
        MO[monitoring]
    end

    subgraph Reports["Generation"]
        HR[html_reports]
        CR[change_reports]
        ER[error_reports]
    end

    subgraph Output["To File System"]
        HTML[reports/*.html]
        CSV[reports/*.csv]
    end

    Data --> AN & MO
    AN --> HR & CR
    MO --> ER
    HR --> HTML
    CR & ER --> CSV
    classDef io fill:#F28779, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef track fill:#BAE67E, stroke:#1F2430, stroke-width:2px, color:#1F2430
    classDef report fill:#C5E1A5, stroke:#1F2430, stroke-width:2px, color:#1F2430
class Data,HTML,CSV io
class AN,MO track
class HR,CR,ER report
```

---

### Layer Responsibilities

| Layer        | Path            | What it does                                                           |
|--------------|-----------------|------------------------------------------------------------------------|
| **App**      | `src/app/`      | Entry point, command routing, pipeline selection                       |
| **Core**     | `src/core/`     | Business logic: genre calculation, year determination, track filtering |
| **Services** | `src/services/` | I/O adapters: AppleScript, cache, external API clients                 |
| **Metrics**  | `src/metrics/`  | Observability: timing, reports, error tracking                         |

### Key Design Patterns

- **Dependency Injection** — `DependencyContainer` creates and wires all services at startup
- **Protocol-Based Interfaces** — `AppleScriptClientProtocol`, `CacheServiceProtocol` enable testing with mocks
- **Multi-Tier Caching** — Memory (L1) → Disk (L2) → Snapshot (L3) for optimal performance
- **Contextual Logging** — Every log entry includes `artist | album | track` context for debugging

### Caching Performance

| Tier | Storage           | Latency     | TTL       | Use Case                  |
|------|-------------------|-------------|-----------|---------------------------|
| L1   | Memory (dict)     | <1ms        | Session   | Hot data, repeated access |
| L2   | Disk (JSON)       | 10-50ms     | 20min     | API responses             |
| L3   | Snapshot (pickle) | <1s for 30K | Permanent | Full library state        |

### AppleScript Integration

Scripts in `applescripts/` directory:

| Script                            | Purpose                              | Output Format                                    |
|-----------------------------------|--------------------------------------|--------------------------------------------------|
| `fetch_tracks.scpt`               | Get all tracks or filtered by artist | ASCII-delimited: `\x1E` (field), `\x1D` (record) |
| `fetch_tracks_by_ids.scpt`        | Get specific tracks by ID list       | Same format                                      |
| `update_property.applescript`     | Set single track property            | "Success: ..." or "No Change: ..."               |
| `batch_update_tracks.applescript` | Batch updates (experimental)         | JSON status array                                |

</details>

## Troubleshooting

| Problem                    | Solution                                               |
|----------------------------|--------------------------------------------------------|
| "Music app is not running" | Launch Music.app before running (except `rotate_keys`) |
| AppleScript timeout        | Increase `applescript_timeouts` values in config       |
| Cache corruption           | Delete `cache/` directory and re-run                   |
| Parse failures in batch    | Reduce `batch_processing.ids_batch_size` (try 100)     |

<details>
<summary>Diagnostic commands</summary>

```bash
# Check Python version (must be 3.13+)
python3 --version

# Test AppleScript connection
osascript applescripts/fetch_tracks.scpt "" 0 10  # Fetch first 10 tracks

# Check launch agent status
launchctl list | grep com.barad1tos.MusicGenreUpdater

# Watch logs in real-time
tail -f /path/to/logs/main/main.log

# Check for errors
grep -i error /path/to/logs/main/main.log | tail -20
```

</details>

<details>
<summary>Log file locations</summary>

All paths relative to `logs_base_dir` in config:

| File                               | Contents                                |
|------------------------------------|-----------------------------------------|
| `main/main.log`                    | Main application log (INFO level)       |
| `main/year_changes.log`            | Year update decisions and API responses |
| `csv/track_list.csv`               | Full track listing from last run        |
| `csv/changes_report.csv`           | All changes made (for revert)           |
| `csv/dry_run_report.csv`           | Changes that would be made (dry-run)    |
| `analytics/analytics.log`          | Function timing and call counts         |
| `analytics/reports/analytics.html` | Visual performance dashboard            |

</details>

## FAQ

<details>
<summary>How do I change update frequency?</summary>

Two settings control this:

1. **launchctl interval** — `StartInterval` in plist (seconds between runs)
2. **Incremental filter** — `incremental_interval_minutes` in config.yaml (skip tracks not modified recently)

Example: Run every hour, but only process tracks modified in last 30 minutes:

- plist: `<integer>3600</integer>` (1 hour)
- config: `incremental_interval_minutes: 30`

</details>

<details>
<summary>How can I see what was changed?</summary>

Check `<logs_base_dir>/csv/changes_report.csv`. Contains:

- Track ID, name, artist, album
- Old and new values for genre/year
- Timestamp of change

Use this file with `revert_years` command to undo changes.

</details>

<details>
<summary>Can I exclude specific artists or albums?</summary>

Yes, add to `exceptions.track_cleaning` in config:

```yaml
exceptions:
  track_cleaning:
    - artist: "Weird Al Yankovic"  # Skip all albums
    - artist: "Pink Floyd"
      album: "The Wall"            # Skip specific album
```

</details>

<details>
<summary>Why are some years wrong?</summary>

Year determination uses scoring from 3 APIs. Sometimes:

- APIs disagree on release year
- Remastered versions have different years
- Regional releases vary

Use `revert_years` to fix, then add to exceptions.

</details>

<details>
<summary>Is it safe to run on my library?</summary>

Yes, with caveats:

1. **Always run `--dry-run` first** to preview changes
2. **Changes sync to iCloud immediately** and cannot be undone via Time Machine
3. **Use `revert_years`** with `changes_report.csv` to undo year changes
4. Genre changes have no built-in revert (restore from backup if needed)

</details>

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

<details>
<summary>Development setup</summary>

```bash
# Clone and setup
git clone https://github.com/barad1tos/GenreUpdater.git
cd GenreUpdater
git checkout -b feature/your-feature
uv sync

# Run tests
uv run pytest tests/unit/ -v --cov=src

# Linting
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type checking
uv run mypy src/

# Security scan
uv run bandit -r src/ -c pyproject.toml
```

</details>

## Links

- [Changelog](CHANGELOG.md) — Version history
- [Contributing](CONTRIBUTING.md) — Development guidelines
- [Security](SECURITY.md) — Vulnerability reporting
- [License](LICENSE) — MIT

## Contact

**Author:** Roman Borodavkin

- Email: [roman.borodavkin@gmail.com](mailto:roman.borodavkin@gmail.com)
- GitHub: [@barad1tos](https://github.com/barad1tos)

---

> **Warning:** Changes sync to iCloud immediately and cannot be easily reverted.
> Always use `--dry-run` first!
