#!/bin/bash
# update.sh - Show the deployed release and switch to the latest one right away.
# The daemon does the same automatically at the start of its next run.

set -euo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=SCRIPTDIR/common.sh
source "$SCRIPT_DIR/common.sh"

if [[ ! -d "$GU_APP_DIR/.git" ]]; then
    echo "App clone not found: $GU_APP_DIR (run install.sh)" >&2
    exit 1
fi

if gu_lock_is_held "$GU_LOCK_FILE"; then
    echo "A daemon run is in progress; try again later." >&2
    exit 1
fi

# Hold the daemon lock so a launchd trigger cannot start mid-update
mkdir -p "$GU_STATE_DIR"
echo $$ > "$GU_LOCK_FILE"
trap 'rm -f "$GU_LOCK_FILE"' EXIT

gu_fetch_tags
current_tag="$(gu_current_release_tag)"
target_tag="$(gu_latest_release_tag)"
[[ -n "$target_tag" ]] || { echo "No release tag (vX.Y.Z) found on origin." >&2; exit 1; }

echo "Deployed: ${current_tag:-<untagged>}"
echo "Latest:   $target_tag"
if [[ "$current_tag" == "$target_tag" ]]; then
    echo "Already on the latest release."
    exit 0
fi

echo
echo "Changes:"
git -C "$GU_APP_DIR" log --oneline "HEAD..refs/tags/$target_tag"
echo
read -rp "Switch to $target_tag? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Cancelled"
    exit 0
fi

git -C "$GU_APP_DIR" checkout --quiet --force --detach "refs/tags/$target_tag"
(cd "$GU_APP_DIR" && uv sync --frozen)
echo "Now on $target_tag"
