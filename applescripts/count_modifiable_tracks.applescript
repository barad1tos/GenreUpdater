-- count_modifiable_tracks.applescript
-- Counts only tracks with modifiable cloud status
-- Excludes: prerelease, unknown, no longer available (read-only tracks)
--
-- Performance: ~27s for 37K tracks (vs 0.4s for simple count)
--
-- Status: NOT WIRED into daemon (kept for manual debugging/future use)
-- The daemon was simplified to remove track-count logic - Python pipeline
-- handles all business logic including deciding whether to run.
--
-- Filtering logic matches fetch_tracks.applescript and fetch_track_ids.applescript

on run
	tell application "Music"
		try
			set statusList to cloud status of every track of library playlist 1
			set validCount to 0

			repeat with s in statusList
				try
					set statusText to (s as text)
					if statusText is in {"local only", "purchased", "matched", "uploaded", "subscription", "downloaded"} then
						set validCount to validCount + 1
					end if
				end try
			end repeat

			return validCount
		on error errMsg
			return "ERROR:" & errMsg
		end try
	end tell
end run
