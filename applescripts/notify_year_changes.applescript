-- notify_year_changes.applescript
-- Shows a macOS notification. Arguments: title, message. The text arrives as arguments, never spliced into the script.
on run argv
	display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"
	return "OK"
end run
