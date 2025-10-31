(*
    Fetch detailed track metadata for a list of track IDs.
    Outputs the same field structure as fetch_tracks.scpt using
    ASCII 30 (field) and ASCII 29 (line) separators.

    Expected argument 1: comma-separated list of track IDs.
*)

on run argv
    if (count of argv) < 1 then return ""

    set idsParam to item 1 of argv
    if idsParam is "" then return ""

    set fieldSeparator to ASCII character 30
    set lineSeparator to ASCII character 29
    set finalResult to {}

    set AppleScript's text item delimiters to ","
    set idList to text items of idsParam
    set AppleScript's text item delimiters to ""

    tell application "Music"
        repeat with idText in idList
            set trackIdText to idText as text
            if trackIdText is "" then
                -- Skip empty ids
                set finalResult to finalResult
            else
                try
                    set currentTrack to track id trackIdText
                    set trackLine to my serializeTrack(currentTrack, fieldSeparator)
                    if trackLine is not "" then
                        set end of finalResult to trackLine
                    end if
                on error
                    -- Skip tracks that cannot be resolved
                end try
            end if
        end repeat
    end tell

    return my joinLines(finalResult, lineSeparator)
end run

on serializeTrack(trackRef, fieldSeparator)
    try
        set track_id to (id of trackRef) as text
        if track_id is "" then return ""

        set track_name to my safeText(name of trackRef)
        set track_artist to my safeText(artist of trackRef)
        set album_artist to my safeText(album artist of trackRef)
        set track_album to my safeText(album of trackRef)
        set track_genre to my safeText(genre of trackRef)
        set date_added to my formatDate(date added of trackRef)
        set track_status to my safeText(cloud status of trackRef)
        set track_year to my normalizeYear(year of trackRef)
        set release_year to my extractReleaseYear(trackRef)
        set new_year to "" -- maintained for compatibility

        set fields to {track_id, track_name, track_artist, album_artist, track_album, track_genre, date_added, track_status, track_year, release_year, new_year}
        return my joinFields(fields, fieldSeparator)
    on error
        return ""
    end try
end serializeTrack

on extractReleaseYear(trackRef)
    try
        set releaseDateValue to release date of trackRef
        return my formatDate(releaseDateValue)
    on error
        return ""
    end try
end extractReleaseYear

on safeText(value)
    if value is missing value then
        return ""
    end if
    try
        return value as text
    on error
        return ""
    end try
end safeText

on normalizeYear(yearValue)
    try
        if yearValue is missing value then return ""
        if yearValue is 0 then return ""
        return yearValue as text
    on error
        return ""
    end try
end normalizeYear

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
