"""Canonical AppleScript file names and output markers.

Single source of truth for script names used across the codebase.
Import these constants instead of hard-coding filename strings.
"""

from __future__ import annotations

# Script file names (must match files in applescripts/)
FETCH_TRACKS: str = "fetch_tracks.applescript"
FETCH_TRACK_IDS: str = "fetch_track_ids.applescript"
FETCH_TRACKS_BY_IDS: str = "fetch_tracks_by_ids.applescript"
UPDATE_PROPERTY: str = "update_property.applescript"
BATCH_UPDATE_TRACKS: str = "batch_update_tracks.applescript"

# Scripts that return track data (used for log formatting in executor)
TRACK_DATA_SCRIPTS: tuple[str, ...] = (FETCH_TRACKS, FETCH_TRACKS_BY_IDS)

# AppleScript output markers
NO_TRACKS_FOUND: str = "NO_TRACKS_FOUND"
