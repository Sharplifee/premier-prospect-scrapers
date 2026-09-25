// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "UtahMLSClient",
    platforms: [
        .iOS(.v17),
        .macOS(.v14)
    ],
    products: [
        .library(
            name: "UtahMLSClient",
            targets: ["UtahMLSClient"]
        )
    ],
    targets: [
        .target(
            name: "UtahMLSClient",
            path: "Sources/UtahMLSClient"
        ),
        .testTarget(
            name: "UtahMLSClientTests",
            dependencies: ["UtahMLSClient"],
            path: "Tests/UtahMLSClientTests"
        )
    ]
)
