-- fetch_track_ids.applescript
-- Lightweight script that returns only track IDs (~1.4s for 37K tracks)
-- Used by Smart Delta to detect new/removed tracks without fetching full metadata

on run argv
	tell application "Music"
		try
			set trackObjects to every track of library playlist 1
			set trackCount to count of trackObjects

			-- Bulk fetch IDs and cloud status
			set idList to id of every track of library playlist 1
			set statusList to cloud status of every track of library playlist 1

			-- Filter by valid cloud status (same as fetch_tracks.scpt)
			set filteredIds to {}
			repeat with idx from 1 to trackCount
				set statusValue to item idx of statusList
				set statusText to my get_status_string(statusValue)
				if my is_valid_cloud_status(statusText) then
					set end of filteredIds to item idx of idList
				end if
			end repeat

			-- Convert list to comma-separated string
			set AppleScript's text item delimiters to ","
			set idString to filteredIds as text
			set AppleScript's text item delimiters to ""

			return idString
		on error errMsg
			return "ERROR:" & errMsg
		end try
	end tell
end run

-- Convert cloud status enum to string (same as fetch_tracks.scpt)
on get_status_string(c)
	try
		return (c as text)
	on error
		return "unknown"
	end try
end get_status_string

-- Same filter as fetch_tracks.scpt: excludes "prerelease" (read-only tracks)
on is_valid_cloud_status(statusText)
	return statusText is in {"local only", "purchased", "matched", "uploaded", "subscription", "downloaded"}
end is_valid_cloud_status
