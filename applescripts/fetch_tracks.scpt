(*
    The script for getting track parameters from the Music library.
    COMBINED LOGIC:
    - Uses reliable separators (U+001E for fields, U+001D for lines).
    - Efficiently builds a list of results.
    - Filters tracks at the source, returning ONLY those with modifiable statuses.
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
    
    if (count of argv) >= 2 and item 2 of argv is not "" then
        set batchOffset to item 2 of argv as integer
    end if
    if (count of argv) >= 3 and item 3 of argv is not "" then
        set batchLimit to item 3 of argv as integer
    end if

    set fieldSeparator to ASCII character 30
    set lineSeparator to ASCII character 29
    set finalResult to {}

    tell application "Music"
        -- Get a reference to all tracks to be processed
        if selectedArtist is not "" then
            -- Include tracks where either track artist or album artist EXACTLY matches the filter
            set trackObjects to (every track of library playlist 1 whose (artist is selectedArtist) or (album artist is selectedArtist))
        else if batchLimit > 0 then
            -- Batch mode: get tracks from offset to offset+limit-1
            set allTracks to (every track of library playlist 1)
            set totalTracks to count of allTracks
            
            -- Check if offset is within bounds
            if batchOffset > totalTracks then
                return ""
            end if
            
            -- Calculate end index
            set endIndex to batchOffset + batchLimit - 1
            if endIndex > totalTracks then
                set endIndex to totalTracks
            end if
            
            -- Extract the batch slice
            set trackObjects to items batchOffset thru endIndex of allTracks
        else
            set trackObjects to (every track of library playlist 1)
        end if

        if (count of trackObjects) = 0 then
            return ""
        end if

        -- Loop through each track and filter by status
        repeat with currentTrack in trackObjects
            try
                set rawCloudStatus to (cloud status of currentTrack)
                set statusText to (rawCloudStatus as text)

                -- Keep only tracks with modifiable cloud status
                if my is_valid_cloud_status(statusText) then
                    -- Get all properties for the valid track
                    set track_id to (id of currentTrack) as text
                    set track_name to (name of currentTrack) as text
                    set track_artist to (artist of currentTrack) as text
                    set album_artist to (album artist of currentTrack) as text
                    set track_album to (album of currentTrack) as text
                    set track_genre to (genre of currentTrack) as text
                    set date_added_raw to (date added of currentTrack)
                    set date_added to my formatDate(date_added_raw)
                    set track_status to statusText -- Already a clean string

                    set raw_year to (year of currentTrack)
                    if class of raw_year is integer then
                        if raw_year is 0 then
                            set track_year to ""
                        else
                            set track_year to raw_year as text
                        end if
                    else
                        set track_year to ""
                    end if
                    
                    -- Get release date if available
                    try
                        set raw_release_date to (release date of currentTrack)
                    on error
                        set raw_release_date to missing value
                    end try
                    
                    -- Extract year from release date (must be done outside the track property access)
                    if raw_release_date is not missing value then
                        try
                            set release_year to my extractYearFromDate(raw_release_date)
                        on error
                            set release_year to ""
                        end try
                    else
                        set release_year to ""
                    end if

                    -- Output: track_id, track_name, track_artist, album_artist, track_album, track_genre, date_added, track_status, track_year, release_year, new_year
                    set trackFields to {track_id, track_name, track_artist, album_artist, track_album, track_genre, date_added, track_status, track_year, release_year, ""}

                    set oldDelimiters to AppleScript's text item delimiters
                    set AppleScript's text item delimiters to fieldSeparator
                    set trackLine to trackFields as text
                    set AppleScript's text item delimiters to oldDelimiters

                    set end of finalResult to trackLine
                end if

            on error
                -- We skip problematic tracks, the logic remains
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


on is_valid_cloud_status(statusText)
    -- Returns true if the cloud status allows editing
    -- Excludes "prerelease" as they are read-only and cause permission errors
    return statusText is in {"local only", "purchased", "matched", "uploaded", "subscription", "downloaded"}
end is_valid_cloud_status

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

on get_cloud_status_string(c)
    try
        return (c as text)
    on error
        return "unknown"
    end try
end get_cloud_status_string

on extractYearFromDate(theDate)
    -- Extract year from a date object (must be called outside the Music tell block)
    try
        if class of theDate is date then
            return (year of theDate) as text
        else
            return ""
        end if
    on error
        return ""
    end try
end extractYearFromDate

-- The escape_special_characters function is removed as it's no longer needed.
