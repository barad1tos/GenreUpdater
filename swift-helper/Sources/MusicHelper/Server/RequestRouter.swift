// RequestRouter.swift
// Routes incoming requests to appropriate handlers

import Foundation

/// Routes requests to the appropriate handler methods
final class RequestRouter {
    private let musicBridge: MusicAppBridge
    private var shouldShutdown = false

    init(musicBridge: MusicAppBridge) {
        self.musicBridge = musicBridge
    }

    /// Whether shutdown has been requested
    var isShutdownRequested: Bool {
        return shouldShutdown
    }

    /// Route a request to the appropriate handler
    func route(request: Request) async -> AnyResponse {
        switch request.method {
        case .healthCheck:
            return await handleHealthCheck(request: request)
        case .fetchAllTrackIds:
            return await handleFetchAllTrackIds(request: request)
        case .fetchTracks:
            return await handleFetchTracks(request: request)
        case .fetchTracksByIds:
            return await handleFetchTracksByIds(request: request)
        case .updateProperty:
            return await handleUpdateProperty(request: request)
        case .batchUpdateTracks:
            return await handleBatchUpdate(request: request)
        case .shutdown:
            return handleShutdown(request: request)
        }
    }

    // MARK: - Health Check

    private func handleHealthCheck(request: Request) async -> AnyResponse {
        let musicRunning = musicBridge.isMusicAppRunning()
        var trackCount: Int? = nil
        var libraryAccessible = false

        if musicRunning {
            libraryAccessible = musicBridge.isLibraryAccessible()
            if libraryAccessible {
                trackCount = musicBridge.getTrackCount()
            }
        }

        let result = HealthCheckResult(
            musicAppRunning: musicRunning,
            libraryAccessible: libraryAccessible,
            trackCount: trackCount,
            version: "1.0.0"
        )

        return AnyResponse(from: Response(id: request.id, result: result))
    }

    // MARK: - Fetch All Track IDs

    private func handleFetchAllTrackIds(request: Request) async -> AnyResponse {
        guard musicBridge.isMusicAppRunning() else {
            return AnyResponse(from: Response<TrackIdsResult>(
                id: request.id,
                error: .musicAppNotRunning
            ))
        }

        guard musicBridge.isLibraryAccessible() else {
            return AnyResponse(from: Response<TrackIdsResult>(
                id: request.id,
                error: .libraryNotAccessible
            ))
        }

        let trackIds = musicBridge.fetchAllTrackIds()
        let result = TrackIdsResult(trackIds: trackIds, count: trackIds.count)
        return AnyResponse(from: Response(id: request.id, result: result))
    }

    // MARK: - Fetch Tracks

    private func handleFetchTracks(request: Request) async -> AnyResponse {
        guard musicBridge.isMusicAppRunning() else {
            return AnyResponse(from: Response<TracksResult>(
                id: request.id,
                error: .musicAppNotRunning
            ))
        }

        guard musicBridge.isLibraryAccessible() else {
            return AnyResponse(from: Response<TracksResult>(
                id: request.id,
                error: .libraryNotAccessible
            ))
        }

        var artist: String? = nil
        var limit: Int? = nil
        var offset: Int? = nil
        var minDateAdded: Date? = nil

        if case .fetchTracks(let params) = request.params {
            artist = params.artist
            limit = params.limit
            offset = params.offset
            if let minDateSeconds = params.minDateAdded {
                minDateAdded = Date(timeIntervalSince1970: TimeInterval(minDateSeconds))
            }
        }

        if offset != nil || minDateAdded != nil {
            let minDateInfo: String
            if let minDateAdded {
                let seconds = Int(minDateAdded.timeIntervalSince1970)
                minDateInfo = "\(seconds)"
            } else {
                minDateInfo = "nil"
            }
            logDebug("fetch_tracks params: artist=\(artist ?? "nil"), limit=\(limit.map(String.init) ?? "nil"), offset=\(offset.map(String.init) ?? "nil"), min_date_added=\(minDateInfo)")
        }

        let tracks = musicBridge.fetchTracks(
            artist: artist,
            limit: limit,
            offset: offset,
            minDateAdded: minDateAdded
        )
        let result = TracksResult(tracks: tracks, count: tracks.count)
        return AnyResponse(from: Response(id: request.id, result: result))
    }

    // MARK: - Fetch Tracks By IDs

    private func handleFetchTracksByIds(request: Request) async -> AnyResponse {
        guard musicBridge.isMusicAppRunning() else {
            return AnyResponse(from: Response<TracksResult>(
                id: request.id,
                error: .musicAppNotRunning
            ))
        }

        guard musicBridge.isLibraryAccessible() else {
            return AnyResponse(from: Response<TracksResult>(
                id: request.id,
                error: .libraryNotAccessible
            ))
        }

        guard case .fetchTracksByIds(let params) = request.params else {
            return AnyResponse(from: Response<TracksResult>(
                id: request.id,
                error: .malformedRequest,
                detail: "Missing track_ids parameter"
            ))
        }

        let tracks = musicBridge.fetchTracksByIds(ids: params.trackIds)
        let result = TracksResult(tracks: tracks, count: tracks.count)
        return AnyResponse(from: Response(id: request.id, result: result))
    }

    // MARK: - Update Property

    private func handleUpdateProperty(request: Request) async -> AnyResponse {
        guard musicBridge.isMusicAppRunning() else {
            return AnyResponse(from: Response<UpdateResult>(
                id: request.id,
                error: .musicAppNotRunning
            ))
        }

        guard musicBridge.isLibraryAccessible() else {
            return AnyResponse(from: Response<UpdateResult>(
                id: request.id,
                error: .libraryNotAccessible
            ))
        }

        guard case .updateProperty(let params) = request.params else {
            return AnyResponse(from: Response<UpdateResult>(
                id: request.id,
                error: .malformedRequest,
                detail: "Missing update parameters"
            ))
        }

        // Validate property name
        guard isValidProperty(params.property) else {
            return AnyResponse(from: Response<UpdateResult>(
                id: request.id,
                error: .propertyNotSupported,
                detail: "Property '\(params.property)' is not supported"
            ))
        }

        // Validate year value if updating year
        if params.property == "year" {
            if let yearValue = Int(params.value) {
                let currentYear = Calendar.current.component(.year, from: Date())
                if yearValue < 1900 || yearValue > currentYear + 1 {
                    return AnyResponse(from: Response<UpdateResult>(
                        id: request.id,
                        error: .yearOutOfRange,
                        detail: "Year \(yearValue) is outside valid range (1900-\(currentYear + 1))"
                    ))
                }
            }
        }

        let updateResult = musicBridge.updateProperty(
            trackId: params.trackId,
            property: params.property,
            value: params.value
        )

        switch updateResult {
        case .success(let result):
            return AnyResponse(from: Response(id: request.id, result: result))
        case .failure(let error):
            return AnyResponse(from: Response<UpdateResult>(
                id: request.id,
                error: error
            ))
        }
    }

    // MARK: - Batch Update

    private func handleBatchUpdate(request: Request) async -> AnyResponse {
        guard musicBridge.isMusicAppRunning() else {
            return AnyResponse(from: Response<BatchUpdateResult>(
                id: request.id,
                error: .musicAppNotRunning
            ))
        }

        guard musicBridge.isLibraryAccessible() else {
            return AnyResponse(from: Response<BatchUpdateResult>(
                id: request.id,
                error: .libraryNotAccessible
            ))
        }

        guard case .batchUpdate(let params) = request.params else {
            return AnyResponse(from: Response<BatchUpdateResult>(
                id: request.id,
                error: .malformedRequest,
                detail: "Missing updates parameter"
            ))
        }

        let result = musicBridge.batchUpdateTracks(updates: params.updates)
        return AnyResponse(from: Response(id: request.id, result: result))
    }

    // MARK: - Shutdown

    private func handleShutdown(request: Request) -> AnyResponse {
        shouldShutdown = true
        let result = ShutdownResult(message: "Server shutting down")
        return AnyResponse(from: Response(id: request.id, result: result))
    }

    // MARK: - Validation

    private func isValidProperty(_ property: String) -> Bool {
        let validProperties = ["genre", "year", "artist", "album", "name", "album_artist"]
        return validProperties.contains(property)
    }
}
