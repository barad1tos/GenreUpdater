#!/bin/bash
# install.sh - One-time setup for Genre Updater daemon
# Run this after cloning the daemon repo

set -euo pipefail

# === Configuration ===
SUPPORT_DIR="$HOME/Library/Application Support/GenreUpdater"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_NAME="com.music.genreautoupdater"

DAEMON_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python/Genres Autoupdater v2.0-daemon"
DEV_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/scripts/python/Genres Autoupdater v2.0"

echo "=== Genre Updater Daemon Installer ==="
echo ""

# === Pre-flight checks ===
echo "Checking prerequisites..."

if [[ ! -d "$DAEMON_DIR" ]]; then
    echo "ERROR: Daemon directory not found: $DAEMON_DIR"
    echo "Please clone the repo first:"
    echo "  cd \"$(dirname "$DAEMON_DIR")\""
    echo "  git clone <repo-url> \"$(basename "$DAEMON_DIR")\""
    exit 1
fi

if ! command -v uv &> /dev/null; then
    echo "ERROR: uv not found. Please install it first:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "All prerequisites met!"
echo ""

# === Create directory structure ===
echo "Creating directory structure..."
mkdir -p "$SUPPORT_DIR"/{state,logs,bin}
mkdir -p "$LAUNCH_AGENTS"
echo "Done"

# === Copy scripts from repo ===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Copying scripts from $SCRIPT_DIR..."

for script in run-daemon.sh notify.sh update.sh; do
    if [[ -f "$SCRIPT_DIR/$script" ]]; then
        cp "$SCRIPT_DIR/$script" "$SUPPORT_DIR/bin/$script"
        echo "  Copied: $script"
    else
        echo "  WARNING: $script not found in repo"
    fi
done

# Copy install.sh last (self-copy for updates)
cp "$SCRIPT_DIR/install.sh" "$SUPPORT_DIR/bin/install.sh"
echo "  Copied: install.sh"

# === Make scripts executable ===
echo "Setting permissions..."
chmod +x "$SUPPORT_DIR/bin/"*.sh
echo "Done"

# === Create config symlink ===
if [[ -f "$DEV_DIR/my-config.yaml" ]] && [[ ! -f "$DAEMON_DIR/my-config.yaml" ]]; then
    echo "Creating config symlink..."
    ln -sf "$DEV_DIR/my-config.yaml" "$DAEMON_DIR/my-config.yaml"
    echo "Done"
fi

# === Deploy plist from template ===
LAUNCHCTL_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_TEMPLATE="$LAUNCHCTL_DIR/$PLIST_NAME.plist"

if [[ ! -f "$PLIST_TEMPLATE" ]]; then
    echo "ERROR: Plist template not found: $PLIST_TEMPLATE"
    exit 1
fi

echo "Deploying plist from template..."
echo "  Template: $PLIST_TEMPLATE"

# Read template and replace $HOME with actual path
# Use envsubst for clean variable expansion, or sed as fallback
if command -v envsubst &> /dev/null; then
    HOME="$HOME" envsubst < "$PLIST_TEMPLATE" > "$LAUNCH_AGENTS/$PLIST_NAME.plist"
else
    # Fallback: use sed (escape $ for literal match)
    sed "s|\$HOME|$HOME|g" "$PLIST_TEMPLATE" > "$LAUNCH_AGENTS/$PLIST_NAME.plist"
fi

echo "  Deployed: $LAUNCH_AGENTS/$PLIST_NAME.plist"
echo "Done"

# === Load LaunchAgent ===
echo ""
echo "Loading LaunchAgent..."

# Unload if already loaded
launchctl unload "$LAUNCH_AGENTS/$PLIST_NAME.plist" 2>/dev/null || true

# Load new config
launchctl load "$LAUNCH_AGENTS/$PLIST_NAME.plist"

echo "Done"

# === Verify ===
echo ""
echo "=== Installation Complete ==="
echo ""
echo "Status:"
launchctl list | grep "$PLIST_NAME" || echo "  (not running - will start on Music Library change)"
echo ""
echo "Paths:"
echo "  Daemon code:  $DAEMON_DIR"
echo "  State/logs:   $SUPPORT_DIR"
echo "  Plist template: $PLIST_TEMPLATE"
echo "  Deployed plist: $LAUNCH_AGENTS/$PLIST_NAME.plist"
echo ""
echo "Commands:"
echo "  Manual run:     launchctl kickstart -k gui/\$(id -u)/$PLIST_NAME"
echo "  View logs:      tail -f \"$SUPPORT_DIR/logs/daemon.log\""
echo "  Skip cooldown:  touch \"$SUPPORT_DIR/state/cooldown_override\""
echo "  Uninstall:      launchctl unload \"$LAUNCH_AGENTS/$PLIST_NAME.plist\""
echo ""
