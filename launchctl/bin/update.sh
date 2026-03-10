#!/bin/bash
# update.sh - Manual update helper for Genre Updater daemon
# Use this to manually pull latest changes without waiting for trigger

set -euo pipefail

SUPPORT_DIR="$HOME/Library/Application Support/GenreUpdater"
DAEMON_DIR="$SUPPORT_DIR/app"

echo "=== Manual Daemon Update ==="

cd "$DAEMON_DIR"

echo "Current branch: $(git branch --show-current)"
echo "Current commit: $(git log -1 --format='%h %s')"
echo ""

echo "Fetching from origin..."
git fetch origin main

echo ""
echo "Changes to pull:"
git log HEAD..origin/main --oneline || echo "  (none)"

echo ""
read -rp "Apply changes? [y/N] " confirm

if [[ "$confirm" =~ ^[Yy]$ ]]; then
    git reset --hard origin/main
    echo ""
    echo "Syncing dependencies..."
    uv sync --frozen
    echo ""
    echo "Update complete!"
    echo "New commit: $(git log -1 --format='%h %s')"
else
    echo "Cancelled"
fi
