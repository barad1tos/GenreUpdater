-- ask_year_drift.applescript
-- Asks the person at the Mac whether a year that changed outside the tool should stay.
-- Arguments: artist, album, old year, new year, the providers' year, seconds before the dialog gives up.
-- Returns "keep" (keep the new year), "fix" (write the providers' year) or "timeout" (nobody answered).
-- Values arrive as arguments and are never spliced into the script, so quotes or backslashes in names are safe.

on run argv
	set artistName to item 1 of argv
	set albumName to item 2 of argv
	set oldYear to item 3 of argv
	set newYear to item 4 of argv
	set providerYear to item 5 of argv
	set giveUpAfter to (item 6 of argv) as integer

	set keepButton to "Зберегти " & newYear
	set fixButton to "Виправити на " & providerYear
	set message to "Рік " & artistName & " — " & albumName & " змінено " & oldYear & " → " & newYear & " ззовні. Провайдери кажуть " & providerYear & "."

	try
		set answer to display dialog message with title "Genre Updater" buttons {keepButton, fixButton} default button fixButton with icon caution giving up after giveUpAfter
	on error number -128
		return "timeout"
	end try

	if gave up of answer then return "timeout"
	if button returned of answer is keepButton then return "keep"
	return "fix"
end run
