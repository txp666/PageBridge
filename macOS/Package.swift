// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PageBridge",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "PageBridge", targets: ["PageBridge"])],
    targets: [
        .target(name: "PageBridgeCore"),
        .executableTarget(name: "PageBridge", dependencies: ["PageBridgeCore"]),
        .testTarget(name: "PageBridgeCoreTests", dependencies: ["PageBridgeCore"])
    ],
    swiftLanguageModes: [.v5]
)
