// CloudStatusFilter.swift
// Filters tracks by cloud status to identify modifiable tracks

import Foundation

/// Cloud status values that indicate a track can be modified locally
enum CloudStatus: String, CaseIterable {
    // Modifiable statuses
    case localOnly = "local only"
    case purchased = "purchased"
    case matched = "matched"
    case uploaded = "uploaded"
    case subscription = "subscription"
    case downloaded = "downloaded"

    // Read-only statuses (should be excluded)
    case prerelease = "prerelease"
    case unknown = "unknown"
    case noLongerAvailable = "no longer available"
    case notUploaded = "not uploaded"
    case error = "error"
    case duplicate = "duplicate"
    case removed = "removed"
    case ineligible = "ineligible"

    /// Whether this status indicates the track can be modified
    var isModifiable: Bool {
        switch self {
        case .localOnly, .purchased, .matched, .uploaded, .subscription, .downloaded:
            return true
        case .prerelease, .unknown, .noLongerAvailable, .notUploaded,
             .error, .duplicate, .removed, .ineligible:
            return false
        }
    }

    /// Initialize from Music.app's raw cloud status value
    /// Music.app returns different formats depending on version
    init(fromMusicApp rawValue: String) {
        let normalized = rawValue.lowercased().trimmingCharacters(in: .whitespaces)

        // Try exact match first
        if let status = CloudStatus(rawValue: normalized) {
            self = status
            return
        }

        // Handle variations in naming
        switch normalized {
        case "none", "local", "":
            self = .localOnly
        case "match", "itunes match":
            self = .matched
        case "upload", "apple music":
            self = .uploaded
        case "purchase", "itunes store":
            self = .purchased
        case "library", "apple music library":
            self = .subscription
        case "download":
            self = .downloaded
        case "pre-release", "pre release":
            self = .prerelease
        case "no longer available", "unavailable":
            self = .noLongerAvailable
        case "not uploaded", "waiting":
            self = .notUploaded
        default:
            self = .unknown
        }
    }
}

/// Filters tracks based on cloud status
final class CloudStatusFilter {

    /// Check if a track with the given cloud status can be modified
    static func isModifiable(cloudStatus: String) -> Bool {
        let status = CloudStatus(fromMusicApp: cloudStatus)
        return status.isModifiable
    }

    /// Filter a list of cloud statuses, returning only modifiable ones
    static func filterModifiable(statuses: [String]) -> [String] {
        return statuses.filter { isModifiable(cloudStatus: $0) }
    }

    /// Get all modifiable status values as strings
    static var modifiableStatuses: [String] {
        return CloudStatus.allCases
            .filter { $0.isModifiable }
            .map { $0.rawValue }
    }

    /// Get all read-only status values as strings
    static var readOnlyStatuses: [String] {
        return CloudStatus.allCases
            .filter { !$0.isModifiable }
            .map { $0.rawValue }
    }
}
