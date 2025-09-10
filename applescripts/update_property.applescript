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

        set propName to item 2 of argv
        -- Verify it's one of the supported properties
        if propName is not in {"name", "album", "genre", "year"} then
            return "Error: Unsupported property '" & propName & "'. Must be name, album, genre, or year."
        end if

        set propValue to item 3 of argv
        if propValue is "" then
            return "Error: Empty property value"
        end if

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
                if propName is "name" then
                    set currentValue to name of trackRef
                else if propName is "album" then
                    set currentValue to album of trackRef
                else if propName is "genre" then
                    set currentValue to genre of trackRef
                else if propName is "year" then
                    set currentValue to (year of trackRef) as string
                end if
                
                -- Check if value is actually different
                if currentValue is equal to propValue then
                    return "No Change: Track " & tID & " " & propName & " already set to " & propValue
                end if
                
                -- Update the appropriate property based on propName
                if propName is "name" then
                    set name of trackRef to propValue
                else if propName is "album" then
                    set album of trackRef to propValue
                else if propName is "genre" then
                    set genre of trackRef to propValue
                else if propName is "year" then
                    -- DEBUG: Let's see what Music.app actually says
                    try
                        set propValueInt to propValue as integer
                        set year of trackRef to propValueInt
                    on error yearErr
                        -- Get more debug info about what's really happening
                        return "DEBUG: Music.app year error for '" & propValue & "': " & yearErr & " | System date: " & (current date)
                    end try
                end if
                return "Success: Updated track " & tID & " " & propName & " from '" & currentValue & "' to '" & propValue & "'"
            end if
        end tell
    on error errMsg
        return "Error: " & errMsg
    end try
end run