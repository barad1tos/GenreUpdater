(*
    The script for getting track parameters from the Music library.
    COMBINED LOGIC:
    - Uses explicit separators (TAB for fields, LF for lines).
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

    -- Optional filter by minimum date added (Unix timestamp)
    set minDateAdded to missing value
    if (count of argv) >= 4 and item 4 of argv is not "" then
        try
            set timestampSeconds to item 4 of argv as integer
            set minDateAdded to my date_from_unix_timestamp(timestampSeconds)
        on error errMsg
            log "Invalid minDateAdded timestamp: " & errMsg
            set minDateAdded to missing value
        end try
    end if

    set fieldSeparator to tab
    set lineSeparator to linefeed
    set finalResult to {}

    tell application "Music"
        -- Get a reference to all tracks to be processed
        if selectedArtist is not "" then
            if minDateAdded is not missing value then
                -- Artist filter + date filter
                set trackObjects to (every track of library playlist 1 whose ((artist is selectedArtist) or (album artist is selectedArtist)) and (date added > minDateAdded))
            else
                -- Include tracks where either track artist or album artist EXACTLY matches the filter
                set trackObjects to (every track of library playlist 1 whose (artist is selectedArtist) or (album artist is selectedArtist))
            end if
        else if batchLimit > 0 then
            -- Batch mode: get tracks from offset to offset+limit-1 without materializing the entire library collection each time
            set totalTracks to (count of tracks of library playlist 1)

            -- Check if offset is within bounds
            if batchOffset > totalTracks then
                return "ERROR:OFFSET_OUT_OF_BOUNDS:offset=" & batchOffset & ":total=" & totalTracks
            end if

            -- Calculate end index
            set endIndex to batchOffset + batchLimit - 1
            if endIndex > totalTracks then
                set endIndex to totalTracks
            end if

            -- Extract the batch slice directly from the playlist
            try
                set trackObjects to (tracks batchOffset thru endIndex of library playlist 1)
            on error
                -- Fallback: materialize once if direct slice fails
                set allTracks to (every track of library playlist 1)
                set trackObjects to items batchOffset thru endIndex of allTracks
            end try
        else
            if minDateAdded is not missing value then
                set trackObjects to (every track of library playlist 1 whose date added > minDateAdded)
            else
                set trackObjects to (every track of library playlist 1)
            end if
        end if

        if (count of trackObjects) = 0 then
            return "NO_TRACKS_FOUND"
        end if

        -- Determine track count once for consistent array handling
        set trackCount to count of trackObjects

        -- Pre-fetch commonly used fields in bulk to minimize per-track AppleScript calls (with safe fallbacks)
        set statusList to my fetch_property_list(trackObjects, "cloud status", trackCount)
        set idList to my fetch_property_list(trackObjects, "id", trackCount)
        set nameList to my fetch_property_list(trackObjects, "name", trackCount)
        set artistList to my fetch_property_list(trackObjects, "artist", trackCount)
        set albumArtistList to my fetch_property_list(trackObjects, "album artist", trackCount)
        set albumList to my fetch_property_list(trackObjects, "album", trackCount)
        set genreList to my fetch_property_list(trackObjects, "genre", trackCount)
        set dateAddedList to my fetch_property_list(trackObjects, "date added", trackCount)
        set modificationDateList to my fetch_property_list(trackObjects, "modification date", trackCount)
        set yearList to my fetch_property_list(trackObjects, "year", trackCount)

        -- Loop through each track index and filter by status
        repeat with idx from 1 to trackCount
            try
                set currentTrack to item idx of trackObjects
                set statusText to my text_or_empty(my item_or_missing(statusList, idx))

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
                    set track_status to statusText -- Already a clean string

                    set raw_year to my item_or_missing(yearList, idx)
                    set track_year to my normalize_year(raw_year)

                    -- Get release date if available (individual fetch is retained for reliability)
                    set release_year to ""
                    try
                        set raw_release_date to (release date of currentTrack)
                        if raw_release_date is not missing value then
                            set release_year to my extractYearFromDate(raw_release_date)
                        end if
                    on error
                        set release_year to ""
                    end try

                    -- Output: track_id, track_name, track_artist, album_artist, track_album, track_genre, date_added, modification_date, track_status, track_year, release_year, new_year
                    -- Note: new_year is empty placeholder, will be populated by Python after year determination
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


on is_valid_cloud_status(statusText)
    -- Returns true if the cloud status allows editing
    -- Excludes "prerelease" as they are read-only and cause permission errors
    return statusText is in {"local only", "purchased", "matched", "uploaded", "subscription", "downloaded"}
end is_valid_cloud_status

on fetch_property_list(trackObjects, propertyName, expectedCount)
    -- Safely fetch a property list via per-track retrieval to ensure consistent counts
    tell application "Music"
        set valueList to {}
        repeat with idx from 1 to expectedCount
            set propertyValue to missing value
            try
                set currentTrack to item idx of trackObjects
                if propertyName is "cloud status" then
                    set propertyValue to (cloud status of currentTrack)
                else if propertyName is "id" then
                    set propertyValue to (id of currentTrack)
                else if propertyName is "name" then
                    set propertyValue to (name of currentTrack)
                else if propertyName is "artist" then
                    set propertyValue to (artist of currentTrack)
                else if propertyName is "album artist" then
                    set propertyValue to (album artist of currentTrack)
                else if propertyName is "album" then
                    set propertyValue to (album of currentTrack)
                else if propertyName is "genre" then
                    set propertyValue to (genre of currentTrack)
                else if propertyName is "date added" then
                    set propertyValue to (date added of currentTrack)
                else if propertyName is "modification date" then
                    set propertyValue to (modification date of currentTrack)
                else if propertyName is "year" then
                    set propertyValue to (year of currentTrack)
                end if
            on error
                set propertyValue to missing value
            end try
            set end of valueList to propertyValue
        end repeat
        return valueList
    end tell
end fetch_property_list

on item_or_missing(theList, position)
    -- Safely return an item from a list, or missing value if out-of-bounds
    try
        if class of theList is list then
            if (count of theList) >= position then
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
