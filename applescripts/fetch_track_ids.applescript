-- fetch_track_ids.applescript
-- Lightweight script that returns only track IDs (~1.4s for 37K tracks)
-- Used by Smart Delta to detect new/removed tracks without fetching full metadata

on run argv
	tell application "Music"
		try
			-- Bulk fetch all IDs at once (same pattern as fetch_tracks.scpt)
			set idList to id of every track of library playlist 1

			-- Convert list to comma-separated string
			set AppleScript's text item delimiters to ","
			set idString to idList as text
			set AppleScript's text item delimiters to ""

			return idString
		on error errMsg
			return "ERROR:" & errMsg
		end try
	end tell
end run
