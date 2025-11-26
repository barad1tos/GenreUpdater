#!/bin/bash
# notify.sh - macOS notification helper for Genre Updater daemon
# Usage: notify.sh "Title" "Message" [sound]

set -euo pipefail

TITLE="${1:-Genre Updater}"
MESSAGE="${2:-No message provided}"
SOUND="${3:-Basso}"  # Basso for errors, Glass for success

osascript -e "display notification \"$MESSAGE\" with title \"$TITLE\" sound name \"$SOUND\""
