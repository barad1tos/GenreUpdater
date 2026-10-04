#!/bin/bash
# notify.sh - macOS notification helper for Genre Updater daemon
# Usage: notify.sh "Title" "Message" [sound]

set -euo pipefail

TITLE="${1:-Genre Updater}"
MESSAGE="${2:-No message provided}"
SOUND="${3:-Basso}"  # Basso for errors, Glass for success

# Values go in as run arguments, never spliced into the script, so quotes or backslashes
# in an error snippet cannot break the AppleScript and swallow the notification
osascript \
    -e 'on run argv' \
    -e 'display notification (item 2 of argv) with title (item 1 of argv) sound name (item 3 of argv)' \
    -e 'end run' \
    -- "$TITLE" "$MESSAGE" "$SOUND"
