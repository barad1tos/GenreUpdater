#!/bin/bash
# run-daemon.sh - Main wrapper script for Genre Updater daemon
# Called by launchctl when Music Library changes
#
# Features:
# - PID-based locking (prevents concurrent runs)
# - Quick track count check (skips if no new tracks)
# - Auto git pull from origin/main
# - macOS notifications on failure
# - Comprehensive logging

set -euo pipefail

# === PATH setup for launchctl (uv is in ~/.local/bin) ===
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# === Configuration ===
SUPPORT_DIR="$HOME/Library/Application Support/GenreUpdater"
STATE_DIR="$SUPPORT_DIR/state"
LOGS_DIR="$SUPPORT_DIR/logs"
BIN_DIR="$SUPPORT_DIR/bin"

DAEMON_DIR="$SUPPORT_DIR/app"
CONFIG_SOURCE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python/Genres Autoupdater v2.0/my-config.yaml"

LOCK_FILE="$STATE_DIR/run.lock"
TOTAL_COUNT_FILE="$STATE_DIR/last_total_count"
MODIFIABLE_COUNT_FILE="$STATE_DIR/last_modifiable_count"
FORCE_RUN_FILE="$STATE_DIR/force_run"
DAEMON_LOG="$LOGS_DIR/daemon.log"

TIMEOUT_SECONDS=14400  # 4 hours

# === Logging ===
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$DAEMON_LOG"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "$DAEMON_LOG" >&2
}

notify() {
    local title="$1"
    local message="$2"
    local sound="${3:-Basso}"
    "$BIN_DIR/notify.sh" "$title" "$message" "$sound" 2>/dev/null || true
}

# === Pre-flight checks ===
log "=== Genre Updater Daemon Started ==="

# Ensure directories exist
mkdir -p "$STATE_DIR" "$LOGS_DIR"

# Check if daemon directory exists
if [[ ! -d "$DAEMON_DIR" ]]; then
    log_error "Daemon directory not found: $DAEMON_DIR"
    notify "Genre Updater Error" "Daemon directory not found"
    exit 1
fi

# === Lock acquisition (macOS-compatible PID-based) ===
acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$LOCK_FILE" 2>/dev/null)
        if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
            log "Another instance is already running (PID: $old_pid). Exiting."
            exit 0
        else
            log "Removing stale lock (old PID: ${old_pid:-empty})"
            rm -f "$LOCK_FILE"
        fi
    fi
    echo $$ > "$LOCK_FILE"
    trap 'rm -f "$LOCK_FILE"' EXIT
}

acquire_lock
log "Lock acquired (PID: $$)"

# === Hybrid track count check (BEFORE git/uv for speed) ===
# Step 1: Quick total count (0.4s) - exit early if unchanged
# Step 2: Slow modifiable count (27s) - only when total changed
quick_track_count_check() {
    # Check for force run override
    if [[ -f "$FORCE_RUN_FILE" ]]; then
        log "Force run requested. Skipping count check."
        rm -f "$FORCE_RUN_FILE"
        return 0
    fi

    # Migration: remove old state file (one-time cleanup)
    rm -f "$STATE_DIR/last_track_count"

    # Step 1: Quick total count check (~0.4s)
    local current_total
    if ! current_total=$(osascript -e 'tell application "Music" to count of tracks' 2>/dev/null); then
        log "Could not get track count (Music.app not running?). Proceeding anyway."
        return 0
    fi

    local last_total
    last_total=$(cat "$TOTAL_COUNT_FILE" 2>/dev/null || echo "0")

    if [[ "$current_total" == "$last_total" ]]; then
        log "Total track count unchanged ($current_total). No new tracks. Exiting."
        exit 0
    fi

    log "Total track count changed: $last_total → $current_total"

    # Step 2: Slow modifiable count check (~27s) - only when total changed
    log "Checking modifiable track count..."
    local current_modifiable
    local script_path="$DAEMON_DIR/applescripts/count_modifiable_tracks.applescript"

    if [[ ! -f "$script_path" ]]; then
        log "Modifiable count script not found. Proceeding with pipeline."
        echo "$current_total" > "$TOTAL_COUNT_FILE"
        return 0
    fi

    if ! current_modifiable=$(osascript "$script_path" 2>/dev/null); then
        log "Could not get modifiable count. Proceeding with pipeline."
        echo "$current_total" > "$TOTAL_COUNT_FILE"
        return 0
    fi

    # Check for error response from script
    if [[ "$current_modifiable" == ERROR:* ]]; then
        log "Modifiable count script error: $current_modifiable. Proceeding with pipeline."
        echo "$current_total" > "$TOTAL_COUNT_FILE"
        return 0
    fi

    local last_modifiable
    last_modifiable=$(cat "$MODIFIABLE_COUNT_FILE" 2>/dev/null || echo "0")

    if [[ "$current_modifiable" == "$last_modifiable" ]]; then
        log "Modifiable count unchanged ($current_modifiable). Only non-modifiable tracks changed. Skipping."
        echo "$current_total" > "$TOTAL_COUNT_FILE"
        exit 0
    fi

    log "Modifiable track count changed: $last_modifiable → $current_modifiable"
    return 0
}

quick_track_count_check

# === Git update ===
log "Updating from git..."
cd "$DAEMON_DIR"

# === Symlink config and .env EARLY (before any Python execution) ===
ENV_SOURCE="${CONFIG_SOURCE%/*}/.env"
if [[ ! -f "$DAEMON_DIR/my-config.yaml" ]] && [[ -f "$CONFIG_SOURCE" ]]; then
    log "Creating config symlink..."
    ln -sf "$CONFIG_SOURCE" "$DAEMON_DIR/my-config.yaml"
fi

if [[ ! -f "$DAEMON_DIR/.env" ]] && [[ -f "$ENV_SOURCE" ]]; then
    log "Creating .env symlink..."
    ln -sf "$ENV_SOURCE" "$DAEMON_DIR/.env"
fi

# Verify we're on main
current_branch=$(git branch --show-current)
if [[ "$current_branch" != "main" ]]; then
    log_error "Wrong branch: $current_branch (expected: main)"
    notify "Genre Updater Error" "Daemon is on wrong branch: $current_branch"
    exit 1
fi

# Fetch and reset to origin/main
if ! git fetch origin main 2>&1 | tee -a "$DAEMON_LOG"; then
    log_error "Git fetch failed"
    notify "Genre Updater Error" "Git fetch failed"
    exit 1
fi

git reset --hard origin/main 2>&1 | tee -a "$DAEMON_LOG"
log "Git update complete"

# === Dependency sync ===
log "Syncing dependencies..."

# Function to sync with auto-recovery
# Note: Using if-block instead of &&/|| to avoid set -e edge cases
sync_dependencies() {
    local sync_output
    local sync_exit

    # First attempt (if-block pattern is safer with set -e)
    sync_exit=0
    if ! sync_output=$(uv sync --frozen 2>&1); then
        sync_exit=$?
    fi
    echo "$sync_output" >> "$DAEMON_LOG"

    if [[ $sync_exit -eq 0 ]]; then
        return 0
    fi

    # First failure - try cleaning venv and retrying
    # Safe to rm -rf here: this is daemon's isolated clone, not user's dev env
    log "First sync failed (exit $sync_exit), cleaning venv and retrying..."
    rm -rf "$DAEMON_DIR/.venv" "$DAEMON_DIR/src/music_genre_updater.egg-info"

    sync_exit=0
    if ! sync_output=$(uv sync --frozen 2>&1); then
        sync_exit=$?
    fi
    echo "$sync_output" >> "$DAEMON_LOG"

    if [[ $sync_exit -eq 0 ]]; then
        log "Sync succeeded after venv cleanup"
        return 0
    fi

    # Second failure - fatal
    log "Second sync also failed (exit $sync_exit)"
    return 1
}

if ! sync_dependencies; then
    log_error "Dependency sync failed even after venv cleanup"
    notify "Genre Updater Error" "uv sync failed"
    exit 1
fi
log "Dependencies synced"

# === Execute main script ===
log "Starting main pipeline..."
EXIT_CODE=0

# Get current counts for saving after success
CURRENT_TOTAL=$(osascript -e 'tell application "Music" to count of tracks' 2>/dev/null || echo "")
MODIFIABLE_SCRIPT="$DAEMON_DIR/applescripts/count_modifiable_tracks.applescript"
if [[ -f "$MODIFIABLE_SCRIPT" ]]; then
    CURRENT_MODIFIABLE=$(osascript "$MODIFIABLE_SCRIPT" 2>/dev/null || echo "")
else
    CURRENT_MODIFIABLE=""
fi

if timeout "$TIMEOUT_SECONDS" uv run python main.py \
    >> "$LOGS_DIR/stdout.log" 2>> "$LOGS_DIR/stderr.log"; then
    log "Main pipeline completed successfully"

    # Update both counts (for next run's hybrid check)
    if [[ -n "$CURRENT_TOTAL" ]]; then
        echo "$CURRENT_TOTAL" > "$TOTAL_COUNT_FILE"
        log "Total count saved: $CURRENT_TOTAL"
    fi
    if [[ -n "$CURRENT_MODIFIABLE" && "$CURRENT_MODIFIABLE" != ERROR:* ]]; then
        echo "$CURRENT_MODIFIABLE" > "$MODIFIABLE_COUNT_FILE"
        log "Modifiable count saved: $CURRENT_MODIFIABLE"
    fi

    notify "Genre Updater" "Update completed successfully" "Glass"

    # Sync snapshot to repo for regression tests
    if [[ -x "$BIN_DIR/sync-fixtures.sh" ]]; then
        log "Syncing snapshot to repo..."
        if "$BIN_DIR/sync-fixtures.sh" >> "$DAEMON_LOG" 2>&1; then
            log "Snapshot synced successfully"
        else
            log "Snapshot sync failed (non-fatal)"
        fi
    fi
else
    EXIT_CODE=$?
    if [[ $EXIT_CODE -eq 124 ]]; then
        log_error "Script timed out after ${TIMEOUT_SECONDS}s"
        notify "Genre Updater Error" "Script timed out after 4 hours"
    else
        log_error "Script failed with exit code: $EXIT_CODE"
        # Get last few lines of stderr for notification
        error_snippet=$(tail -3 "$LOGS_DIR/stderr.log" 2>/dev/null | tr '\n' ' ' | cut -c1-100)
        notify "Genre Updater Error" "Exit code $EXIT_CODE: $error_snippet"
    fi
fi

log "=== Genre Updater Daemon Finished (exit: $EXIT_CODE) ==="
exit $EXIT_CODE
