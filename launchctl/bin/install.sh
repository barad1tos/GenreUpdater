#!/bin/bash
# install.sh - Install the Genre Updater daemon, or migrate it from the legacy
# ~/Library/Application Support/GenreUpdater layout to the XDG layout (see common.sh).
#
# Run from a repository checkout:  ./launchctl/bin/install.sh
# Safe to re-run: user config in ~/.config/genreupdater is never overwritten,
# legacy files are only copied (never moved or deleted).

set -euo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=SCRIPTDIR/common.sh
source "$SCRIPT_DIR/common.sh"

PLIST_TEMPLATE="$REPO_ROOT/launchctl/$GU_LABEL.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/$GU_LABEL.plist"
PLIST_LEGACY_BACKUP="$GU_STATE_DIR/$GU_LABEL.plist.pre-xdg"
LAUNCHD_DOMAIN="gui/$(id -u)"
LAUNCHD_SERVICE="$LAUNCHD_DOMAIN/$GU_LABEL"
LEGACY_APP="$GU_LEGACY_DIR/app"
DEPLOYED_SCRIPTS=(common.sh run-daemon.sh notify.sh update.sh)

die() {
    echo "ERROR: $*" >&2
    exit 1
}

step() {
    echo
    echo "==> $*"
}

# On a failed install, reload the agent that was running before; PLIST_TARGET is
# replaced only after the new plist passes lint, so until then it is the previous one.
agent_was_loaded=false
restore_agent() {
    local exit_code=$?
    if [[ "$exit_code" -eq 0 || "$agent_was_loaded" != true ]]; then
        return
    fi
    echo "Install failed; reloading the LaunchAgent from $PLIST_TARGET" >&2
    launchctl bootstrap "$LAUNCHD_DOMAIN" "$PLIST_TARGET" \
        || echo "Reload failed; run: launchctl bootstrap $LAUNCHD_DOMAIN \"$PLIST_TARGET\"" >&2
}

# Copy the first existing source to target, never overwriting target.
# Returns 0 if copied, 1 if target already exists, 2 if no source exists.
migrate_file() {
    local target="$1" source
    shift
    if [[ -e "$target" ]]; then
        echo "  keep:   $target"
        return 1
    fi
    for source in "$@"; do
        if [[ -f "$source" ]]; then
            # -L turns legacy symlinks into real files
            cp -L "$source" "$target" || die "Could not copy $source to $target"
            echo "  copied: $source -> $target"
            return 0
        fi
    done
    return 2
}

echo "=== Genre Updater Daemon Installer ==="

step "Checking prerequisites"
for tool in git uv timeout plutil; do
    command -v "$tool" > /dev/null || die "'$tool' not found in PATH"
done
[[ -f "$PLIST_TEMPLATE" ]] || die "Plist template not found: $PLIST_TEMPLATE (run from a repository checkout)"
# Checking out the release tag below would rewrite this script while it runs
[[ "$REPO_ROOT" != "$GU_APP_DIR" ]] || die "Run install.sh from a development checkout, not from $GU_APP_DIR"

# bootout below also terminates a running job, so refuse while a run holds a lock
step "Checking that no daemon run is in progress"
for lock in "$GU_LEGACY_DIR/state/run.lock" "$GU_LOCK_FILE"; do
    if gu_lock_is_held "$lock"; then
        die "A daemon run is in progress (lock: $lock). Wait for it to finish, then re-run."
    fi
done

step "Unloading LaunchAgent $GU_LABEL"
if launchctl print "$LAUNCHD_SERVICE" > /dev/null 2>&1; then
    agent_was_loaded=true
    trap restore_agent EXIT
    launchctl bootout "$LAUNCHD_SERVICE"
    # bootout returns before the service is gone; bootstrapping too early fails with "Input/output error"
    for _ in {1..50}; do
        launchctl print "$LAUNCHD_SERVICE" > /dev/null 2>&1 || break
        sleep 0.2
    done
    ! launchctl print "$LAUNCHD_SERVICE" > /dev/null 2>&1 || die "$GU_LABEL is still loaded after bootout"
else
    echo "  (was not loaded)"
fi

step "Creating directories"
mkdir -p "$GU_CONFIG_DIR" "$GU_BIN_DIR" "$GU_LOGS_DIR"
chmod 700 "$GU_CONFIG_DIR"

step "Migrating user config to $GU_CONFIG_DIR"
# The legacy clone's tracked my-config.yaml is what the daemon actually ran with, so it wins
rc=0
migrate_file "$GU_CONFIG_FILE" "$LEGACY_APP/my-config.yaml" "$REPO_ROOT/my-config.yaml" || rc=$?
case "$rc" in
    0)
        # AppleScripts must come from the pinned clone, never from a development checkout
        sed -i '' "s|^apple_scripts_dir:.*|apple_scripts_dir: $GU_APP_DIR/applescripts|" "$GU_CONFIG_FILE"
        if grep -qxF "apple_scripts_dir: $GU_APP_DIR/applescripts" "$GU_CONFIG_FILE"; then
            echo "  set apple_scripts_dir: $GU_APP_DIR/applescripts"
        else
            echo "  WARN: no top-level apple_scripts_dir found; set it to $GU_APP_DIR/applescripts"
        fi
        if [[ -f "$LEGACY_APP/my-config.yaml" && -f "$REPO_ROOT/my-config.yaml" ]] \
            && ! cmp -s "$LEGACY_APP/my-config.yaml" "$REPO_ROOT/my-config.yaml"; then
            echo "  NOTE: the checkout's my-config.yaml differs from the migrated one. Review:"
            echo "        diff \"$REPO_ROOT/my-config.yaml\" \"$GU_CONFIG_FILE\""
        fi
        ;;
    1) ;;
    *) die "No my-config.yaml to migrate; create $GU_CONFIG_FILE from config.yaml" ;;
esac

rc=0
migrate_file "$GU_ENV_FILE" "$LEGACY_APP/.env" "$REPO_ROOT/.env" || rc=$?
[[ "$rc" -le 1 ]] || die "No .env to migrate; create $GU_ENV_FILE with DISCOGS_TOKEN and CONTACT_EMAIL"
chmod 600 "$GU_ENV_FILE"

# Relative paths in my-config.yaml (e.g. artist_renamer.config_path) resolve against the config directory
rc=0
migrate_file "$GU_CONFIG_DIR/artist-renames.yaml" "$LEGACY_APP/artist-renames.yaml" "$REPO_ROOT/artist-renames.yaml" || rc=$?
[[ "$rc" -le 1 ]] || echo "  WARN: artist-renames.yaml not found; artist renames will be skipped"

step "Migrating daemon state to $GU_STATE_DIR"
# last_incremental_run_file and last_db_verify_log may still point at the legacy state directory
legacy_state_dir="$GU_LEGACY_DIR/state"
legacy_state_tilde="~${legacy_state_dir#"$HOME"}"
if grep -qF -e "$legacy_state_dir/" -e "$legacy_state_tilde/" "$GU_CONFIG_FILE"; then
    for state_file in "$legacy_state_dir"/*; do
        [[ -f "$state_file" && "$(basename "$state_file")" != run.lock ]] || continue
        migrate_file "$GU_STATE_DIR/$(basename "$state_file")" "$state_file" || true
    done
    sed -i '' -e "s|$legacy_state_dir/|$GU_STATE_DIR/|g" -e "s|$legacy_state_tilde/|$GU_STATE_DIR/|g" "$GU_CONFIG_FILE"
    echo "  repointed state paths to $GU_STATE_DIR"
fi

# Any remaining path into this checkout or the legacy directory would re-couple the daemon to them
repo_root_tilde="~${REPO_ROOT#"$HOME"}"
legacy_dir_tilde="~${GU_LEGACY_DIR#"$HOME"}"
if grep -nF -e "$REPO_ROOT" -e "$repo_root_tilde" -e "$GU_LEGACY_DIR" -e "$legacy_dir_tilde" "$GU_CONFIG_FILE"; then
    echo "  WARN: the lines above still point into $REPO_ROOT or $GU_LEGACY_DIR; repoint them before the first run"
fi

step "Preparing app clone in $GU_APP_DIR"
if [[ ! -d "$GU_APP_DIR/.git" ]]; then
    git clone --quiet "$GU_REPO_URL" "$GU_APP_DIR"
    echo "  cloned $GU_REPO_URL"
fi
gu_fetch_tags
release_tag="$(gu_latest_release_tag)"
[[ -n "$release_tag" ]] || die "No release tag (vX.Y.Z) on origin. Tag one first, e.g.: git tag -a v3.0.0 origin/main -m v3.0.0 && git push origin v3.0.0"
git -C "$GU_APP_DIR" checkout --quiet --force --detach "refs/tags/$release_tag"
ln -sfn "$GU_ENV_FILE" "$GU_APP_DIR/.env"
echo "  pinned to $release_tag"

step "Syncing dependencies"
(cd "$GU_APP_DIR" && uv sync --frozen --no-dev)

step "Deploying scripts to $GU_BIN_DIR"
for script in "${DEPLOYED_SCRIPTS[@]}"; do
    install -m 755 "$SCRIPT_DIR/$script" "$GU_BIN_DIR/$script"
    echo "  $script"
done

step "Deploying LaunchAgent"
# Keep the very first pre-XDG plist for rollback; later installs never touch this backup
if [[ -f "$PLIST_TARGET" && ! -f "$PLIST_LEGACY_BACKUP" ]] && grep -q "Application Support/GenreUpdater" "$PLIST_TARGET"; then
    cp "$PLIST_TARGET" "$PLIST_LEGACY_BACKUP"
    echo "  backup: $PLIST_LEGACY_BACKUP"
fi
# Stage and lint first, so a bad template never replaces a working plist
plist_staged="$PLIST_TARGET.new"
sed "s|\$HOME|$HOME|g" "$PLIST_TEMPLATE" > "$plist_staged"
plutil -lint "$plist_staged" > /dev/null || die "Invalid plist: $plist_staged"
mv -f "$plist_staged" "$PLIST_TARGET"
launchctl bootstrap "$LAUNCHD_DOMAIN" "$PLIST_TARGET"
trap - EXIT
echo "  loaded $GU_LABEL"

cat << EOF

=== Installation complete ===
Release: $release_tag
Config:  $GU_CONFIG_DIR
App:     $GU_APP_DIR
Logs:    $GU_LOGS_DIR

Verify before relying on it:
  (cd "$GU_APP_DIR" && uv run python main.py --config "$GU_CONFIG_FILE" --test-mode --dry-run)
  launchctl kickstart -k "gui/\$(id -u)/$GU_LABEL" && tail -f "$GU_LOGS_DIR/daemon.log"

Legacy daemon files were left untouched. After a few successful runs, remove only the Python ones:
  rm -rf "$GU_LEGACY_DIR/app" "$GU_LEGACY_DIR/state" "$GU_LEGACY_DIR/logs" && rm "$GU_LEGACY_DIR/bin"
Keep config.json, undo-history.json, checkpoints/ and api_cache.db*: they belong to the Swift app.
EOF
