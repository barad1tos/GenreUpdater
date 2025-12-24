#!/bin/bash
# sync-fixtures.sh - Push library snapshot to repo for regression tests
#
# Called by run-daemon.sh after successful pipeline run
# Syncs library_snapshot.json → tests/fixtures/

set -euo pipefail

# === Configuration (template with iCloud defaults) ===
LOGS_DIR="${MGU_LOGS_DIR:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs}"
REPO_DIR="${MGU_REPO_DIR:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python/Genres Autoupdater v2.0}"
FIXTURE_PATH="tests/fixtures/library_snapshot.json"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [sync-fixtures] $*"
}

# Check if snapshot exists
SNAPSHOT="$LOGS_DIR/cache/library_snapshot.json"
if [[ ! -f "$SNAPSHOT" ]]; then
    log "Snapshot not found: $SNAPSHOT"
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

# Check if snapshot changed
if [[ -f "$FIXTURE_PATH" ]]; then
    if diff -q "$SNAPSHOT" "$FIXTURE_PATH" >/dev/null 2>&1; then
        log "Snapshot unchanged, skipping"
        exit 0
    fi
fi

# Copy and commit
log "Copying snapshot to fixtures..."
cp "$SNAPSHOT" "$FIXTURE_PATH"

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
