// ErrorCodes.swift
// Error codes for Music Helper daemon
// Maps to errno values for Python compatibility

import Foundation

/// Error codes that can be returned by the Music Helper daemon.
/// These map to standard errno values where possible for consistency with Python's OSError.
enum MusicHelperError: Int, Error, CustomStringConvertible {
    // Connection errors (map to errno 61 = ECONNREFUSED for retryable)
    case musicAppNotRunning = 1000      // errno 61 - Music.app not running
    case libraryNotAccessible = 1001    // errno 61 - Library not accessible

    // Resource errors (map to errno 2 = ENOENT for not found)
    case trackNotFound = 1002           // errno 2 - Track ID not found
    case playlistNotFound = 1003        // errno 2 - Playlist not found

    // Input validation errors (map to errno 22 = EINVAL)
    case propertyNotSupported = 1004    // errno 22 - Property name invalid
    case valueInvalid = 1005            // errno 22 - Property value invalid
    case yearOutOfRange = 1006          // errno 22 - Year outside valid range

    // Protocol errors
    case malformedRequest = 1010        // Bad JSON or missing fields
    case unknownMethod = 1011           // Unknown RPC method

    // Internal errors (map to errno 5 = EIO)
    case internalError = 1099           // errno 5 - Unexpected internal error

    /// Maps error code to corresponding errno value for Python OSError compatibility
    var errno: Int32 {
        switch self {
        case .musicAppNotRunning, .libraryNotAccessible:
            return 61  // ECONNREFUSED - retryable
        case .trackNotFound, .playlistNotFound:
            return 2   // ENOENT - not found
        case .propertyNotSupported, .valueInvalid, .yearOutOfRange,
             .malformedRequest, .unknownMethod:
            return 22  // EINVAL - invalid argument
        case .internalError:
            return 5   // EIO - I/O error
        }
    }

    /// Whether this error is retryable
    var isRetryable: Bool {
        switch self {
        case .musicAppNotRunning, .libraryNotAccessible:
            return true
        default:
            return false
        }
    }

    var description: String {
        switch self {
        case .musicAppNotRunning:
            return "Music.app is not running"
        case .libraryNotAccessible:
            return "Music library is not accessible"
        case .trackNotFound:
            return "Track not found"
        case .playlistNotFound:
            return "Playlist not found"
        case .propertyNotSupported:
            return "Property not supported for modification"
        case .valueInvalid:
            return "Invalid value for property"
        case .yearOutOfRange:
            return "Year value is outside valid range (1900-current+1)"
        case .malformedRequest:
            return "Malformed request JSON"
        case .unknownMethod:
            return "Unknown RPC method"
        case .internalError:
            return "Internal server error"
        }
    }
}

/// JSON-encodable error structure for responses
struct ErrorResponse: Codable {
    let code: Int
    let message: String
    let errno: Int32
    let retryable: Bool

    init(error: MusicHelperError, detail: String? = nil) {
        self.code = error.rawValue
        self.message = detail ?? error.description
        self.errno = error.errno
        self.retryable = error.isRetryable
    }
}
