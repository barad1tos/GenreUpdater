#!/bin/bash
# sync-fixtures.sh - Push library snapshot to repo for regression tests
#
# Called by run-daemon.sh after successful pipeline run
# Syncs library_snapshot.json(.gz) → tests/fixtures/ (always as plain JSON)

set -euo pipefail

# === Configuration (template with iCloud defaults) ===
LOGS_DIR="${MGU_LOGS_DIR:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs}"
REPO_DIR="${MGU_REPO_DIR:-$HOME/Library/Application Support/GenreUpdater/app}"
FIXTURE_PATH="tests/fixtures/library_snapshot.json"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [sync-fixtures] $*"
}

# Resolve snapshot path (mirrors Python's _snapshot_path logic)
# Try compressed first (default config), then plain JSON
SNAPSHOT_GZ="$LOGS_DIR/cache/library_snapshot.json.gz"
SNAPSHOT_JSON="$LOGS_DIR/cache/library_snapshot.json"

if [[ -f "$SNAPSHOT_GZ" ]]; then
    SNAPSHOT="$SNAPSHOT_GZ"
elif [[ -f "$SNAPSHOT_JSON" ]]; then
    SNAPSHOT="$SNAPSHOT_JSON"
else
    log "Snapshot not found (checked .json.gz and .json)"
    exit 0
fi

# Check if repo exists
if [[ ! -d "$REPO_DIR/.git" ]]; then
    log "Repo not found: $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"

# Safety check: only push from main or dev branch
CURRENT_BRANCH=$(git branch --show-current)
if [[ ! "$CURRENT_BRANCH" =~ ^(main|dev)$ ]]; then
    log "Skipping: not on main/dev branch (current: $CURRENT_BRANCH)"
    exit 0
fi

# Create fixtures dir if needed
mkdir -p "$(dirname "$FIXTURE_PATH")"

# Copy snapshot to fixtures (decompress if needed)
log "Copying snapshot to fixtures..."
if [[ "$SNAPSHOT" == *.gz ]]; then
    gunzip -c "$SNAPSHOT" > "$FIXTURE_PATH.tmp"
else
    cp "$SNAPSHOT" "$FIXTURE_PATH.tmp"
fi

# Check if snapshot changed
if [[ -f "$FIXTURE_PATH" ]]; then
    if diff -q "$FIXTURE_PATH.tmp" "$FIXTURE_PATH" >/dev/null 2>&1; then
        rm "$FIXTURE_PATH.tmp"
        log "Snapshot unchanged, skipping"
        exit 0
    fi
fi

mv "$FIXTURE_PATH.tmp" "$FIXTURE_PATH"

# Ensure trailing newline (fixes end-of-file-fixer pre-commit hook)
[[ -n "$(tail -c1 "$FIXTURE_PATH")" ]] && echo >> "$FIXTURE_PATH"

log "Committing..."
git add "$FIXTURE_PATH"

if git diff --staged --quiet; then
    log "No changes to commit"
    exit 0
fi

# Commit only the fixture file (not other staged files)
git commit "$FIXTURE_PATH" -m "chore: auto-update library snapshot [skip ci]"

log "Pushing to origin..."
git push origin "$CURRENT_BRANCH"

log "Sync complete"
