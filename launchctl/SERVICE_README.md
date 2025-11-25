# Genre Updater Daemon Service

Automated daemon that monitors your Music Library and updates album years when changes are detected.

## Architecture

```
iCloud/.../Genres Autoupdater v2.0/           # DEV (your development work)
iCloud/.../Genres Autoupdater v2.0-daemon/    # DAEMON (production, main branch)

~/Library/Application Support/GenreUpdater/   # STATE (local, not synced)
├── state/
│   ├── last_run.timestamp
│   ├── run.lock
│   └── cooldown_override
├── logs/
│   ├── daemon.log
│   ├── stdout.log
│   └── stderr.log
└── bin/
    ├── run-daemon.sh
    ├── install.sh
    ├── update.sh
    └── notify.sh
```

### Why Two Directories?

- **Development (v2.0/)**: Work on `dev` branch freely
- **Daemon (v2.0-daemon/)**: Always on `main` branch, runs automatically
- Both sync via iCloud, but are independent git clones
- State files are local (not synced) to avoid iCloud conflicts

## Quick Start

### First-Time Setup

```bash
# 1. Clone the daemon repo (if not done)
cd "~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python"
git clone https://github.com/YOUR_USER/GenreUpdater.git "Genres Autoupdater v2.0-daemon"

# 2. Set up daemon venv
cd "Genres Autoupdater v2.0-daemon"
git checkout main
uv venv --python 3.13
uv sync

# 3. Run the installer
~/Library/Application\ Support/GenreUpdater/bin/install.sh
```

### Verify Installation

```bash
# Check if service is loaded
launchctl list | grep genreautoupdater

# View logs
tail -f ~/Library/Application\ Support/GenreUpdater/logs/daemon.log
```

## Commands

| Command | Description |
|---------|-------------|
| `install.sh` | One-time setup, generates plist, loads service |
| `update.sh` | Manually pull latest changes from main |
| `run-daemon.sh` | Main wrapper (called by launchctl) |
| `notify.sh` | Notification helper |

### Manual Operations

```bash
# Force run now (ignoring cooldown)
touch ~/Library/Application\ Support/GenreUpdater/state/cooldown_override
launchctl kickstart -k gui/$(id -u)/com.music.genreautoupdater

# Manual run without kickstart
~/Library/Application\ Support/GenreUpdater/bin/run-daemon.sh

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

### Cooldown Logic

```
┌──────────────────────────────────────────────────────┐
│  Music Library Changed                               │
│           │                                          │
│           ▼                                          │
│  ┌─────────────────┐                                 │
│  │ Already running? │──Yes──▶ Exit (flock)          │
│  └────────┬────────┘                                 │
│           │ No                                       │
│           ▼                                          │
│  ┌─────────────────┐                                 │
│  │ Cooldown active? │──Yes──▶ Exit (2h not passed)  │
│  └────────┬────────┘                                 │
│           │ No (or override exists)                  │
│           ▼                                          │
│  ┌─────────────────┐                                 │
│  │   git pull      │                                 │
│  │   uv sync       │                                 │
│  │   run script    │                                 │
│  └────────┬────────┘                                 │
│           │                                          │
│           ▼                                          │
│  Update timestamp / Send notification                │
└──────────────────────────────────────────────────────┘
```

**Timings:**
- LaunchAgent ThrottleInterval: 5 minutes (minimum between triggers)
- Wrapper cooldown: 2 hours (minimum between actual runs)
- Max runtime: 4 hours (timeout)

## Development Workflow

```
┌─────────────────┐     PR/MR      ┌─────────────────┐
│   dev branch    │ ───────────►   │   main branch   │
│   (v2.0/)       │                │   (v2.0-daemon/)│
└─────────────────┘                └─────────────────┘
        │                                  │
        ▼                                  ▼
   You develop                     Daemon auto-pulls
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
- Shared code via iCloud (both v2.0/ and v2.0-daemon/)
- Local state (lock, timestamp, logs) - NOT synced

This means:
- Each machine has independent cooldown
- Each machine can run daemon independently
- No conflict between machines

## Logs

| Log | Location | Content |
|-----|----------|---------|
| daemon.log | `~/Library/Application Support/GenreUpdater/logs/` | Wrapper script logs |
| stdout.log | `~/Library/Application Support/GenreUpdater/logs/` | Script output |
| stderr.log | `~/Library/Application Support/GenreUpdater/logs/` | Script errors |
| launchctl-*.log | `~/Library/Application Support/GenreUpdater/logs/` | LaunchAgent logs |

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

### Script Not Running on Library Changes

```bash
# Verify WatchPaths target exists
ls -la ~/Music/Music/Music\ Library.musiclibrary

# Check daemon.log for cooldown messages
grep -i cooldown ~/Library/Application\ Support/GenreUpdater/logs/daemon.log
```

### Git Pull Fails

```bash
# Check daemon directory
cd "~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python/Genres Autoupdater v2.0-daemon"
git status
git remote -v

# Manual fix
git fetch origin main
git reset --hard origin/main
```

### iCloud Sync Conflicts

```bash
# Find conflict files
find "~/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python" -name "* 2" -o -name "* 2.*"

# Remove them (they're duplicates)
find ... -exec rm -rf {} +
```

## Configuration

### Cooldown Time

Edit `~/Library/Application Support/GenreUpdater/bin/run-daemon.sh`:

```bash
COOLDOWN_SECONDS=7200  # 2 hours (default)
```

### Timeout

```bash
TIMEOUT_SECONDS=14400  # 4 hours (default)
```

### Different Music Library Path

Re-run `install.sh` and enter the correct path when prompted.

---

**Status:** Production-ready
**Last update:** 2025-01-25
