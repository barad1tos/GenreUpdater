// Request.swift
// JSON-RPC style request structures for Music Helper daemon

import Foundation

/// Supported RPC methods
enum RequestMethod: String, Codable {
    case healthCheck = "health_check"
    case fetchAllTrackIds = "fetch_all_track_ids"
    case fetchTracks = "fetch_tracks"
    case fetchTracksByIds = "fetch_tracks_by_ids"
    case updateProperty = "update_property"
    case batchUpdateTracks = "batch_update_tracks"
    case shutdown = "shutdown"
}

/// Base request structure (JSON-RPC style)
struct Request: Codable {
    let id: String
    let method: RequestMethod
    let params: RequestParams

    enum CodingKeys: String, CodingKey {
        case id, method, params
    }

    init(id: String, method: RequestMethod, params: RequestParams = .empty) {
        self.id = id
        self.method = method
        self.params = params
    }
}

/// Request parameters (union of all possible params)
enum RequestParams: Codable {
    case empty
    case fetchTracks(FetchTracksParams)
    case fetchTracksByIds(FetchTracksByIdsParams)
    case updateProperty(UpdatePropertyParams)
    case batchUpdate(BatchUpdateParams)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()

        // Try to decode as each type, default to empty if no params or null
        if container.decodeNil() {
            self = .empty
            return
        }

        // Try empty object first
        if let dict = try? container.decode([String: String].self) {
            if dict.isEmpty {
                self = .empty
                return
            }
        }

        // Try specific param types (most specific first)
        if let params = try? container.decode(FetchTracksByIdsParams.self) {
            self = .fetchTracksByIds(params)
            return
        }

        if let params = try? container.decode(UpdatePropertyParams.self) {
            self = .updateProperty(params)
            return
        }

        if let params = try? container.decode(BatchUpdateParams.self) {
            self = .batchUpdate(params)
            return
        }

        if let params = try? container.decode(FetchTracksParams.self) {
            self = .fetchTracks(params)
            return
        }

        // Default to empty
        self = .empty
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .empty:
            try container.encode([String: String]())
        case .fetchTracks(let params):
            try container.encode(params)
        case .fetchTracksByIds(let params):
            try container.encode(params)
        case .updateProperty(let params):
            try container.encode(params)
        case .batchUpdate(let params):
            try container.encode(params)
        }
    }
}

/// Parameters for fetch_tracks method
struct FetchTracksParams: Codable {
    let artist: String?
    let limit: Int?
    let offset: Int?
    let minDateAdded: Int?

    enum CodingKeys: String, CodingKey {
        case artist
        case limit
        case offset
        case minDateAdded = "min_date_added"
    }

    init(artist: String? = nil, limit: Int? = nil, offset: Int? = nil, minDateAdded: Int? = nil) {
        self.artist = artist
        self.limit = limit
        self.offset = offset
        self.minDateAdded = minDateAdded
    }
}

/// Parameters for fetch_tracks_by_ids method
struct FetchTracksByIdsParams: Codable {
    let trackIds: [String]

    enum CodingKeys: String, CodingKey {
        case trackIds = "track_ids"
    }
}

/// Parameters for update_property method
struct UpdatePropertyParams: Codable {
    let trackId: String
    let property: String
    let value: String

    enum CodingKeys: String, CodingKey {
        case trackId = "track_id"
        case property
        case value
    }
}

/// Single track update in batch
struct TrackUpdate: Codable {
    let trackId: String
    let property: String
    let value: String

    enum CodingKeys: String, CodingKey {
        case trackId = "track_id"
        case property
        case value
    }
}

/// Parameters for batch_update_tracks method
struct BatchUpdateParams: Codable {
    let updates: [TrackUpdate]
}
