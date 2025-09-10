#!/bin/bash

# Music Genre Autoupdater Service Management Script
# Usage: ./manage_service.sh [start|stop|status|install|uninstall|logs]

PLIST_NAME="com.music.genreautoupdater"
PLIST_PATH="/Users/romanborodavkin/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/Python Scripts/Genres Autoupdater v2.0/$PLIST_NAME.plist"
LAUNCHAGENTS_PATH="$HOME/Library/LaunchAgents"
LOGS_PATH="/Users/romanborodavkin/Library/Mobile Documents/com~apple~CloudDocs/4. Dev/MGU logs"

case "$1" in
install)
	echo "📦 Installing Music Genre Autoupdater service..."

	# Create LaunchAgents directory if it doesn't exist
	mkdir -p "$LAUNCHAGENTS_PATH"

	# Copy plist to LaunchAgents
	cp "$PLIST_PATH" "$LAUNCHAGENTS_PATH/"

	# Load the service
	launchctl load "$LAUNCHAGENTS_PATH/$PLIST_NAME.plist"

	echo "✅ Service installed and loaded"
	echo "📅 Scheduled to run daily at 2:00 AM"
	echo "📊 Logs: $LOGS_PATH/launchctl/"
	;;

uninstall)
	echo "🗑️ Uninstalling Music Genre Autoupdater service..."

	# Unload the service
	launchctl unload "$LAUNCHAGENTS_PATH/$PLIST_NAME.plist" 2>/dev/null || true

	# Remove plist from LaunchAgents
	rm -f "$LAUNCHAGENTS_PATH/$PLIST_NAME.plist"

	echo "✅ Service uninstalled"
	;;

start)
	echo "🚀 Starting Music Genre Autoupdater service..."
	launchctl start "$PLIST_NAME"
	echo "✅ Service started"
	;;

stop)
	echo "🛑 Stopping Music Genre Autoupdater service..."
	launchctl stop "$PLIST_NAME"
	echo "✅ Service stopped"
	;;

status)
	echo "📊 Music Genre Autoupdater service status:"
	launchctl list | grep "$PLIST_NAME" || echo "❌ Service not found"

	# Show recent logs
	echo ""
	echo "📋 Recent stdout (last 10 lines):"
	tail -10 "$LOGS_PATH/launchctl/stdout.log" 2>/dev/null || echo "No stdout log found"

	echo ""
	echo "🚨 Recent stderr (last 10 lines):"
	tail -10 "$LOGS_PATH/launchctl/stderr.log" 2>/dev/null || echo "No stderr log found"
	;;

logs)
	echo "📋 Showing logs for Music Genre Autoupdater service..."
	echo ""
	echo "=== STDOUT LOG ==="
	tail -50 "$LOGS_PATH/launchctl/stdout.log" 2>/dev/null || echo "No stdout log found"

	echo ""
	echo "=== STDERR LOG ==="
	tail -50 "$LOGS_PATH/launchctl/stderr.log" 2>/dev/null || echo "No stderr log found"

	echo ""
	echo "=== MAIN APPLICATION LOG ==="
	tail -20 "$LOGS_PATH/main/main.log" 2>/dev/null || echo "No main log found"
	;;

test)
	echo "🧪 Testing Music Genre Autoupdater service (dry run)..."
	cd "/Users/romanborodavkin/Library/Mobile Documents/com~apple~CloudDocs/3. Git/Own/Python Scripts/Genres Autoupdater v2.0" || exit
	/Users/romanborodavkin/.pyenv/shims/python main.py years --artist Agalloch
	;;

*)
	echo "🎵 Music Genre Autoupdater Service Manager"
	echo ""
	echo "Usage: $0 [command]"
	echo ""
	echo "Commands:"
	echo "  install   - Install and start the service"
	echo "  uninstall - Stop and remove the service"
	echo "  start     - Start the service"
	echo "  stop      - Stop the service"
	echo "  status    - Show service status and recent logs"
	echo "  logs      - Show detailed logs"
	echo "  test      - Test the service with a single artist"
	echo ""
	echo "Service runs daily at 2:00 AM and updates album years from MusicBrainz/Discogs"
	;;
esac
