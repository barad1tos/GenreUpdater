-- batch_update_tracks.applescript
-- Accepts a single string with multiple commands separated by semicolons.
-- Each command is in the format "trackID:propertyName:value".
-- Example: "123:genre:Rock;456:year:2022"

on run argv
    if (count of argv) is 0 then
        return "Error: No update string provided."
    end if

    set updateString to item 1 of argv
    set command_separator to ";"
    set part_separator to ":"

    -- Save current delimiters to avoid breaking other scripts
    set old_delimiters to AppleScript's text item delimiters

    try
        -- Split the entire string into individual commands
        set AppleScript's text item delimiters to command_separator
        set commandList to text items of updateString

        tell application "Music"
            -- Iterate over each command
            repeat with aCommand in commandList
                if aCommand is not "" then
                    -- Split the command into parts: ID, property, value
                    set AppleScript's text item delimiters to part_separator
                    set commandParts to text items of aCommand

                    set trackID to item 1 of commandParts
                    set propName to item 2 of commandParts
                    set propValue to item 3 of commandParts

                    try
                        -- Find track by ID
                        set the_track to (first track of library playlist 1 whose id is trackID)

                        -- Perform update based on property name
                        if propName is "genre" then
                            set genre of the_track to propValue
                        else if propName is "year" then
                            set year of the_track to (propValue as integer)
                        else if propName is "name" then
                            set name of the_track to propValue
                        else if propName is "album" then
                            set album of the_track to propValue
                        end if

                    on error errMsg number errNum
                        -- If track not found or other error, log it
                        log "Error updating track ID " & trackID & ": " & errMsg
                    end try
                end if
            end repeat
        end tell

        -- Restore original delimiters
        set AppleScript's text item delimiters to old_delimiters
        return "Success: Batch update process completed."

    on error e
        -- Restore original delimiters in case of global error
        set AppleScript's text item delimiters to old_delimiters
        return "Error: " & e
    end try
end run