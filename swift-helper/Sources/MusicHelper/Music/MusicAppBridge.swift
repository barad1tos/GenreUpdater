// MusicAppBridge.swift
// Bridge to Music.app using ScriptingBridge framework

import Foundation
import ScriptingBridge

// MARK: - ScriptingBridge Protocol Definitions

/// Protocol for Music application
@objc protocol MusicApplication {
    @objc optional var isRunning: Bool { get }
    @objc optional var sources: SBElementArray { get }
    @objc optional func libraryPlaylists() -> SBElementArray
}

/// Protocol for Music source (library, etc.)
@objc protocol MusicSource {
    @objc optional var name: String { get }
    @objc optional var kind: Int { get }  // kLib = 0x6B4C6962 (FourCC code)
    @objc optional func libraryPlaylists() -> SBElementArray
    @objc optional func playlists() -> SBElementArray
}

// MARK: - FourCC Constants for Music.app Source Kind

/// FourCC code for library source (ASCII 'kLib')
private let kSourceKindLibrary: Int = 0x6B4C6962  // 'kLib'

/// Protocol for Music playlist
@objc protocol MusicPlaylist {
    @objc optional var name: String { get }
    @objc optional func tracks() -> SBElementArray
}

/// Protocol for Music track
@objc protocol MusicTrack {
    @objc optional var persistentID: String { get }
    @objc optional var databaseID: Int { get }
    @objc optional var name: String { get }
    @objc optional var artist: String { get }
    @objc optional var albumArtist: String { get }
    @objc optional var album: String { get }
    @objc optional var genre: String { get }
    @objc optional var year: Int { get }
    @objc optional var dateAdded: Date { get }
    @objc optional var modificationDate: Date { get }
    @objc optional var cloudStatus: Int { get }

    // Setters
    @objc optional func setName(_ name: String)
    @objc optional func setArtist(_ artist: String)
    @objc optional func setAlbumArtist(_ albumArtist: String)
    @objc optional func setAlbum(_ album: String)
    @objc optional func setGenre(_ genre: String)
    @objc optional func setYear(_ year: Int)
}

extension SBApplication: MusicApplication {}
extension SBObject: MusicSource, MusicPlaylist, MusicTrack {}

// MARK: - MusicAppBridge

/// Bridge to interact with Music.app via ScriptingBridge
final class MusicAppBridge {
    private var musicApp: SBApplication?
    private var libraryPlaylist: SBObject?
    private var trackCache: [String: SBObject] = [:]
    private var lastCacheTime: Date?
    private let cacheValiditySeconds: TimeInterval = 30

    init() {
        // Defer connection to first use
    }

    // MARK: - Connection Management

    /// Connect to Music.app
    private func connect() -> Bool {
        if musicApp == nil {
            musicApp = SBApplication(bundleIdentifier: "com.apple.Music")
        }
        return musicApp != nil
    }

    /// Get library playlist (main track source)
    private func getLibraryPlaylist() -> SBObject? {
        if let cached = libraryPlaylist {
            return cached
        }

        guard connect(), let app = musicApp else { return nil }

        // Get sources using dynamic key-value access
        guard let sources = app.value(forKey: "sources") as? SBElementArray else { return nil }

        for source in sources {
            guard let musicSource = source as? SBObject else { continue }

            // Check if this is the library source (kind = kLib FourCC code)
            if let kind = musicSource.value(forKey: "kind") as? Int, kind == kSourceKindLibrary {
                // Get library playlists
                if let playlists = musicSource.value(forKey: "libraryPlaylists") as? SBElementArray,
                   playlists.count > 0,
                   let firstPlaylist = playlists[0] as? SBObject {
                    libraryPlaylist = firstPlaylist
                    return firstPlaylist
                }
            }
        }

        return nil
    }

    /// Invalidate caches
    private func invalidateCache() {
        trackCache.removeAll()
        lastCacheTime = nil
    }

    /// Check if cache is still valid
    private func isCacheValid() -> Bool {
        guard let lastTime = lastCacheTime else { return false }
        return Date().timeIntervalSince(lastTime) < cacheValiditySeconds
    }

    // MARK: - Public Interface

    /// Check if Music.app is running
    func isMusicAppRunning() -> Bool {
        guard connect(), let app = musicApp else { return false }
        return app.isRunning
    }

    /// Check if library is accessible
    func isLibraryAccessible() -> Bool {
        guard isMusicAppRunning() else { return false }
        return getLibraryPlaylist() != nil
    }

    /// Get total track count
    func getTrackCount() -> Int? {
        guard let library = getLibraryPlaylist() else { return nil }
        guard let tracks = library.value(forKey: "tracks") as? SBElementArray else { return nil }
        return tracks.count
    }

    /// Fetch all track IDs
    func fetchAllTrackIds() -> [String] {
        guard let library = getLibraryPlaylist() else { return [] }
        guard let tracks = library.value(forKey: "tracks") as? SBElementArray else { return [] }

        var ids: [String] = []
        ids.reserveCapacity(tracks.count)

        // Use batch property fetching for performance
        if let persistentIds = tracks.value(forKey: "persistentID") as? [String] {
            return persistentIds
        }

        // Fallback to individual fetching
        for track in tracks {
            guard let trackObj = track as? SBObject else { continue }
            if let persistentId = trackObj.value(forKey: "persistentID") as? String {
                ids.append(persistentId)
            }
        }

        return ids
    }

    /// Fetch tracks with optional filtering
    func fetchTracks(
        artist: String? = nil,
        limit: Int? = nil,
        offset: Int? = nil,
        minDateAdded: Date? = nil
    ) -> [TrackData] {
        guard let library = getLibraryPlaylist() else { return [] }
        guard let tracks = library.value(forKey: "tracks") as? SBElementArray else { return [] }

        var results: [TrackData] = []
        let maxTracks = limit ?? Int.max
        let startIndex = max(offset ?? 1, 1)
        var currentIndex = 1

        for track in tracks {
            defer { currentIndex += 1 }

            if currentIndex < startIndex {
                continue
            }

            guard let trackObj = track as? SBObject else { continue }

            // Get track ID
            guard let trackId = trackObj.value(forKey: "persistentID") as? String else { continue }

            // Filter by artist if specified
            if let artistFilter = artist {
                let trackArtist = trackObj.value(forKey: "artist") as? String ?? ""
                let albumArtist = trackObj.value(forKey: "albumArtist") as? String ?? ""
                let matchesArtist = trackArtist.caseInsensitiveCompare(artistFilter) == .orderedSame
                let matchesAlbumArtist = albumArtist.caseInsensitiveCompare(artistFilter) == .orderedSame
                if !matchesArtist && !matchesAlbumArtist {
                    continue
                }
            }

            if let minDate = minDateAdded {
                guard let dateAdded = trackObj.value(forKey: "dateAdded") as? Date else {
                    continue
                }
                if dateAdded <= minDate {
                    continue
                }
            }

            // Filter by cloud status - only modifiable tracks
            let cloudStatusValue = getCloudStatusString(trackObj)
            if !CloudStatusFilter.isModifiable(cloudStatus: cloudStatusValue) {
                continue
            }

            // Serialize track
            if let trackData = TrackSerializer.serialize(track: trackObj, id: trackId) {
                results.append(trackData)
                trackCache[trackId] = trackObj

                if results.count >= maxTracks {
                    break
                }
            }
        }

        lastCacheTime = Date()
        return results
    }

    /// Fetch tracks by specific IDs
    func fetchTracksByIds(ids: [String]) -> [TrackData] {
        guard let library = getLibraryPlaylist() else { return [] }
        guard let tracks = library.value(forKey: "tracks") as? SBElementArray else { return [] }

        let idSet = Set(ids)
        var results: [TrackData] = []
        var foundIds = Set<String>()

        // Check cache first
        for id in ids {
            if let cachedTrack = trackCache[id], isCacheValid() {
                if let trackData = TrackSerializer.serialize(track: cachedTrack, id: id) {
                    results.append(trackData)
                    foundIds.insert(id)
                }
            }
        }

        // Fetch remaining from library
        if foundIds.count < ids.count {
            for track in tracks {
                guard let trackObj = track as? SBObject else { continue }
                guard let trackId = trackObj.value(forKey: "persistentID") as? String else { continue }

                if idSet.contains(trackId) && !foundIds.contains(trackId) {
                    if let trackData = TrackSerializer.serialize(track: trackObj, id: trackId) {
                        results.append(trackData)
                        trackCache[trackId] = trackObj
                        foundIds.insert(trackId)
                    }
                }

                if foundIds.count == ids.count {
                    break
                }
            }
        }

        lastCacheTime = Date()
        return results
    }

    /// Update a single property on a track
    func updateProperty(trackId: String, property: String, value: String) -> Result<UpdateResult, MusicHelperError> {
        guard let trackObj = findTrack(byId: trackId) else {
            return .failure(.trackNotFound)
        }

        // Get old value
        let oldValue = getPropertyValue(trackObj, property: property)

        // Set new value
        let success = setPropertyValue(trackObj, property: property, value: value)

        if success {
            // Verify the update
            let newValue = getPropertyValue(trackObj, property: property)
            let result = UpdateResult(
                trackId: trackId,
                property: property,
                oldValue: oldValue,
                newValue: newValue,
                success: true
            )
            return .success(result)
        } else {
            return .failure(.valueInvalid)
        }
    }

    /// Batch update multiple tracks
    func batchUpdateTracks(updates: [TrackUpdate]) -> BatchUpdateResult {
        var results: [BatchUpdateItemResult] = []
        var successCount = 0
        var failureCount = 0

        for update in updates {
            let updateResult = updateProperty(
                trackId: update.trackId,
                property: update.property,
                value: update.value
            )

            switch updateResult {
            case .success:
                results.append(BatchUpdateItemResult(
                    trackId: update.trackId,
                    success: true,
                    error: nil
                ))
                successCount += 1

            case .failure(let error):
                results.append(BatchUpdateItemResult(
                    trackId: update.trackId,
                    success: false,
                    error: error.description
                ))
                failureCount += 1
            }
        }

        return BatchUpdateResult(
            results: results,
            successCount: successCount,
            failureCount: failureCount
        )
    }

    // MARK: - Private Helpers

    /// Find track by persistent ID
    private func findTrack(byId trackId: String) -> SBObject? {
        // Check cache first
        if let cached = trackCache[trackId], isCacheValid() {
            return cached
        }

        guard let library = getLibraryPlaylist() else { return nil }
        guard let tracks = library.value(forKey: "tracks") as? SBElementArray else { return nil }

        // Search for track
        for track in tracks {
            guard let trackObj = track as? SBObject else { continue }
            if let id = trackObj.value(forKey: "persistentID") as? String, id == trackId {
                trackCache[trackId] = trackObj
                return trackObj
            }
        }

        return nil
    }

    /// Get property value as string
    private func getPropertyValue(_ track: SBObject, property: String) -> String {
        let key = propertyToKey(property)

        if let value = track.value(forKey: key) {
            if let stringValue = value as? String {
                return stringValue
            }
            if let intValue = value as? Int {
                return String(intValue)
            }
        }

        return ""
    }

    /// Set property value
    private func setPropertyValue(_ track: SBObject, property: String, value: String) -> Bool {
        let key = propertyToKey(property)

        if property == "year" {
            if let intValue = Int(value) {
                track.setValue(intValue, forKey: key)
            } else {
                return false
            }
        } else {
            track.setValue(value, forKey: key)
        }
        return true
    }

    /// Convert property name to ScriptingBridge key
    private func propertyToKey(_ property: String) -> String {
        switch property {
        case "genre": return "genre"
        case "year": return "year"
        case "artist": return "artist"
        case "album": return "album"
        case "name": return "name"
        case "album_artist": return "albumArtist"
        default: return property
        }
    }

    /// Get cloud status as string
    private func getCloudStatusString(_ track: SBObject) -> String {
        if let statusInt = track.value(forKey: "cloudStatus") as? Int {
            return cloudStatusFromInt(statusInt)
        }
        return "unknown"
    }

    /// Convert cloud status integer to string
    private func cloudStatusFromInt(_ value: Int) -> String {
        switch value {
        case 0: return "unknown"
        case 1: return "purchased"
        case 2: return "matched"
        case 3: return "uploaded"
        case 4: return "ineligible"
        case 5: return "removed"
        case 6: return "error"
        case 7: return "duplicate"
        case 8: return "subscription"
        case 9: return "prerelease"
        case 10: return "no longer available"
        default: return "unknown"
        }
    }
}
