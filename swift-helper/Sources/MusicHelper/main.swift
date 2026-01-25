// main.swift
// Entry point for Music Helper daemon

import Foundation

nonisolated(unsafe) var debugEnabled = ProcessInfo.processInfo.environment["MUSIC_HELPER_DEBUG"] == "1"

// MARK: - Logging Helpers

func log(_ message: String) {
    let timestamp = ISO8601DateFormatter().string(from: Date())
    fputs("[\(timestamp)] \(message)\n", stderr)
}

func logDebug(_ message: String) {
    guard debugEnabled else { return }
    let timestamp = ISO8601DateFormatter().string(from: Date())
    fputs("[\(timestamp)] DEBUG: \(message)\n", stderr)
}

func logError(_ message: String) {
    let timestamp = ISO8601DateFormatter().string(from: Date())
    fputs("[\(timestamp)] ERROR: \(message)\n", stderr)
}

// MARK: - Help Functions

func printUsage() {
    print("""
    Music Helper - Swift daemon for fast Music.app integration

    USAGE:
        music-helper [OPTIONS]

    OPTIONS:
        --socket <path>    Unix socket path (default: /tmp/music-helper.sock)
        --debug            Enable verbose debug logging
        --help, -h         Show this help message
        --version, -v      Show version information

    PROTOCOL:
        The daemon accepts JSON-RPC requests over a Unix domain socket.
        Messages are length-prefixed (4-byte big-endian + JSON body).

    METHODS:
        health_check        Check Music.app status
        fetch_all_track_ids Get all track IDs
        fetch_tracks        Fetch track metadata (with optional filters)
        fetch_tracks_by_ids Fetch specific tracks by ID
        update_property     Update a track property
        batch_update_tracks Batch update multiple tracks
        shutdown            Gracefully stop the daemon
    """)
}

func printVersion() {
    print("Music Helper v1.0.0")
}

// MARK: - Signal Handling

func setupSignalHandlers() {
    // SIGTERM handler
    signal(SIGTERM) { _ in
        log("Received SIGTERM, shutting down...")
        exit(0)
    }

    // SIGINT handler (Ctrl+C)
    signal(SIGINT) { _ in
        log("Received SIGINT, shutting down...")
        exit(0)
    }

    // Ignore SIGPIPE (broken pipe from client disconnect)
    signal(SIGPIPE, SIG_IGN)
}

// MARK: - Main Entry Point

// Parse command line arguments
let args = CommandLine.arguments

// Default socket path
var socketPath = "/tmp/music-helper.sock"

// Check for --socket argument
for (index, arg) in args.enumerated() {
    if arg == "--socket" && index + 1 < args.count {
        socketPath = args[index + 1]
    }
    if arg == "--debug" {
        debugEnabled = true
    }
    if arg == "--help" || arg == "-h" {
        printUsage()
        exit(0)
    }
    if arg == "--version" || arg == "-v" {
        printVersion()
        exit(0)
    }
}

// Setup signal handlers
setupSignalHandlers()

// Log startup
log("Music Helper daemon starting...")
log("Socket path: \(socketPath)")

// Create bridge and router
let musicBridge = MusicAppBridge()
let router = RequestRouter(musicBridge: musicBridge)

// Create and start server
let server = UnixSocketServer(socketPath: socketPath, router: router)

// Run until shutdown
do {
    try server.start()

    // Check for shutdown flag periodically
    while !router.isShutdownRequested {
        Thread.sleep(forTimeInterval: 0.1)
    }

    server.stop()
    log("Music Helper daemon stopped normally")

} catch let error as ServerError {
    logError("Server error: \(error.description)")
    exit(1)

} catch {
    logError("Unexpected error: \(error)")
    exit(1)
}
