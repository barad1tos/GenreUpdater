(*
    Lightweight track summary extractor.
    Returns track id, date added, and modification date for all tracks
    using ASCII 30 (field) and ASCII 29 (line) separators.
*)

on run argv
    set fieldSeparator to ASCII character 30
    set lineSeparator to ASCII character 29
    set finalResult to {}

    tell application "Music"
        set trackObjects to every track of library playlist 1
        repeat with currentTrack in trackObjects
            try
                set track_id to (id of currentTrack) as text
                if track_id is "" then error "missing id"

                set date_added_value to my formatDate(date added of currentTrack)
                set modification_value to my formatDate(my resolveModificationDate(currentTrack))

                set trackLine to my joinFields({track_id, date_added_value, modification_value}, fieldSeparator)
                set end of finalResult to trackLine
            on error
                -- Skip tracks that raise errors
            end try
        end repeat
    end tell

    return my joinLines(finalResult, lineSeparator)
end run

on resolveModificationDate(aTrack)
    try
        return modification date of aTrack
    on error
        try
            return date modified of aTrack
        on error
            return missing value
        end try
    end try
end resolveModificationDate

on joinFields(fieldList, fieldSeparator)
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to fieldSeparator
    set joined to fieldList as text
    set AppleScript's text item delimiters to oldDelims
    return joined
end joinFields

on joinLines(lineList, lineSeparator)
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to lineSeparator
    set joined to lineList as text
    set AppleScript's text item delimiters to oldDelims
    return joined
end joinLines

on formatDate(dateValue)
    if dateValue is missing value then return ""
    try
        set yearPart to year of dateValue
        set monthPart to my zeroPad(month of dateValue as integer)
        set dayPart to my zeroPad(day of dateValue)
        set hourPart to my zeroPad(hours of dateValue)
        set minutePart to my zeroPad(minutes of dateValue)
        set secondPart to my zeroPad(seconds of dateValue)
        return (yearPart as string) & "-" & monthPart & "-" & dayPart & " " & hourPart & ":" & minutePart & ":" & secondPart
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
        return "00"
    end try
end zeroPad
