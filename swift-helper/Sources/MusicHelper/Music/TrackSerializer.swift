// TrackSerializer.swift
// Serializes Music.app track data to TrackData models

import Foundation
import ScriptingBridge

/// Serializes track data from ScriptingBridge objects to TrackData models
final class TrackSerializer {

    private static let dateFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let fallbackDateFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    /// Serialize a ScriptingBridge track object to TrackData
    static func serialize(track: SBObject, id trackId: String) -> TrackData? {
        // Get all properties with null-safety
        let name = getStringProperty(track, "name") ?? ""
        let artist = getStringProperty(track, "artist") ?? ""
        let albumArtist = getStringProperty(track, "albumArtist") ?? artist
        let album = getStringProperty(track, "album") ?? ""
        let genre = getStringProperty(track, "genre") ?? ""
        let year = getIntProperty(track, "year") ?? 0
        let releaseYear = getIntProperty(track, "releaseDate")?.year ?? year

        // Date properties
        let dateAdded = getDateProperty(track, "dateAdded")
        let modificationDate = getDateProperty(track, "modificationDate")

        // Cloud status - handle different property names
        let cloudStatus = getCloudStatus(track)

        return TrackData(
            id: trackId,
            name: name,
            artist: artist,
            albumArtist: albumArtist,
            album: album,
            genre: genre,
            dateAdded: formatDate(dateAdded),
            cloudStatus: cloudStatus,
            year: String(year),
            releaseYear: String(releaseYear),
            modificationDate: formatDate(modificationDate)
        )
    }

    /// Get string property from SBObject
    private static func getStringProperty(_ object: SBObject, _ property: String) -> String? {
        guard let value = object.value(forKey: property) else { return nil }

        if let stringValue = value as? String {
            return stringValue.isEmpty ? nil : stringValue
        }
        return nil
    }

    /// Get integer property from SBObject
    private static func getIntProperty(_ object: SBObject, _ property: String) -> Int? {
        guard let value = object.value(forKey: property) else { return nil }

        if let intValue = value as? Int {
            return intValue == 0 ? nil : intValue
        }
        if let numberValue = value as? NSNumber {
            let intValue = numberValue.intValue
            return intValue == 0 ? nil : intValue
        }
        return nil
    }

    /// Get date property from SBObject
    private static func getDateProperty(_ object: SBObject, _ property: String) -> Date? {
        guard let value = object.value(forKey: property) else { return nil }
        return value as? Date
    }

    /// Get cloud status from track, handling different property names
    private static func getCloudStatus(_ track: SBObject) -> String {
        // Try different property names used in different Music.app versions
        let propertyNames = ["cloudStatus", "iCloudStatus", "cloudState"]

        for propName in propertyNames {
            if let value = track.value(forKey: propName) {
                if let stringValue = value as? String, !stringValue.isEmpty {
                    return stringValue
                }
                // Handle enum values
                if let enumValue = value as? Int {
                    return cloudStatusFromEnum(enumValue)
                }
            }
        }

        return "unknown"
    }

    /// Convert cloud status enum value to string
    private static func cloudStatusFromEnum(_ value: Int) -> String {
        // Music.app cloud status enum values (may vary by version)
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

    /// Format date to ISO8601 string
    private static func formatDate(_ date: Date?) -> String {
        guard let date = date else { return "" }
        return dateFormatter.string(from: date)
    }
}

// MARK: - Date Extension for Year Extraction

private extension Int {
    var year: Int {
        // If it's already a year-like value, return it
        if self >= 1900 && self <= 2100 {
            return self
        }
        return 0
    }
}
