-- This script updates a property of a track in the Music app based on the provided track ID, property name, and property value.
-- It validates the inputs and handles errors gracefully, providing feedback on success or failure.
--
-- Usage:
-- update_property.applescript TrackID PropertyName PropertyValue
-- Example:
-- update_property.applescript 12345 name "New Track Name"
-- Note: This script is designed to be run from the command line with arguments.
-- The script will return success or error messages based on the operation's outcome.


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
        set propDisplayName to do shell script "/usr/bin/python3 -c 'import sys;print(sys.argv[1].strip())' " & quoted form of rawPropName
        if propDisplayName is "" then
            return "Error: Missing property name"
        end if
        set normalizedPropName to do shell script "/usr/bin/python3 -c 'import sys;name = sys.argv[1];name = name.strip().lower();name = \"_\".join(name.replace(\"-\", \" \").split());print(name)' " & quoted form of propDisplayName
        set supportedProps to {"name", "album", "artist", "album_artist", "genre", "year"}
        -- Verify it's one of the supported properties
        if normalizedPropName is not in supportedProps then
            return "Error: Unsupported property '" & propDisplayName & "'. Must be name, album, artist, album_artist, genre, or year."
        end if

        set propIdentifier to normalizedPropName

        set rawPropValue to item 3 of argv
        set propValue to do shell script "/usr/bin/python3 -c 'import sys;print(sys.argv[1].strip())' " & quoted form of rawPropValue
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
                -- Get current property value for comparison
                set currentValue to ""
                if propIdentifier is "name" then
                    set currentValue to name of trackRef
                else if propIdentifier is "album" then
                    set currentValue to album of trackRef
                else if propIdentifier is "artist" then
                    set currentValue to artist of trackRef
                else if propIdentifier is "album_artist" then
                    set currentValue to album artist of trackRef
                else if propIdentifier is "genre" then
                    set currentValue to genre of trackRef
                else if propIdentifier is "year" then
                    set currentValue to (year of trackRef) as string
                end if
                
                -- Check if value is actually different
                if currentValue is equal to propValue then
                    return "No Change: Track " & tID & " " & propDisplayName & " already set to " & propValue
                end if
                
                -- Update the appropriate property based on propName
                if propIdentifier is "name" then
                    set name of trackRef to propValue
                else if propIdentifier is "album" then
                    set album of trackRef to propValue
                else if propIdentifier is "artist" then
                    set artist of trackRef to propValue
                else if propIdentifier is "album_artist" then
                    set album artist of trackRef to propValue
                else if propIdentifier is "genre" then
                    set genre of trackRef to propValue
                else if propIdentifier is "year" then
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
                return "Success: Updated track " & tID & " " & propDisplayName & " from '" & currentValue & "' to '" & propValue & "'"
            end if
        end tell
    on error errMsg
        return "Error: " & errMsg
    end try
end run
