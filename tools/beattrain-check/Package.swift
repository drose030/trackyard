// swift-tools-version: 6.0
// Reads a chart with Beat-Train's own parser and prints its notes as JSON, so
// Trackyard's tests can check exported charts against the game. BeatTrainCore is a
// symlink into the Beat-Train MPC repo next to this one.
import PackageDescription

let package = Package(
    name: "beattrain-check",
    platforms: [.macOS(.v15)],
    targets: [
        .target(name: "BeatTrainCore"),
        .executableTarget(name: "beattrain-check", dependencies: ["BeatTrainCore"]),
    ]
)
