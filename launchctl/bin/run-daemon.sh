#!/bin/bash
# run-daemon.sh - Thin infrastructure wrapper for Genre Updater daemon
# Called by launchctl when Music Library changes
#
# Responsibilities (infrastructure only):
# - PID-based locking (prevents concurrent runs)
# - Git pull from origin/main
# - Dependency sync (uv)
# - Execute Python pipeline
# - Notifications on success/failure
#
# Business logic is handled entirely by Python.

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
sync_dependencies() {
    local sync_output
    local sync_exit

    # First attempt
    sync_exit=0
    if ! sync_output=$(uv sync --frozen 2>&1); then
        sync_exit=$?
    fi
    echo "$sync_output" >> "$DAEMON_LOG"

    if [[ $sync_exit -eq 0 ]]; then
        return 0
    fi

    # First failure - try cleaning venv and retrying
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

if timeout "$TIMEOUT_SECONDS" uv run python main.py \
    >> "$LOGS_DIR/stdout.log" 2>> "$LOGS_DIR/stderr.log"; then
    log "Main pipeline completed successfully"
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
        error_snippet=$(tail -3 "$LOGS_DIR/stderr.log" 2>/dev/null | tr '\n' ' ' | cut -c1-100)
        notify "Genre Updater Error" "Exit code $EXIT_CODE: $error_snippet"
    fi
fi

log "=== Genre Updater Daemon Finished (exit: $EXIT_CODE) ==="
exit $EXIT_CODE
