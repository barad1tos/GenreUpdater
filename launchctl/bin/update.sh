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

# Hold the daemon lock so a launchd trigger cannot start mid-update
if ! gu_acquire_lock; then
    echo "A daemon run is in progress; try again later." >&2
    exit 1
fi

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

previous_ref="$(git -C "$GU_APP_DIR" rev-parse HEAD)"
git -C "$GU_APP_DIR" checkout --quiet --force --detach "refs/tags/$target_tag"
if ! (cd "$GU_APP_DIR" && uv sync --frozen --no-dev); then
    # Keep code and environment consistent: go back to the release the venv was built for
    git -C "$GU_APP_DIR" checkout --quiet --force --detach "$previous_ref"
    echo "Dependency sync failed; stayed on ${current_tag:-$previous_ref}" >&2
    exit 1
fi
echo "Now on $target_tag"
