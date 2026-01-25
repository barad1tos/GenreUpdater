// swift-tools-version:5.9
// The swift-tools-version declares the minimum version of Swift required to build this package.

import PackageDescription

let package = Package(
    name: "MusicHelper",
    platforms: [
        .macOS(.v12)  // macOS Monterey minimum for modern Music.app
    ],
    products: [
        .executable(
            name: "music-helper",
            targets: ["MusicHelper"]
        )
    ],
    dependencies: [],
    targets: [
        .executableTarget(
            name: "MusicHelper",
            dependencies: [],
            path: "Sources/MusicHelper",
            swiftSettings: [
                .enableExperimentalFeature("StrictConcurrency")
            ],
            linkerSettings: [
                .linkedFramework("ScriptingBridge"),
                .linkedFramework("Foundation")
            ]
        )
    ]
)
