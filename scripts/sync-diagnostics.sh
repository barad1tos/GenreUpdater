#!/bin/bash
# sync-diagnostics.sh - Sync diagnostic data to git repo for CI
#
# This script:
# 1. Copies pending_year_verification.csv from logs to tests/fixtures/
# 2. Commits and pushes to origin (if changed)
#
# Called by run-daemon.sh after successful pipeline run
#
# Usage: ./scripts/sync-diagnostics.sh [--force]
#   --force: Push even if no changes detected

set -euo pipefail

# === Configuration ===
# Source: where daemon writes logs
LOGS_DIR="${MGU_LOGS_DIR:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs}"

# Target: daemon's app clone (pushes to main)
REPO_DIR="${MGU_REPO_DIR:-$HOME/Library/Application Support/GenreUpdater/app}"

# File to sync
SOURCE_FILE="$LOGS_DIR/csv/pending_year_verification.csv"
TARGET_FILE="$REPO_DIR/tests/fixtures/pending_year_verification.csv"

# === Arguments ===
FORCE=false
if [[ "${1:-}" == "--force" ]]; then
    FORCE=true
fi

# === Logging ===
log() {
    echo "[sync-diagnostics] $(date '+%H:%M:%S') $*"
}

log_error() {
    echo "[sync-diagnostics] $(date '+%H:%M:%S') ERROR: $*" >&2
}

# === Validation ===
if [[ ! -f "$SOURCE_FILE" ]]; then
    log "Source file not found: $SOURCE_FILE"
    log "Skipping sync (nothing to push)"
    exit 0
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
    log_error "Not a git repository: $REPO_DIR"
    exit 1
fi

# === Ensure target directory exists ===
mkdir -p "$(dirname "$TARGET_FILE")"

# === Copy file ===
log "Copying pending_year_verification.csv to tests/fixtures/"
cp "$SOURCE_FILE" "$TARGET_FILE"

# === Git operations ===
cd "$REPO_DIR"

# Check if file changed
if git diff --quiet "$TARGET_FILE" 2>/dev/null && [[ "$FORCE" == "false" ]]; then
    log "No changes detected in pending_year_verification.csv"
    exit 0
fi

# Stage the file
git add "$TARGET_FILE"

# Check if there's something to commit
if git diff --staged --quiet; then
    log "Nothing staged to commit"
    exit 0
fi

# Get stats for commit message
LINE_COUNT=$(wc -l < "$TARGET_FILE" | tr -d ' ')
ALBUM_COUNT=$((LINE_COUNT - 1))  # Subtract header line

# Commit
log "Committing changes ($ALBUM_COUNT pending albums)"
git commit -m "chore: sync pending verification data ($ALBUM_COUNT albums) [skip ci]

Automated sync from daemon run.
Source: $SOURCE_FILE"

# Push with retry
log "Pushing to origin..."
push_success=false
for i in 1 2 3; do
    if git push origin HEAD 2>&1; then
        push_success=true
        break
    fi
    log "Push failed (attempt $i/3), pulling and retrying..."
    git pull --rebase origin HEAD 2>&1 || true
    sleep $((i * 2))
done

if [[ "$push_success" == "true" ]]; then
    log "Successfully synced pending_year_verification.csv to origin"
else
    log_error "Failed to push after 3 attempts"
    exit 1
fi
