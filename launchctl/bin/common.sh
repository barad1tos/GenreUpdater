#!/bin/bash
# common.sh - Shared layout and helpers for the Genre Updater daemon scripts.
# Sourced by install.sh, run-daemon.sh and update.sh; not meant to be executed directly.
# Must stay compatible with macOS /bin/bash 3.2.
#
# Layout (the Swift app keeps ~/Library/Application Support/GenreUpdater to itself):
#   ~/.config/genreupdater/       my-config.yaml, .env, artist-renames.yaml (user-owned)
#   ~/.local/share/genreupdater/  app/ (clone pinned to the latest vX.Y.Z tag), bin/ (deployed scripts)
#   ~/.local/state/genreupdater/  logs/, run.lock (flock), Python state files
#
# shellcheck disable=SC2034  # variables are consumed by the sourcing scripts

# Python accepts config files only under the working directory or ~/.config
# (core_config._validate_config_path), so this path ignores XDG_CONFIG_HOME on purpose.
GU_CONFIG_DIR="$HOME/.config/genreupdater"
GU_DATA_DIR="$HOME/.local/share/genreupdater"
GU_STATE_DIR="$HOME/.local/state/genreupdater"

GU_APP_DIR="$GU_DATA_DIR/app"
GU_BIN_DIR="$GU_DATA_DIR/bin"
GU_LOGS_DIR="$GU_STATE_DIR/logs"
GU_LOCK_FILE="$GU_STATE_DIR/run.lock"

GU_CONFIG_FILE="$GU_CONFIG_DIR/my-config.yaml"
GU_ENV_FILE="$GU_CONFIG_DIR/.env"

GU_LABEL="com.music.genreautoupdater"
GU_REPO_URL="https://github.com/barad1tos/GenreUpdater.git"

# Pre-XDG location: used only as a migration source and for lock detection
GU_LEGACY_DIR="$HOME/Library/Application Support/GenreUpdater"

# Refresh tags from origin; --prune-tags drops tags deleted upstream,
# so a yanked release never stays "latest". timeout keeps a stalled
# transfer from holding the daemon lock forever.
gu_fetch_tags() {
    timeout 30 git -C "$GU_APP_DIR" fetch --quiet --prune --prune-tags --force --tags origin
}

# Keep only the first stable release tag (vX.Y.Z) from a sorted list.
# awk consumes the whole stream, so upstream commands never get SIGPIPE under pipefail.
_gu_first_release_tag() {
    awk '/^v[0-9]+\.[0-9]+\.[0-9]+$/ && !found { print; found = 1 }'
}

# Print the highest stable release tag on origin; pre-releases are never deployed
gu_latest_release_tag() {
    git -C "$GU_APP_DIR" tag --list 'v*' --sort=-v:refname | _gu_first_release_tag
}

# Print the stable release tag HEAD points at, or nothing
gu_current_release_tag() {
    git -C "$GU_APP_DIR" tag --points-at HEAD --list 'v*' --sort=-v:refname | _gu_first_release_tag
}

# Take the daemon lock for the rest of this process, or fail if another process holds it.
# flock via lockf(1) on fd 9: atomic, and released by the kernel when the holder exits,
# so a crashed run never leaves a stale lock. The PID is written for diagnostics only.
gu_acquire_lock() {
    mkdir -p "$GU_STATE_DIR"
    exec 9>> "$GU_LOCK_FILE"
    lockf -s -t 0 9 || return 1
    echo $$ > "$GU_LOCK_FILE"
}

# Succeed if another process holds the daemon lock
gu_lock_is_held() {
    [[ -f "$GU_LOCK_FILE" ]] || return 1
    ! lockf -k -s -t 0 "$GU_LOCK_FILE" true
}
