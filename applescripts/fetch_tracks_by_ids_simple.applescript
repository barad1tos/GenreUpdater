on run argv
    if (count of argv) < 1 then return ""
    
    set idsParam to item 1 of argv
    set fieldSeparator to ASCII character 30
    set lineSeparator to ASCII character 29
    set finalResult to {}
    
    set AppleScript's text item delimiters to ","
    set idList to text items of idsParam
    set AppleScript's text item delimiters to ""
    
    tell application "Music"
        repeat with idText in idList
            set trackIdText to idText as text
            if trackIdText is not "" then
                try
                    set t to track id trackIdText
                    
                    set trackId to (id of t) as text
                    set trackName to name of t
                    set trackArtist to artist of t
                    set albumName to album of t
                    
                    set trackLine to trackId & fieldSeparator & trackName & fieldSeparator & trackArtist & fieldSeparator & albumName
                    set end of finalResult to trackLine
                on error
                    -- Skip tracks that cannot be resolved
                end try
            end if
        end repeat
    end tell
    
    set AppleScript's text item delimiters to lineSeparator
    set output to finalResult as text
    set AppleScript's text item delimiters to ""
    
    return output
end run
