-- fetch_track_ids.applescript
-- Lightweight script that returns only track IDs (fast, ~1-2 sec for 37K tracks)
-- Used by Smart Delta to detect new/removed tracks without fetching full metadata

on run argv
	tell application "Music"
		-- Get all tracks from library playlist
		set trackList to every track of library playlist 1
		set idList to {}

		-- Collect database IDs only (no metadata)
		repeat with aTrack in trackList
			set end of idList to (database ID of aTrack as text)
		end repeat

		-- Return as comma-separated string
		set AppleScript's text item delimiters to ","
		set idString to idList as text
		set AppleScript's text item delimiters to ""

		return idString
	end tell
end run
