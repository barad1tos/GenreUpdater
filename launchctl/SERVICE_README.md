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
3. Deploys the LaunchAgent plist
4. Loads the service

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

## Commands

| Script             | Description                             |
|--------------------|-----------------------------------------|
| `install.sh`       | Deploy scripts + plist, load service    |
| `run-daemon.sh`    | Main wrapper (called by launchctl)      |
| `update.sh`        | Manually pull latest changes from main  |
| `notify.sh`        | macOS notification helper               |
| `sync-fixtures.sh` | Sync library snapshot to repo for tests |

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
│  Send notification                         │
└────────────────────────────────────────────┘
```

**Timings:**

- LaunchAgent ThrottleInterval: 5 minutes (minimum between triggers)
- Python startup + quick exit if nothing to do: ~3-4 seconds
- Max runtime: 4 hours (timeout)

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
- Local state (lock, logs) - NOT synced

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

---

**Status:** Production-ready
**Last update:** 2025-12-26
