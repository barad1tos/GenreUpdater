#!/bin/bash
# run-daemon.sh - Thin infrastructure wrapper for the Genre Updater daemon
# Called by launchd on Music Library changes and on an hourly tick.
#
# Responsibilities (infrastructure only):
# - PID-based locking (prevents concurrent runs)
# - Pin the app clone to the latest stable release tag (vX.Y.Z)
# - Dependency sync (uv)
# - Run the Python pipeline with the user config from ~/.config/genreupdater
# - Notifications on success/failure (none when a run is skipped: another run holds the lock,
#   Music.app is closed, or there is no network)
#
# Business logic is handled entirely by Python. The layout is defined in common.sh.

set -euo pipefail

# === PATH setup for launchd (uv is in ~/.local/bin, route in /sbin) ===
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=SCRIPTDIR/common.sh
source "$SCRIPT_DIR/common.sh"

DAEMON_LOG="$GU_LOGS_DIR/daemon.log"
TIMEOUT_SECONDS=14400  # 4 hours
FETCH_ATTEMPTS=13      # 12 waits of 5 s: about a minute when each attempt fails at once
FETCH_TIMEOUT=30       # seconds per attempt, so a stalled transfer cannot hold the lock indefinitely

# === Logging and notifications ===
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$DAEMON_LOG"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "$DAEMON_LOG" >&2
}

notify() {
    "$SCRIPT_DIR/notify.sh" "$1" "$2" "${3:-Basso}" 2>/dev/null || true
}

# Log, notify and stop: every infrastructure failure is loud
fail() {
    log_error "$1"
    notify "Genre Updater Error" "${2:-$1}"
    exit 1
}

mkdir -p "$GU_STATE_DIR" "$GU_LOGS_DIR"
log "=== Genre Updater Daemon Started ==="

# === Log rotation (prevent unbounded growth) ===
# Rotate stdout.log/stderr.log if they exceed 50 MB; keep one previous copy.
rotate_log() {
    local log_file="$1"
    local max_bytes=$((50 * 1024 * 1024))
    if [[ -f "$log_file" ]]; then
        local size
        size=$(stat -f%z "$log_file" 2>/dev/null || echo 0)
        if (( size > max_bytes )); then
            mv -f "$log_file" "${log_file}.prev"
            : > "$log_file"
            log "Rotated $(basename "$log_file") (was ${size} bytes)"
        fi
    fi
}
rotate_log "$GU_LOGS_DIR/stdout.log"
rotate_log "$GU_LOGS_DIR/stderr.log"

# === Pre-flight checks ===
[[ -d "$GU_APP_DIR/.git" ]] || fail "App clone not found: $GU_APP_DIR (run install.sh)" "App clone not found"

# Python resolves symlinks and accepts config only under ~/.config, so a symlinked config would be rejected
if [[ ! -f "$GU_CONFIG_FILE" || -L "$GU_CONFIG_FILE" ]]; then
    fail "Config must be a regular file: $GU_CONFIG_FILE" "Config file missing"
fi
[[ -f "$GU_ENV_FILE" ]] || fail "Secrets file not found: $GU_ENV_FILE" ".env file missing"

# AppleScripts must come from the pinned clone, never from a development checkout
scripts_dir="$(awk -F': *' '/^apple_scripts_dir:/ { print $2; exit }' "$GU_CONFIG_FILE")"
scripts_dir="${scripts_dir%%[[:space:]]#*}"  # inline comment
scripts_dir="${scripts_dir%"${scripts_dir##*[![:space:]]}"}"  # trailing whitespace
scripts_dir="${scripts_dir#[\"\']}"
scripts_dir="${scripts_dir%[\"\']}"
scripts_dir="${scripts_dir/#\~/$HOME}"
# Compare canonical paths, so ".." components and symlinks cannot lead out of the clone
scripts_dir_real="$(cd "$scripts_dir" 2>/dev/null && pwd -P)" || scripts_dir_real=""
app_dir_real="$(cd "$GU_APP_DIR" && pwd -P)"
if [[ "$scripts_dir_real" != "$app_dir_real"/* ]]; then
    fail "apple_scripts_dir must be an existing directory inside $GU_APP_DIR (got: ${scripts_dir:-<unset>})" \
        "apple_scripts_dir is missing or outside the app clone"
fi

# Daemon state lives in $GU_STATE_DIR; the legacy directory belongs to the Swift app
if grep -qE '^[^#]*Application Support/GenreUpdater' "$GU_CONFIG_FILE"; then
    fail "my-config.yaml still points into $GU_LEGACY_DIR (re-run install.sh)" \
        "Config points into the legacy directory"
fi

# === Lock acquisition ===
if ! gu_acquire_lock; then
    log "Another instance is already running (PID: $(cat "$GU_LOCK_FILE" 2>/dev/null || echo unknown)). Exiting."
    exit 0
fi
log "Lock acquired (PID: $$)"

# === Pin to the latest release ===
# git's first "fatal:" or "error:" line names the cause; the lines after it are advice
first_git_error() {
    local line last=""
    while IFS= read -r line; do
        case "$line" in
            fatal:* | error:*)
                printf '%s\n' "$line"
                return
                ;;
        esac
        [[ -n "$line" ]] && last="$line"
    done <<< "$1"
    printf '%s\n' "${last:-no output}"
}

# A trigger right after boot or wake can fire before the network is up, so retry.
# Sets fetch_error to the cause of the last failed attempt.
fetch_release_tags() {
    local attempt=1 fetch_output fetch_status
    while :; do
        fetch_status=0
        fetch_output="$(timeout "$FETCH_TIMEOUT" git -C "$GU_APP_DIR" "${GU_FETCH_TAGS_ARGS[@]}" 2>&1)" || fetch_status=$?
        (( fetch_status == 0 )) && break
        if (( fetch_status == 124 )); then
            # timeout(1) kills the fetch without a word and exits 124
            fetch_error="timed out after $FETCH_TIMEOUT s"
        else
            printf '%s\n' "$fetch_output" >> "$DAEMON_LOG"
            fetch_error="$(first_git_error "$fetch_output")"
        fi
        log "Fetch attempt $attempt failed: $fetch_error"
        (( attempt++ < FETCH_ATTEMPTS )) || return 1
        sleep 5
    done
    (( attempt == 1 )) || log "Fetch succeeded on attempt $attempt"
    return 0
}

# route(8) exits 0 even when the route is missing and says so only in its message; any other
# answer, including a route command that cannot run, counts as a network, so the failure stays loud
has_default_route() {
    [[ "$(route -n get default 2>&1)" != *"not in table"* ]]
}

log "Fetching release tags..."
fetch_error=""
if ! fetch_release_tags; then
    # Offline is a normal state, like a closed Music.app: skip quietly rather than run the deployed
    # release, where every failed year lookup would use up one of the album's verification attempts
    if ! has_default_route; then
        log "No network after $FETCH_ATTEMPTS fetch attempts; run skipped"
        exit 75  # EX_TEMPFAIL
    fi
    # A notification has room for the cause, not for git's "unable to access '<url>': " prefix
    notify_cause="${fetch_error#"fatal: unable to access '"*"': "}"
    fail "git fetch failed after $FETCH_ATTEMPTS attempts: $fetch_error" "Git fetch failed: ${notify_cause:0:80}"
fi

target_tag="$(gu_latest_release_tag)"
[[ -n "$target_tag" ]] || fail "No release tag (vX.Y.Z) found on origin" "No release tag found"
current_tag="$(gu_current_release_tag)"

# --force on every run also discards any drift in tracked files
git -C "$GU_APP_DIR" checkout --quiet --force --detach "refs/tags/$target_tag" >> "$DAEMON_LOG" 2>&1 \
    || fail "Checkout of $target_tag failed" "Checkout of $target_tag failed"

if [[ "$current_tag" != "$target_tag" ]]; then
    log "Switched release: ${current_tag:-<untagged>} -> $target_tag"
else
    log "Release: $target_tag"
fi

# Python's load_dotenv() finds app/.env by walking up from src/; keep it pointing at the user-owned file
ln -sfn "$GU_ENV_FILE" "$GU_APP_DIR/.env"

# === Dependency sync ===
cd "$GU_APP_DIR"
log "Syncing dependencies..."

# Sync with one automatic recovery attempt (stale venv).
# Note: `cmd || rc=$?` keeps the real exit code; `if ! cmd; then rc=$?` would always yield 0.
sync_dependencies() {
    local sync_output
    local sync_exit

    sync_exit=0
    sync_output=$(uv sync --frozen --no-dev 2>&1) || sync_exit=$?
    echo "$sync_output" >> "$DAEMON_LOG"

    if [[ $sync_exit -eq 0 ]]; then
        return 0
    fi

    log "First sync failed (exit $sync_exit), cleaning venv and retrying..."
    rm -rf "$GU_APP_DIR/.venv" "$GU_APP_DIR/src/music_genre_updater.egg-info"

    sync_exit=0
    sync_output=$(uv sync --frozen --no-dev 2>&1) || sync_exit=$?
    echo "$sync_output" >> "$DAEMON_LOG"

    if [[ $sync_exit -eq 0 ]]; then
        log "Sync succeeded after venv cleanup"
        return 0
    fi

    log "Second sync also failed (exit $sync_exit)"
    return 1
}

sync_dependencies || fail "Dependency sync failed even after venv cleanup" "uv sync failed"
log "Dependencies synced"

# === Execute main script ===
log "Starting main pipeline ($target_tag)..."
EXIT_CODE=0

if timeout "$TIMEOUT_SECONDS" uv run python main.py --config "$GU_CONFIG_FILE" \
    >> "$GU_LOGS_DIR/stdout.log" 2>> "$GU_LOGS_DIR/stderr.log"; then
    log "Main pipeline completed successfully"
    notify "Genre Updater" "Update completed successfully ($target_tag)" "Glass"
else
    EXIT_CODE=$?
    if [[ $EXIT_CODE -eq 75 ]]; then
        # EX_TEMPFAIL: Music.app is closed, a normal state for the hourly tick
        log "Music.app is not running; run skipped"
    elif [[ $EXIT_CODE -eq 124 ]]; then
        log_error "Script timed out after ${TIMEOUT_SECONDS}s"
        notify "Genre Updater Error" "Script timed out after 4 hours"
    else
        log_error "Script failed with exit code: $EXIT_CODE"
        error_snippet=$(tail -3 "$GU_LOGS_DIR/stderr.log" 2>/dev/null | tr '\n' ' ' | cut -c1-100)
        notify "Genre Updater Error" "Exit code $EXIT_CODE: $error_snippet"
    fi
fi

log "=== Genre Updater Daemon Finished (exit: $EXIT_CODE) ==="
exit $EXIT_CODE
