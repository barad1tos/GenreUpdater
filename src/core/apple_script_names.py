"""Canonical AppleScript file names.

Single source of truth for script names used across the codebase.
Import these constants instead of hard-coding filename strings.
"""

from __future__ import annotations

# Script file names (must match files in applescripts/)
FETCH_TRACKS = "fetch_tracks.applescript"
FETCH_TRACK_IDS = "fetch_track_ids.applescript"
FETCH_TRACKS_BY_IDS = "fetch_tracks_by_ids.applescript"
UPDATE_PROPERTY = "update_property.applescript"
BATCH_UPDATE_TRACKS = "batch_update_tracks.applescript"

# Scripts that return track data (used for log formatting in executor)
TRACK_DATA_SCRIPTS: tuple[str, ...] = (FETCH_TRACKS, FETCH_TRACKS_BY_IDS)
