// swift-tools-version: 5.10
// Keel for macOS: SwiftUI screens over the bundled Python engine (see README.md).
import PackageDescription

let package = Package(
    name: "Keel",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Keel", targets: ["Keel"]),
        .executable(name: "keel-selftest", targets: ["keel-selftest"]),
    ],
    targets: [
        // Engine process, API client, models, formatting and translations. No UI.
        .target(name: "KeelCore"),
        .executableTarget(name: "Keel", dependencies: ["KeelCore"]),
        // CI: start the real engine on demo data and decode every response the app uses.
        .executableTarget(name: "keel-selftest", dependencies: ["KeelCore"]),
        .testTarget(name: "KeelCoreTests", dependencies: ["KeelCore"]),
    ]
)
