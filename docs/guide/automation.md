# Automation

Run Music Genre Updater automatically using macOS LaunchAgents.

## Overview

The recommended approach is a **daemon** that monitors your Music library and applies updates incrementally as tracks are added or modified.

## LaunchAgent Setup

### 1. Create the Daemon App

Copy the project to a stable location:

```bash
cp -r /path/to/project ~/Library/Application\ Support/GenreUpdater/app
```

### 2. Create LaunchAgent Plist

Create `~/Library/LaunchAgents/com.music.genreautoupdater.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.music.genreautoupdater</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/uv</string>
        <string>run</string>
        <string>python</string>
        <string>main.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/Library/Application Support/GenreUpdater/app</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>StartInterval</key>
    <integer>900</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/tmp/genreupdater.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/genreupdater.err</string>
</dict>
</plist>
```

### 3. Load the Agent

```bash
launchctl load ~/Library/LaunchAgents/com.music.genreautoupdater.plist
```

### 4. Verify It's Running

```bash
launchctl list | grep genreautoupdater
```

## Configuration for Automation

Optimize `my-config.yaml` for background operation:

```yaml
# Enable incremental updates (only process recent changes)
incremental_interval_minutes: 15

# Enable snapshot caching for fast startup
caching:
  library_snapshot:
    enabled: true
    delta_enabled: true
    max_age_hours: 24

# Reduce logging verbosity
logging:
  levels:
    console: WARNING
    main_file: INFO

# Disable analytics for daemon runs
analytics:
  enabled: false
```

## Scheduling Options

### Fixed Interval

Run every N seconds:

```xml
<key>StartInterval</key>
<integer>900</integer>  <!-- 15 minutes -->
```

### Calendar-Based

Run at specific times:

```xml
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key>
    <integer>3</integer>
    <key>Minute</key>
    <integer>0</integer>
</dict>
```

### On File Change (Advanced)

Watch for Music library changes:

```xml
<key>WatchPaths</key>
<array>
    <string>/Users/YOUR_USERNAME/Music/Music/Music Library.musiclibrary</string>
</array>
```

## Managing the Daemon

### Stop

```bash
launchctl unload ~/Library/LaunchAgents/com.music.genreautoupdater.plist
```

### Restart

```bash
launchctl unload ~/Library/LaunchAgents/com.music.genreautoupdater.plist
launchctl load ~/Library/LaunchAgents/com.music.genreautoupdater.plist
```

### Check Logs

```bash
tail -f /tmp/genreupdater.log
tail -f /tmp/genreupdater.err
```

### Force Run Now

```bash
launchctl start com.music.genreautoupdater
```

## Troubleshooting

### Agent Not Starting

1. Check plist syntax:
   ```bash
   plutil -lint ~/Library/LaunchAgents/com.music.genreautoupdater.plist
   ```

2. Check permissions:
   ```bash
   ls -la ~/Library/LaunchAgents/com.music.genreautoupdater.plist
   ```

3. View system logs:
   ```bash
   log show --predicate 'subsystem == "com.apple.xpc.launchd"' --last 5m
   ```

### Music.app Not Running

The daemon requires Music.app to be running. Add a check script:

```bash
#!/bin/bash
if pgrep -x "Music" > /dev/null; then
    cd ~/Library/Application\ Support/GenreUpdater/app
    uv run python main.py
fi
```

### Memory Usage

For large libraries, enable garbage collection tuning:

```yaml
caching:
  cleanup_interval_seconds: 300
```

## Alternative: Cron

For simpler scheduling, use cron instead:

```bash
crontab -e
```

Add:
```
0 * * * * cd ~/Library/Application\ Support/GenreUpdater/app && /usr/local/bin/uv run python main.py >> /tmp/genreupdater.log 2>&1
```

This runs hourly.
