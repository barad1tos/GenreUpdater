(*
    Optimized script for getting track parameters from the Music library.

    PERFORMANCE: Uses reference-based bulk property access instead of per-track iteration.
    This provides ~2000x speedup for large libraries (37k tracks: 3 seconds vs ~2 hours).

    KEY FEATURES:
    - Uses explicit separators (ASCII 30 for fields, ASCII 29 for lines)
    - Bulk property fetching via reference approach
    - Handles AppleScript raw enum constants (e.g., «constant ****kSub»)
    - Filters tracks at the source, returning ONLY those with modifiable statuses
*)

on run argv
	-- Getting the artist (if specified)
	if (count of argv) > 0 then
		set selectedArtist to item 1 of argv
	else
		set selectedArtist to ""
	end if

	-- Batch processing parameters (offset, limit)
	set batchOffset to 0
	set batchLimit to 0

	if (count of argv) ≥ 2 and item 2 of argv is not "" then
		set batchOffset to item 2 of argv as integer
	end if
	if (count of argv) ≥ 3 and item 3 of argv is not "" then
		set batchLimit to item 3 of argv as integer
	end if

	-- Optional filter by minimum date added (Unix timestamp)
	set minDateAdded to missing value
	if (count of argv) ≥ 4 and item 4 of argv is not "" then
		try
			set timestampSeconds to item 4 of argv as integer
			set minDateAdded to my date_from_unix_timestamp(timestampSeconds)
		on error errMsg
			log "Invalid minDateAdded timestamp: " & errMsg
			set minDateAdded to missing value
		end try
	end if

	set fieldSeparator to ASCII character 30
	set lineSeparator to ASCII character 29
	set finalResult to {}

	tell application "Music"
		-- Get a REFERENCE to tracks (not materialized list) for bulk property access
		if selectedArtist is not "" then
			if minDateAdded is not missing value then
				set trackRef to a reference to (every track of library playlist 1 whose ((artist is selectedArtist) or (album artist is selectedArtist)) and (date added > minDateAdded))
			else
				set trackRef to a reference to (every track of library playlist 1 whose (artist is selectedArtist) or (album artist is selectedArtist))
			end if
		else if batchLimit > 0 then
			-- Batch mode: get tracks from offset to offset+limit-1
			set totalTracks to (count of tracks of library playlist 1)

			if batchOffset > totalTracks then
				return "ERROR:OFFSET_OUT_OF_BOUNDS:offset=" & batchOffset & ":total=" & totalTracks
			end if

			set endIndex to batchOffset + batchLimit - 1
			if endIndex > totalTracks then
				set endIndex to totalTracks
			end if

			set trackRef to a reference to (tracks batchOffset thru endIndex of library playlist 1)
		else
			if minDateAdded is not missing value then
				set trackRef to a reference to (every track of library playlist 1 whose date added > minDateAdded)
			else
				set trackRef to a reference to (every track of library playlist 1)
			end if
		end if

		-- Get track count
		set trackCount to count of trackRef
		if trackCount = 0 then
			return "NO_TRACKS_FOUND"
		end if

		-- BULK FETCH all properties at once (this is the major optimization)
		-- Using reference allows fetching all N tracks' properties in a single Apple Event
		set idList to id of trackRef
		set nameList to name of trackRef
		set artistList to artist of trackRef
		set albumArtistList to album artist of trackRef
		set albumList to album of trackRef
		set genreList to genre of trackRef
		set dateAddedList to date added of trackRef
		set modificationDateList to modification date of trackRef
		set statusList to cloud status of trackRef
		set yearList to year of trackRef
		try
			set releaseDateList to release date of trackRef
		on error
			set releaseDateList to {}
		end try

		-- Loop through each track index and filter by status
		repeat with idx from 1 to trackCount
			try
				-- Get and normalize cloud status
				set rawStatus to my item_or_missing(statusList, idx)
				set statusText to my normalize_cloud_status(rawStatus)

				-- Keep only tracks with modifiable cloud status
				if my is_valid_cloud_status(statusText) then
					-- Retrieve pre-fetched values
					set track_id to my text_or_empty(my item_or_missing(idList, idx))
					set track_name to my text_or_empty(my item_or_missing(nameList, idx))
					set track_artist to my text_or_empty(my item_or_missing(artistList, idx))
					set album_artist to my text_or_empty(my item_or_missing(albumArtistList, idx))
					set track_album to my text_or_empty(my item_or_missing(albumList, idx))
					set track_genre to my text_or_empty(my item_or_missing(genreList, idx))
					set date_added_raw to my item_or_missing(dateAddedList, idx)
					set date_added to my formatDate(date_added_raw)
					set modification_date_raw to my item_or_missing(modificationDateList, idx)
					set modification_date to my formatDate(modification_date_raw)
					set track_status to statusText

					set raw_year to my item_or_missing(yearList, idx)
					set track_year to my normalize_year(raw_year)

					-- Get release year from bulk-fetched release date
					set release_date_raw to my item_or_missing(releaseDateList, idx)
					set release_year to my extractYearFromDate(release_date_raw)

					-- Output: track_id, track_name, track_artist, album_artist, track_album, track_genre, date_added, modification_date, track_status, track_year, release_year, "" (empty placeholder)
					set trackFields to {track_id, track_name, track_artist, album_artist, track_album, track_genre, date_added, modification_date, track_status, track_year, release_year, ""}

					set oldDelimiters to AppleScript's text item delimiters
					set AppleScript's text item delimiters to fieldSeparator
					set trackLine to trackFields as text
					set AppleScript's text item delimiters to oldDelimiters

					set end of finalResult to trackLine
				end if
			on error
				-- Skip problematic tracks while continuing processing
			end try
		end repeat
	end tell

	-- Join all the track lines into a single string for output
	set oldDelimiters to AppleScript's text item delimiters
	set AppleScript's text item delimiters to lineSeparator
	set resultString to finalResult as text
	set AppleScript's text item delimiters to oldDelimiters

	return resultString
end run


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


on is_valid_cloud_status(statusText)
	-- Returns true if the cloud status allows editing
	-- Excludes "prerelease" as they are read-only and cause permission errors
	return statusText is in {"local only", "purchased", "matched", "uploaded", "subscription", "downloaded"}
end is_valid_cloud_status


on item_or_missing(theList, position)
	-- Safely return an item from a list, or missing value if out-of-bounds
	try
		if class of theList is list then
			if (count of theList) ≥ position then
				return item position of theList
			else
				return missing value
			end if
		else if position = 1 then
			return theList
		end if
	on error
		return missing value
	end try
	return missing value
end item_or_missing


on text_or_empty(value)
	-- Convert common value types to text, guarding against missing value
	if value is missing value then
		return ""
	end if
	try
		return value as text
	on error
		try
			return value as string
		on error
			return ""
		end try
	end try
end text_or_empty


on date_from_unix_timestamp(timestampSeconds)
	try
		set epochDate to (current date)
		set year of epochDate to 1970
		set month of epochDate to January
		set day of epochDate to 1
		set time of epochDate to 0
		return epochDate + timestampSeconds
	on error errMsg
		log "date_from_unix_timestamp error: " & errMsg
		return missing value
	end try
end date_from_unix_timestamp


on normalize_year(value)
	-- Normalize raw year values into a clean string representation
	if value is missing value then
		return ""
	end if
	try
		if class of value is integer then
			if value is 0 then
				return ""
			else
				return value as text
			end if
		else if class of value is text then
			if value is "" then
				return ""
			else if value is "0" then
				return ""
			else
				return value
			end if
		else
			return ""
		end if
	on error
		return ""
	end try
end normalize_year


on formatDate(theDate)
	try
		if class of theDate is date then
			set y to year of theDate
			set mInt to (month of theDate as integer)
			set dInt to day of theDate
			set hhInt to hours of theDate
			set mmInt to minutes of theDate
			set ssInt to seconds of theDate

			set mStr to my zeroPad(mInt)
			set dStr to my zeroPad(dInt)
			set hhStr to my zeroPad(hhInt)
			set mmStr to my zeroPad(mmInt)
			set ssStr to my zeroPad(ssInt)

			return (y as string) & "-" & mStr & "-" & dStr & " " & hhStr & ":" & mmStr & ":" & ssStr
		else
			return ""
		end if
	on error
		return ""
	end try
end formatDate


on zeroPad(numValue)
	try
		if numValue < 10 then
			return "0" & (numValue as string)
		else
			return numValue as string
		end if
	on error
		return ""
	end try
end zeroPad


on extractYearFromDate(theDate)
	-- Extract year from a date object
	try
		if theDate is missing value then
			return ""
		end if
		if class of theDate is date then
			return (year of theDate) as text
		else
			return ""
		end if
	on error
		return ""
	end try
end extractYearFromDate
