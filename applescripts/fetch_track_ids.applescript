-- fetch_track_ids.applescript
-- Lightweight script that returns only the persistent IDs of editable tracks (about 30 s for 33K tracks, mostly the status filter)
-- Persistent IDs survive Music.app renumbering its track ids, so the tool keys every track by them
-- Used by Smart Delta to detect new/removed tracks without fetching full metadata

on run argv
	tell application "Music"
		try
			set trackObjects to every track of library playlist 1
			set trackCount to count of trackObjects

			-- Bulk fetch IDs and cloud status
			set idList to persistent ID of every track of library playlist 1
			set statusList to cloud status of every track of library playlist 1

			-- Filter by valid cloud status (same as fetch_tracks.scpt)
			set filteredIds to {}
			repeat with idx from 1 to trackCount
				set statusValue to item idx of statusList
				set statusText to my normalize_cloud_status(statusValue)
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

-- Same normalization as fetch_tracks.applescript, so both scripts count the same tracks as editable
on normalize_cloud_status(statusValue)
	-- Normalize cloud status, handling both normal strings and raw AppleScript constants
	-- Raw constants look like: «constant ****kSub» where kSub is the 4-char code
	if statusValue is missing value then
		return ""
	end if

	try
		set statusText to statusValue as text

		-- Check if it's a raw constant (contains "constant" keyword)
		-- Use ignoring case for robustness across macOS versions
		ignoring case
			if statusText contains "constant" then
				-- Map 4-char codes to status strings
				if statusText contains "kSub" then return "subscription"
				if statusText contains "kPre" then return "prerelease"
				if statusText contains "kLoc" then return "local only"
				if statusText contains "kPur" then return "purchased"
				if statusText contains "kMat" then return "matched"
				if statusText contains "kUpl" then return "uploaded"
				if statusText contains "kDwn" then return "downloaded"
				-- Unknown constant
				return "unknown"
			end if
		end ignoring

		-- Return the text as-is (already a proper string)
		return statusText
	on error
		return "unknown"
	end try
end normalize_cloud_status

-- Same filter as fetch_tracks.scpt: excludes "prerelease" (read-only tracks)
on is_valid_cloud_status(statusText)
	return statusText is in {"local only", "purchased", "matched", "uploaded", "subscription", "downloaded"}
end is_valid_cloud_status
