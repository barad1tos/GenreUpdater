// Response.swift
// JSON-RPC style response structures for Music Helper daemon

import Foundation

/// Base response structure (JSON-RPC style)
struct Response<T: Codable>: Codable {
    let id: String
    let success: Bool
    let result: T?
    let error: ErrorResponse?

    init(id: String, result: T) {
        self.id = id
        self.success = true
        self.result = result
        self.error = nil
    }

    init(id: String, error: MusicHelperError, detail: String? = nil) {
        self.id = id
        self.success = false
        self.result = nil
        self.error = ErrorResponse(error: error, detail: detail)
    }
}

/// Response for health_check
struct HealthCheckResult: Codable {
    let musicAppRunning: Bool
    let libraryAccessible: Bool
    let trackCount: Int?
    let version: String

    enum CodingKeys: String, CodingKey {
        case musicAppRunning = "music_app_running"
        case libraryAccessible = "library_accessible"
        case trackCount = "track_count"
        case version
    }
}

/// Response for fetch_all_track_ids
struct TrackIdsResult: Codable {
    let trackIds: [String]
    let count: Int

    enum CodingKeys: String, CodingKey {
        case trackIds = "track_ids"
        case count
    }
}

/// Track data model (matches Python TrackDict)
struct TrackData: Codable {
    let id: String
    let name: String
    let artist: String
    let albumArtist: String
    let album: String
    let genre: String
    let dateAdded: String
    let cloudStatus: String
    let year: String
    let releaseYear: String
    let modificationDate: String

    enum CodingKeys: String, CodingKey {
        case id, name, artist, album, genre, year
        case albumArtist = "album_artist"
        case dateAdded = "date_added"
        case cloudStatus = "cloud_status"  // Maps to track_status in Python
        case releaseYear = "release_year"
        case modificationDate = "modification_date"
    }
}

/// Response for fetch_tracks and fetch_tracks_by_ids
struct TracksResult: Codable {
    let tracks: [TrackData]
    let count: Int
}

/// Response for update_property
struct UpdateResult: Codable {
    let trackId: String
    let property: String
    let oldValue: String
    let newValue: String
    let success: Bool

    enum CodingKeys: String, CodingKey {
        case trackId = "track_id"
        case property
        case oldValue = "old_value"
        case newValue = "new_value"
        case success
    }
}

/// Single update result in batch
struct BatchUpdateItemResult: Codable {
    let trackId: String
    let success: Bool
    let error: String?

    enum CodingKeys: String, CodingKey {
        case trackId = "track_id"
        case success
        case error
    }
}

/// Response for batch_update_tracks
struct BatchUpdateResult: Codable {
    let results: [BatchUpdateItemResult]
    let successCount: Int
    let failureCount: Int

    enum CodingKeys: String, CodingKey {
        case results
        case successCount = "success_count"
        case failureCount = "failure_count"
    }
}

/// Response for shutdown
struct ShutdownResult: Codable {
    let message: String
}

/// Type-erased response for encoding any result type
struct AnyResponse: Codable {
    let id: String
    let success: Bool
    let result: AnyCodable?
    let error: ErrorResponse?

    init<T: Codable>(from response: Response<T>) {
        self.id = response.id
        self.success = response.success
        self.result = response.result.map { AnyCodable($0) }
        self.error = response.error
    }
}

/// Type-erased Codable wrapper
struct AnyCodable: Codable {
    let value: Any

    init(_ value: Any) {
        self.value = value
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()

        if container.decodeNil() {
            self.value = NSNull()
        } else if let bool = try? container.decode(Bool.self) {
            self.value = bool
        } else if let int = try? container.decode(Int.self) {
            self.value = int
        } else if let double = try? container.decode(Double.self) {
            self.value = double
        } else if let string = try? container.decode(String.self) {
            self.value = string
        } else if let array = try? container.decode([AnyCodable].self) {
            self.value = array.map { $0.value }
        } else if let dict = try? container.decode([String: AnyCodable].self) {
            self.value = dict.mapValues { $0.value }
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unable to decode value"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()

        switch value {
        case is NSNull:
            try container.encodeNil()
        case let bool as Bool:
            try container.encode(bool)
        case let int as Int:
            try container.encode(int)
        case let double as Double:
            try container.encode(double)
        case let string as String:
            try container.encode(string)
        case let array as [Any]:
            try container.encode(array.map { AnyCodable($0) })
        case let dict as [String: Any]:
            try container.encode(dict.mapValues { AnyCodable($0) })
        case let codable as Codable:
            try codable.encode(to: encoder)
        default:
            throw EncodingError.invalidValue(
                value,
                EncodingError.Context(
                    codingPath: encoder.codingPath,
                    debugDescription: "Unable to encode value"
                )
            )
        }
    }
}
