// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PageBridge",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "PageBridge", targets: ["PageBridge"])],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.0")
    ],
    targets: [
        .target(name: "PageBridgeCore"),
        .executableTarget(
            name: "PageBridge",
            dependencies: ["PageBridgeCore", .product(name: "Sparkle", package: "Sparkle")],
            linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]
        ),
        .testTarget(name: "PageBridgeCoreTests", dependencies: ["PageBridgeCore"])
    ],
    swiftLanguageModes: [.v5]
)
