# Genre Updater Daemon Service

Automated daemon that monitors your Music Library and updates album years when changes are detected.

## File Structure

```
launchctl/                              # In git repo (version controlled)
├── bin/
│   ├── install.sh                      # Deploys everything
│   ├── run-daemon.sh                   # Main wrapper script
│   ├── update.sh                       # Manual git pull trigger
│   ├── notify.sh                       # macOS notifications
│   └── sync-fixtures.sh                # Sync library snapshot to repo
├── com.music.genreautoupdater.plist    # TEMPLATE (edit this!)
└── SERVICE_README.md

~/Library/Application Support/GenreUpdater/   # LOCAL installation
├── app/                                # Git clone of repo (main branch)
│   └── [full repo clone]
├── state/
│   └── run.lock                        # PID lock file
├── logs/
│   ├── daemon.log
│   ├── stdout.log
│   ├── stderr.log
│   └── launchctl-*.log
└── bin/                                # Deployed copies of scripts
    └── *.sh

~/Library/LaunchAgents/
└── com.music.genreautoupdater.plist    # Deployed plist (with $HOME expanded)
```

## Architecture

The daemon is a **thin infrastructure wrapper**. All business logic is in Python.

```
┌─────────────────────────────────────────────────┐
│  Python + AppleScript = Self-sufficient system  │
│  Decides what to process, handles all logic     │
└─────────────────────────────────────────────────┘
                      ↑
                      │ just runs
┌─────────────────────────────────────────────────┐
│  Daemon = Infrastructure only                   │
│  Lock → Git pull → uv sync → Run Python         │
└─────────────────────────────────────────────────┘
```

### Why This Structure?

- **Development (v2.0/)**: Work on `dev` branch in iCloud, syncs across devices
- **Daemon (app/)**: Isolated git clone in Application Support, always on `main`
- **Isolation**: Daemon code is NOT synced via iCloud (prevents race conditions)
- **Auto-update**: Daemon pulls from `origin/main` on each trigger

## Quick Start

### First-Time Setup

```bash
# Just run the installer - it handles everything
cd "~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python/Genres Autoupdater v2.0"
./launchctl/bin/install.sh
```

The installer automatically:

1. Clones the repo to `~/Library/Application Support/GenreUpdater/app/`
2. Copies scripts to `bin/`
3. Creates config symlinks (see [Config Symlinks](#config-symlinks))
4. Deploys the LaunchAgent plist
5. Loads the service

### Verify Installation

```bash
# Check if service is loaded
launchctl list | grep genreautoupdater

# View logs
tail -f ~/Library/Application\ Support/GenreUpdater/logs/daemon.log
```

## Configuration

### Edit Plist Template (Recommended)

Edit `launchctl/com.music.genreautoupdater.plist` in the repo:

```xml
<!-- Change throttle interval (minimum between triggers) -->
<key>ThrottleInterval</key>
<integer>300</integer>  <!-- 5 minutes -->

        <!-- Change CPU priority (0=normal, 10=low) -->
<key>Nice</key>
<integer>10</integer>

        <!-- Change timeout -->
<key>ExitTimeOut</key>
<integer>14400</integer>  <!-- 4 hours -->
```

Then redeploy:

```bash
./launchctl/bin/install.sh
```

### Edit Wrapper Script

For runtime settings, edit `launchctl/bin/run-daemon.sh`:

```bash
TIMEOUT_SECONDS=14400   # 4 hour max runtime
```

Then redeploy:

```bash
./launchctl/bin/install.sh
```

### Music Library Path

Edit the plist template, find WatchPaths:

```xml
<key>WatchPaths</key>
<array>
<string>$HOME/Music/Music/Music Library.musiclibrary</string>
</array>
```

## Environment Variables

Sync scripts support environment variable overrides for non-standard setups:

| Variable       | Default                                                          | Used By                                    |
|----------------|------------------------------------------------------------------|--------------------------------------------|
| `MGU_LOGS_DIR` | `~/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs` | `sync-fixtures.sh`, `sync-diagnostics.sh`  |
| `MGU_REPO_DIR` | `~/Library/Application Support/GenreUpdater/app`                 | `sync-fixtures.sh`, `sync-diagnostics.sh`  |

These variables allow sync scripts to locate the library snapshot cache and the daemon's git clone
without hardcoding paths. In most setups the defaults are correct and no override is needed.

Example override:

```bash
MGU_LOGS_DIR="/custom/logs/path" MGU_REPO_DIR="/custom/repo" ./sync-fixtures.sh
```

## Commands

| Script             | Description                                           |
|--------------------|-------------------------------------------------------|
| `install.sh`       | Deploy scripts + plist, create symlinks, load service |
| `run-daemon.sh`    | Main wrapper (called by launchctl)                    |
| `update.sh`        | Manually pull latest changes from main                |
| `notify.sh`        | macOS notification helper                             |
| `sync-fixtures.sh` | Sync library snapshot to repo via daemon's clone      |

### Manual Operations

```bash
# Trigger daemon manually
launchctl kickstart -k gui/$(id -u)/com.music.genreautoupdater

# Manual run without kickstart
~/Library/Application\ Support/GenreUpdater/bin/run-daemon.sh

# Check status
launchctl list | grep genreautoupdater

# Stop service
launchctl unload ~/Library/LaunchAgents/com.music.genreautoupdater.plist

# Start service
launchctl load ~/Library/LaunchAgents/com.music.genreautoupdater.plist

# Uninstall completely
launchctl unload ~/Library/LaunchAgents/com.music.genreautoupdater.plist
rm ~/Library/LaunchAgents/com.music.genreautoupdater.plist
rm -rf ~/Library/Application\ Support/GenreUpdater
```

## Trigger Behavior

### WatchPaths Trigger

The daemon watches:

```
~/Music/Music/Music Library.musiclibrary
```

Any change to this file triggers the daemon.

### Daemon Flow

```
┌────────────────────────────────────────────┐
│  Music Library Changed                     │
│           │                                │
│           ▼                                │
│  ┌─────────────────────┐                   │
│  │ Already running?    │──Yes──▶ Exit      │
│  └────────┬────────────┘                   │
│           │ No                             │
│           ▼                                │
│  ┌────────────────────┐                    │
│  │  Symlink config    │                    │
│  │  + .env            │                    │
│  └────────┬───────────┘                    │
│           │                                │
│           ▼                                │
│  ┌────────────────────┐                    │
│  │  git pull          │                    │
│  │  uv sync           │                    │
│  │  run Python        │                    │
│  └────────┬───────────┘                    │
│           │                                │
│           ▼                                │
│  Python decides what to process            │
│  (uses snapshot cache for speed)           │
│           │                                │
│           ▼                                │
│  ┌────────────────────────────┐            │
│  │  On success:               │            │
│  │  1. Notify (Glass sound)   │            │
│  │  2. sync-fixtures.sh       │            │
│  │     (push snapshot to git) │            │
│  └────────────────────────────┘            │
│           │                                │
│  ┌────────────────────────────┐            │
│  │  On failure:               │            │
│  │  1. Notify (Basso sound)   │            │
│  │  2. Log error details      │            │
│  └────────────────────────────┘            │
└────────────────────────────────────────────┘
```

**Timings:**

- LaunchAgent ThrottleInterval: 5 minutes (minimum between triggers)
- Python startup and quick exit if nothing to do: ~3–4 seconds
- Max runtime: 4 hours (timeout)

## Config Symlinks

`run-daemon.sh` creates symlinks from the DEV REPO (iCloud) into the daemon's clone so that
the daemon uses the same user config and secrets as the development environment.

| Symlink Target (daemon's clone) | Source (DEV REPO in iCloud) |
|---------------------------------|-----------------------------|
| `app/my-config.yaml`            | `v2.0/my-config.yaml`       |
| `app/.env`                      | `v2.0/.env`                 |

The symlinks are created **before** any Python execution and only if they don't already exist.
Both `install.sh` and `run-daemon.sh` handle symlink creation, so they are restored automatically
even if someone deletes the daemon's clone and re-runs the installer.

**Why symlinks?**

- `my-config.yaml` contains user-specific paths and API key references
- `.env` contains encrypted API keys
- Both are gitignored, so the daemon's `git reset --hard` would delete real copies
- Symlinks survive `git reset --hard` because git doesn't track them

## Sync Scripts

### sync-fixtures.sh

Copies the library snapshot from the logs/cache directory into `tests/fixtures/` and pushes the
commit through the **daemon's git clone** (not the DEV REPO in iCloud).

**Flow:**

1. Finds snapshot at `$MGU_LOGS_DIR/cache/library_snapshot.json`
2. Compares with existing `tests/fixtures/library_snapshot.json`
3. If changed: copies, commits with `[skip ci]` tag, pushes to current branch
4. Safety: only pushes from `main` or `dev` branch

**Called by:** `run-daemon.sh` (after successful pipeline run, non-fatal on failure)

**Important:** The script pushes via the daemon's clone (`~/Library/Application Support/GenreUpdater/app/`),
not the DEV REPO in iCloud. This prevents git conflicts with the developer's working directory.

## Development Workflow

```
┌─────────────────┐     PR/MR    ┌────────────────────┐
│   dev branch    │ ───────────► │   main branch      │
│   (v2.0/)       │              │   (app/)           │
└─────────────────┘              └────────────────────┘
        │                                  │
        ▼                                  ▼
   You develop                     Daemon auto-pulls
   in iCloud                       from origin/main
```

1. Work in `v2.0/` on any branch
2. Push changes, create PR to `main`
3. Merge PR on GitHub
4. Daemon automatically pulls changes on next trigger

### Manual Update

If you don't want to wait for a trigger:

```bash
~/Library/Application\ Support/GenreUpdater/bin/update.sh
```

## Multi-Machine Support

Each machine has:

- Shared dev code via iCloud (v2.0/)
- Independent daemon clone (app/) - NOT synced
- Local state (lock, logs) – NOT synced

This means:

- Each machine can run daemon independently
- No conflict between machines

## Logs

| Log             | Location                                           | Content             |
|-----------------|----------------------------------------------------|---------------------|
| daemon.log      | `~/Library/Application Support/GenreUpdater/logs/` | Wrapper script logs |
| stdout.log      | `~/Library/Application Support/GenreUpdater/logs/` | Script output       |
| stderr.log      | `~/Library/Application Support/GenreUpdater/logs/` | Script errors       |
| launchctl-*.log | `~/Library/Application Support/GenreUpdater/logs/` | LaunchAgent logs    |

### View Logs

```bash
# Real-time daemon log
tail -f ~/Library/Application\ Support/GenreUpdater/logs/daemon.log

# Last run output
cat ~/Library/Application\ Support/GenreUpdater/logs/stdout.log

# Errors
cat ~/Library/Application\ Support/GenreUpdater/logs/stderr.log
```

## Troubleshooting

### Service Not Starting

```bash
# Check if loaded
launchctl list | grep genreautoupdater

# Check plist syntax
plutil ~/Library/LaunchAgents/com.music.genreautoupdater.plist

# Reload
launchctl unload ~/Library/LaunchAgents/com.music.genreautoupdater.plist
launchctl load ~/Library/LaunchAgents/com.music.genreautoupdater.plist
```

### Git Pull Fails

```bash
# Check daemon directory
cd ~/Library/Application\ Support/GenreUpdater/app
git status
git remote -v

# Manual fix
git fetch origin main
git reset --hard origin/main
```

### Missing Environment Variables

If you see "Missing required environment variables" error:

```bash
# Check .env symlink
ls -la ~/Library/Application\ Support/GenreUpdater/app/.env

# If missing, recreate manually or re-run daemon script
# (it creates symlinks automatically now)
```

### Sync Scripts Failing

```bash
# Check if snapshot exists
ls -la ~/Library/Mobile\ Documents/com~apple~CloudDocs/4.\ Dev/MGU\ logs/cache/library_snapshot.json

# Check daemon's git status (sync pushes from here, not DEV REPO)
cd ~/Library/Application\ Support/GenreUpdater/app
git status
git log -3 --oneline

# Manual sync (from daemon's clone)
~/Library/Application\ Support/GenreUpdater/bin/sync-fixtures.sh
```

## Changelog

### 2026-02-05

- **fix(sync):** Redirected sync-fixtures.sh to push from daemon's clone, not the DEV REPO in iCloud
- **cleanup:** Removed legacy `v2.0-daemon` worktree (replaced by `app/` in Application Support)
- **cleanup:** Pruned stale git worktree references
- **docs:** Added Config Symlinks, Sync Scripts, Environment Variables sections

### 2025-12-26

- Initial SERVICE_README.md

---

**Status:** Production-ready
**Last update:** 2026-02-05
