# Genre Updater Daemon Service

LaunchAgent that watches the Music Library and runs the Python pipeline when it changes,
plus an hourly fallback tick.

## Layout

```
launchctl/                                  # In the repository
├── bin/
│   ├── common.sh                           # Shared layout + helpers (sourced, not executed)
│   ├── install.sh                          # Install / migrate / redeploy
│   ├── run-daemon.sh                       # Wrapper called by launchd
│   ├── update.sh                           # Switch to the latest release right away
│   └── notify.sh                           # macOS notifications
├── com.music.genreautoupdater.plist        # LaunchAgent TEMPLATE
└── SERVICE_README.md

~/.config/genreupdater/                     # User-owned, never overwritten by install.sh
├── my-config.yaml                          # Passed to Python via --config
├── .env                                    # Secrets (DISCOGS_TOKEN, CONTACT_EMAIL, ...), mode 600
└── artist-renames.yaml                     # Resolved relative to my-config.yaml

~/.local/share/genreupdater/
├── app/                                    # Clone pinned to the latest vX.Y.Z tag (detached HEAD)
│   └── .env -> ~/.config/genreupdater/.env
└── bin/                                    # Copies of launchctl/bin/*.sh deployed by install.sh

~/.local/state/genreupdater/
├── logs/                                   # daemon.log, stdout.log, stderr.log, launchctl-*.log
├── last_incremental_run.log, last_db_verify.log   # Python state referenced by my-config.yaml
└── run.lock                                # flock held by the running script (PID inside for diagnostics)

~/Library/LaunchAgents/com.music.genreautoupdater.plist    # Deployed plist ($HOME expanded)
```

`~/Library/Application Support/GenreUpdater/` belongs to the Swift app and is not used by the daemon.

### Why this layout

- **Config lives outside the clone.** Python accepts config files only from the working
  directory or `~/.config/` (`core_config._validate_config_path`), and it resolves symlinks first,
  so `~/.config/genreupdater/my-config.yaml` must be a regular file.
- **The daemon runs releases, not branches.** Merging into `main` changes nothing until a
  `vX.Y.Z` tag is pushed.
- **No dependency on a development checkout.** `apple_scripts_dir` must point inside the pinned
  clone, and no path may point into `~/Library/Application Support/GenreUpdater/`;
  `run-daemon.sh` refuses to run otherwise.

## Install or migrate

```bash
# From a repository checkout
./launchctl/bin/install.sh
```

The installer:

1. Refuses to continue while a daemon run holds a lock, then unloads the LaunchAgent
2. Creates the XDG directories
3. Migrates `my-config.yaml`, `.env` and `artist-renames.yaml` into `~/.config/genreupdater/`
   and copies legacy state files missing from `~/.local/state/genreupdater/` (copy only;
   existing files are kept). On every run it repoints `apple_scripts_dir` to the pinned clone
   and the Python state paths (`last_incremental_run_file`, `last_db_verify_log`) to
   `~/.local/state/genreupdater/` (also when the config came from the checkout's development
   settings), so re-running it repairs an existing config
4. Clones the repo into `~/.local/share/genreupdater/app` and checks out the latest release tag
5. Runs `uv sync --frozen --no-dev`, deploys the scripts and the plist, and loads the LaunchAgent

If any step after the unload fails, the installer reloads the LaunchAgent that was loaded before,
so a failed install never leaves the daemon switched off.

Re-run it after changing anything in `launchctl/`: deployed scripts are copies, not links.

## Releasing

```bash
git tag -a v3.0.1 -m "v3.0.1" origin/main
git push origin v3.0.1
```

The next daemon run fetches tags and switches to the highest stable `vX.Y.Z`. Pre-release tags
(`v3.1.0-rc1`) are ignored; deleting a tag on origin rolls the daemon back on its next run.

## Daemon flow

```
launchd trigger (Music Library change or hourly tick)
  → pre-flight: app clone, regular config file, .env, apple_scripts_dir inside the clone
  → lock (exit quietly if another run is active)
  → git fetch --tags, up to 13 attempts 5 s apart (still no network: run skipped)
  → checkout --force --detach <latest vX.Y.Z>
  → link app/.env → ~/.config/genreupdater/.env
  → uv sync --frozen --no-dev (one retry with a clean venv)
  → uv run python main.py --config ~/.config/genreupdater/my-config.yaml
  → notification (Glass on success, Basso on failure; none when the run is skipped)
```

Every infrastructure failure is logged and notified; none of them falls back silently.

A trigger right after boot or wake can fire before the network is up, so `git fetch` gets up to 13 attempts, 5 s apart, each capped at 30 s; every failed attempt appends git's output to `daemon.log` and logs its cause: git's first `fatal:` or `error:` line, `timed out after 30 s`, or `no output`. Without a network the attempts fail at once, so after about a minute with still no default route the run logs `No network after 13 fetch attempts; run skipped`, exits with `EX_TEMPFAIL` (75) and sends no notification, like a closed Music.app. The run is skipped rather than started on the deployed release because year lookups without network would fail and use up the albums' verification attempts. If all attempts fail while a default route exists, the run fails with a notification that carries the last cause. That includes a router that is up while its internet link is not, for example after a power cut when the router is back before its link: every run notifies until the internet is back.

When Music.app is not running, `main.py` exits with `EX_TEMPFAIL` (75). The wrapper logs
`Music.app is not running; run skipped` to `daemon.log` and sends no notification, since a closed
Music.app is a normal state for the hourly tick. Every other non-zero exit code is a failure.

## Commands

```bash
# Run now
launchctl kickstart -k "gui/$(id -u)/com.music.genreautoupdater"

# Dry run against the deployed release
cd ~/.local/share/genreupdater/app
uv run python main.py --config ~/.config/genreupdater/my-config.yaml --test-mode --dry-run

# Switch to the latest release immediately
~/.local/share/genreupdater/bin/update.sh

# Status
launchctl print "gui/$(id -u)/com.music.genreautoupdater" | head -20

# Stop / start
launchctl bootout "gui/$(id -u)/com.music.genreautoupdater"
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.music.genreautoupdater.plist
```

## Logs

| Log               | Content                                    |
|-------------------|--------------------------------------------|
| `daemon.log`      | Wrapper: release switches, sync, results   |
| `stdout.log`      | Python output (rotated at 50 MB)           |
| `stderr.log`      | Python errors (rotated at 50 MB)           |
| `launchctl-*.log` | launchd-level output                       |

All of them live in `~/.local/state/genreupdater/logs/`. Python's own logs, reports and caches
go to `logs_base_dir` from `my-config.yaml`.

```bash
tail -f ~/.local/state/genreupdater/logs/daemon.log
```

## Troubleshooting

**"Config must be a regular file"**: `~/.config/genreupdater/my-config.yaml` is missing or is a
symlink. Copy the real file there.

**"apple_scripts_dir is missing or outside the app clone"**: point it at an existing directory inside the clone, normally `~/.local/share/genreupdater/app/applescripts`.

**"No release tag found"**: push a `vX.Y.Z` tag (see Releasing).

**Missing environment variables**: check `~/.config/genreupdater/.env` and the
`app/.env` symlink (`ls -la ~/.local/share/genreupdater/app/.env`).

**Plist problems**: `plutil -lint ~/Library/LaunchAgents/com.music.genreautoupdater.plist`

## Rollback to the pre-XDG daemon

The installer keeps the original plist and never touches the legacy files:

```bash
launchctl bootout "gui/$(id -u)/com.music.genreautoupdater"
cp ~/.local/state/genreupdater/com.music.genreautoupdater.plist.pre-xdg \
   ~/Library/LaunchAgents/com.music.genreautoupdater.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.music.genreautoupdater.plist
```

This restores the old behavior only while the XDG change is not on `main`. The legacy daemon
resets its clone to `origin/main` on every run, and its `bin` is a link into that clone, so once the
change is merged the old plist starts the new `run-daemon.sh` in the old layout. For the same
reason, migrate a machine before its legacy daemon can pick up a `main` that contains this change.

After the merge, roll back a bad release instead: delete its tag on origin and the daemon checks out
the previous `vX.Y.Z` on its next run (`--prune-tags` drops the deleted tag locally).

## Changelog

### 2026-10-07

- **fix:** a trigger that fires before the network is up retries `git fetch` for about a minute and, if there is still no network, skips the run quietly instead of failing with "Git fetch failed"; a fetch that fails with a network present names the cause in the notification, and each attempt is capped at 30 s

### 2026-10-03

- **feat:** XDG layout (`~/.config`, `~/.local/share`, `~/.local/state`), separate from the
  Swift app's Application Support directory
- **feat:** the daemon deploys the latest stable release tag instead of `origin/main`
- **feat:** config and secrets live in `~/.config/genreupdater/`, passed via `--config`
- **feat:** pre-flight refuses an `apple_scripts_dir` outside the pinned clone
- **fix:** `uv sync` failures were reported as success (`if ! cmd; then rc=$?` always yields 0)
- **remove:** `sync-fixtures.sh`; pushes to the protected `main` branch were always rejected
- **docs:** rewritten for the new layout

### 2026-02-05

- **fix(sync):** sync-fixtures.sh pushes from the daemon's clone, not the development checkout
- **cleanup:** removed legacy `v2.0-daemon` worktree (replaced by `app/` in Application Support)

### 2025-12-26

- Initial SERVICE_README.md
