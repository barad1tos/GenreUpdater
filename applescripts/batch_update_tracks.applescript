-- batch_update_tracks.applescript
-- Accepts a single string with multiple commands separated by ASCII control characters.
-- Uses the same separator pattern as fetch_tracks.applescript for consistency:
--   ASCII 30 (Record Separator) - separates fields within a command (trackID, propertyName, value)
--   ASCII 29 (Group Separator) - separates individual commands
--
-- Example input: "123<RS>genre<RS>Rock: Classic<GS>456<RS>year<RS>2022"
-- where <RS> = ASCII 30, <GS> = ASCII 29
--
-- Benefits over URL-encoding:
--   1. No external dependencies (no python3 shell call)
--   2. Faster parsing (simple string split vs shell script per value)
--   3. No size increase (URL-encoding can 3x string length)
--   4. Proven pattern (fetch_tracks handles 37k+ tracks this way)

on run argv
    if (count of argv) is 0 then
        return "Error: No update string provided."
    end if

    set updateString to item 1 of argv

    -- Use same separators as fetch_tracks.applescript
    set fieldSeparator to ASCII character 30    -- Record Separator (between fields)
    set commandSeparator to ASCII character 29  -- Group Separator (between commands)

    -- Get current year BEFORE tell block to avoid Music.app scope conflict
    set currentYear to year of (current date)
    set maxValidYear to currentYear + 2

    -- Save current delimiters to avoid breaking other scripts
    set old_delimiters to AppleScript's text item delimiters

    try
        -- Split the entire string into individual commands
        set AppleScript's text item delimiters to commandSeparator
        set commandList to text items of updateString

        tell application "Music"
            -- Iterate over each command
            repeat with aCommand in commandList
                if aCommand is not "" then
                    -- Split the command into parts: ID, property, value
                    set AppleScript's text item delimiters to fieldSeparator
                    set commandParts to text items of aCommand

                    set trackID to item 1 of commandParts
                    set propName to item 2 of commandParts
                    -- No decoding needed - ASCII separators don't appear in metadata
                    set propValue to item 3 of commandParts

                    try
                        -- Find track by ID
                        set the_track to (first track of library playlist 1 whose id is trackID)

                        -- Perform update based on property name
                        if propName is "genre" then
                            set genre of the_track to propValue
                        else if propName is "year" then
                            set propValueInt to propValue as integer
                            -- Validate year range
                            if propValueInt < 1900 or propValueInt > maxValidYear then
                                log "Year " & propValue & " out of range for track " & trackID
                            else
                                set year of the_track to propValueInt
                            end if
                        else if propName is "name" then
                            set name of the_track to propValue
                        else if propName is "album" then
                            set album of the_track to propValue
                        else if propName is "artist" then
                            set artist of the_track to propValue
                        else if propName is "album_artist" then
                            set album artist of the_track to propValue
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
