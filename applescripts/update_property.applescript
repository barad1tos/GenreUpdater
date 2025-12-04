-- This script updates a property of a track in the Music app based on the provided track ID, property name, and property value.
-- It validates the inputs and handles errors gracefully, providing feedback on success or failure.
--
-- Usage:
-- update_property.applescript TrackID PropertyName PropertyValue
-- Example:
-- update_property.applescript 12345 name "New Track Name"
-- Note: This script is designed to be run from the command line with arguments.
-- The script will return success or error messages based on the operation's outcome.

-- Native AppleScript text trimming (replaces shell script + python3 calls)
on trimText(theText)
	if theText is "" or theText is missing value then return ""
	set trimmed to theText as text
	-- Trim leading whitespace
	repeat while trimmed starts with " " or trimmed starts with tab or trimmed starts with return or trimmed starts with linefeed
		if length of trimmed ≤ 1 then return ""
		set trimmed to text 2 thru -1 of trimmed
	end repeat
	-- Trim trailing whitespace
	repeat while trimmed ends with " " or trimmed ends with tab or trimmed ends with return or trimmed ends with linefeed
		if length of trimmed ≤ 1 then return ""
		set trimmed to text 1 thru -2 of trimmed
	end repeat
	return trimmed
end trimText

-- Native AppleScript property name normalization (replaces shell script + python3 calls)
-- Converts to lowercase and replaces spaces/hyphens with underscores
on normalizePropertyName(propName)
	if propName is "" or propName is missing value then return ""
	-- First trim
	set trimmed to my trimText(propName)
	if trimmed is "" then return ""

	-- Convert to lowercase using ASCII codes and replace spaces/hyphens with underscores
	set lowered to ""
	repeat with c in characters of trimmed
		set charCode to id of c
		-- A-Z (65-90) -> a-z (97-122)
		if charCode ≥ 65 and charCode ≤ 90 then
			set lowered to lowered & (character id (charCode + 32))
		else if charCode is 32 or charCode is 45 or charCode is 9 then
			-- 32=space, 45=hyphen, 9=tab -> underscore
			set lowered to lowered & "_"
		else
			set lowered to lowered & c
		end if
	end repeat

	-- Collapse multiple underscores to single (handles "album  artist" -> "album_artist")
	repeat while lowered contains "__"
		set AppleScript's text item delimiters to "__"
		set parts to text items of lowered
		set AppleScript's text item delimiters to "_"
		set lowered to parts as text
		set AppleScript's text item delimiters to ""
	end repeat

	-- Trim leading/trailing underscores
	repeat while lowered starts with "_"
		if length of lowered ≤ 1 then return ""
		set lowered to text 2 thru -1 of lowered
	end repeat
	repeat while lowered ends with "_"
		if length of lowered ≤ 1 then return ""
		set lowered to text 1 thru -2 of lowered
	end repeat

	return lowered
end normalizePropertyName

on run argv
    try
        -- Validate arguments
        if (count of argv) < 3 then
            return "Error: Not enough arguments. Usage: TrackID PropertyName PropertyValue"
        end if

        -- Parse arguments with validation
        set tID to item 1 of argv

        -- Verify track ID is a valid number
        if tID is not missing value and tID is not "" then
            try
                set tIDnum to (tID as integer)
            on error
                return "Error: Invalid track ID '" & tID & "'. Must be a number."
            end try
        else
            return "Error: Missing track ID"
        end if

        set rawPropName to item 2 of argv
        set propDisplayName to my trimText(rawPropName)
        if propDisplayName is "" then
            return "Error: Missing property name"
        end if
        set normalizedPropName to my normalizePropertyName(propDisplayName)
        set supportedProps to {"name", "album", "artist", "album_artist", "genre", "year"}
        -- Verify it's one of the supported properties
        if normalizedPropName is not in supportedProps then
            return "Error: Unsupported property '" & propDisplayName & "'. Must be name, album, artist, album_artist, genre, or year."
        end if

        set propIdentifier to normalizedPropName

        set rawPropValue to item 3 of argv
        set propValue to my trimText(rawPropValue)
        if propValue is "" then
            return "Error: Empty property value"
        end if

        -- Get current year BEFORE tell block to avoid Music.app scope conflict
        -- (Music.app's 'year' property overrides Standard Additions' date 'year')
        set currentYear to year of (current date)
        set maxValidYear to currentYear + 2

        tell application "Music"
            -- First verify track exists to avoid wasting time
            try
                set trackExists to false
                set trackRef to (first track of library playlist 1 whose id is tIDnum)
                set trackExists to true
            on error errMsg
                return "Error: Track " & tID & " not found: " & errMsg
            end try

            if trackExists then
                -- Combined read-compare-write logic for each property
                set currentValue to ""

                if propIdentifier is "name" then
                    set currentValue to name of trackRef
                    if currentValue is not equal to propValue then
                        set name of trackRef to propValue
                    end if

                else if propIdentifier is "album" then
                    set currentValue to album of trackRef
                    if currentValue is not equal to propValue then
                        set album of trackRef to propValue
                    end if

                else if propIdentifier is "artist" then
                    set currentValue to artist of trackRef
                    if currentValue is not equal to propValue then
                        set artist of trackRef to propValue
                    end if

                else if propIdentifier is "album_artist" then
                    set currentValue to album artist of trackRef
                    if currentValue is not equal to propValue then
                        set album artist of trackRef to propValue
                    end if

                else if propIdentifier is "genre" then
                    set currentValue to genre of trackRef
                    if currentValue is not equal to propValue then
                        set genre of trackRef to propValue
                    end if

                else if propIdentifier is "year" then
                    set currentValue to (year of trackRef) as string
                    if currentValue is not equal to propValue then
                        try
                            set propValueInt to propValue as integer
                            -- Soft validation: reject obviously invalid years
                            -- Allow future years (up to +2 years) for pre-releases and scheduled albums
                            if propValueInt < 1900 or propValueInt > maxValidYear then
                                return "Error: Year value '" & propValueInt & "' is out of reasonable range (1900-" & maxValidYear & ")"
                            end if
                            set year of trackRef to propValueInt
                        on error yearErr
                            return "Error: Failed to set year '" & propValue & "': " & yearErr
                        end try
                    end if
                end if

                -- Return appropriate message
                if currentValue is equal to propValue then
                    return "No Change: Track " & tID & " " & propDisplayName & " already set to " & propValue
                end if
                return "Success: Updated track " & tID & " " & propDisplayName & " from '" & currentValue & "' to '" & propValue & "'"
            end if
        end tell
    on error errMsg
        return "Error: " & errMsg
    end try
end run
