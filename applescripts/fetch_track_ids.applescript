-- fetch_track_ids.applescript
-- Returns the persistent ID and year of every editable track (ASCII 30 between the two, ASCII 29 between tracks)
-- About 2 s for 33K tracks with the big lists as script-object properties (below); as plain variables the loop took
-- 32 s for ids alone and 44.5 s with years (measured 2026-10-11)
-- Persistent IDs survive Music.app renumbering its track ids, so the tool keys every track by them
-- Used by Smart Delta (new, removed and year-changed tracks) and by every fetch_all_track_ids caller

-- The big lists live here: `item idx of` on a script-object property stays fast where a plain-variable list made the loop O(n²)
script o
	property ids : {}
	property statuses : {}
	property years : {}
	property rows : {}
end script

on run argv
	tell application "Music"
		try
			set trackObjects to every track of library playlist 1
			set trackCount to count of trackObjects

			-- Bulk fetch IDs, cloud status and years (one read each; the year list is what Smart Delta compares with its snapshot)
			set o's ids to persistent ID of every track of library playlist 1
			set o's statuses to cloud status of every track of library playlist 1
			set o's years to year of every track of library playlist 1
			set fieldSeparator to ASCII character 30
			set lineSeparator to ASCII character 29

			-- Filter by valid cloud status (same as fetch_tracks.scpt)
			set o's rows to {}
			repeat with idx from 1 to trackCount
				set statusValue to item idx of o's statuses
				set statusText to my normalize_cloud_status(statusValue)
				if my is_valid_cloud_status(statusText) then
					set yearValue to item idx of o's years
					if yearValue is missing value or yearValue is 0 then
						set yearText to ""
					else
						set yearText to yearValue as text
					end if
					set end of o's rows to (item idx of o's ids) & fieldSeparator & yearText
				end if
			end repeat

			set AppleScript's text item delimiters to lineSeparator
			set output to o's rows as text
			set AppleScript's text item delimiters to ""

			return output
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
