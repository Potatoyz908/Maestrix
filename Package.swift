// swift-tools-version: 5.9
import PackageDescription

// MARK: - Linux 移植：OpenMaestriCore
//
// OpenMaestriCore 是不依赖 AppKit/SwiftUI/WebKit/Sparkle 的无头核心（模型、持久化、
// 工作区管理、连接/技能注入、Floor/Git）。
//
// 阶段 1（当前）：为避免在没有编译器反馈的情况下给整个代码库批量加 `public`，
// Core 在 Linux 上直接从 `Sources/` 按白名单取文件编译；macOS 构建完全不变。
// 阶段 2：待 Linux 构建稳定后，把白名单文件物理迁入 Sources/OpenMaestriCore/，
// 让 macOS 的 open-maestri 也依赖该 target（届时统一加 `package`/`public` 访问级别）。
//
// 白名单之外（InterAgentServer、PTY/终端、DirectoryWatcher…）由 Sentinela 后续移植。
let coreSources: [String] = [
    "Shared/AtomicFile.swift",
    "Shared/Constants.swift",
    "Shared/NotificationNames.swift",
    "Shared/ErrorTypes.swift",
    "Shared/LoggingService.swift",
    "Shared/LocalizedFallback+Linux.swift",
    "Shared/PlatformHooks.swift",
    "Shared/PlatformServices.swift",
    "Shared/Extensions/CGRect+Frame.swift",
    "Shared/Extensions/Date+ISO8601.swift",
    "Shared/Extensions/UUID+Coding.swift",
    "Workspace/BackupManager.swift",
    "Workspace/PersistenceManager.swift",
    "Workspace/SidebarLayout.swift",
    "Workspace/WorkspaceManager.swift",
    "Workspace/Migrations/Migration_v1_to_v2.swift",
    "Workspace/Models/AppState+Models.swift",
    "Workspace/Models/CanvasNode.swift",
    "Workspace/Models/NodeContent.swift",
    "Workspace/Models/Preferences.swift",
    "Workspace/Models/WorkspaceDocument.swift",
    "Workspace/Models/WorkspacePayload.swift",
    "Connection/ConnectionManager.swift",
    "Connection/RoleInjector.swift",
    "Connection/SkillInjector.swift",
    "Connection/WorkspaceGraph.swift",
    "Floor/FloorManager.swift",
    "Floor/HooksManager.swift",
    "Git/GitFileStatus.swift",
    "Git/GitStatusProvider.swift",
    "Note/NoteFileManager.swift",
    "Routine/RoutineModels.swift",
    "Terminal/ScrollbackStore.swift",
    "Terminal/TerminalActivityMonitor.swift",
    "Terminal/TerminalSession.swift",
    "InterAgent/CLIRouter.swift",
    "InterAgent/InterAgentServer.swift",
    "InterAgent/WebSocketServer.swift",
    "InterAgent/EventBroadcaster.swift",
    "InterAgent/ServerWorkspace.swift",
    "InterAgent/Handlers/AskHandler.swift",
    "InterAgent/Handlers/CheckHandler.swift",
    "InterAgent/Handlers/ListHandler.swift",
    "InterAgent/Handlers/MaestroHandlers.swift",
    "InterAgent/Handlers/NoteHandler.swift",
    "InterAgent/Handlers/PortalHandler.swift",
    "Terminal/POSIXPtyProcess.swift",
    "Terminal/LinuxTerminalManager.swift",
]

let coreTestSources: [String] = [
    "Workspace/CGRectFrameTests.swift",
    "Workspace/CanvasNodeLegacyDecodeTests.swift",
    "Workspace/PersistenceManagerTests.swift",
    "Workspace/MigrationTests.swift",
    "Workspace/WorkspaceTerminalCountTests.swift",
    "Workspace/DataDirOverrideTests.swift",
    "TestSupport/IsolatedDataDir.swift",
    "InterAgent/LinuxPtyAndServerTests.swift",
    "InterAgent/EventStreamTests.swift",
    "Workspace/CrossLanguageDecodingTests.swift",
    "Routine/RoutineModelsTests.swift",
    "Floor/FloorManagerTests.swift",
]

#if os(Linux)

let package = Package(
    name: "open-maestri",
    products: [
        .library(name: "OpenMaestriCore", targets: ["OpenMaestriCore"]),
        .executable(name: "omaestri", targets: ["omaestri"]),
        .executable(name: "open-maestri-server", targets: ["open-maestri-server"]),
    ],
    dependencies: [
        .package(url: "https://github.com/migueldeicaza/SwiftTerm", "1.4.0"..<"2.0.0"),
    ],
    targets: [
        .target(
            name: "OpenMaestriCore",
            dependencies: [
                "SwiftTerm",
            ],
            path: "Sources",
            sources: coreSources
        ),
        .executableTarget(
            name: "open-maestri-server",
            dependencies: [
                "OpenMaestriCore",
            ],
            path: "Sources/Server"
        ),
        .executableTarget(
            name: "omaestri",
            dependencies: [],
            path: "Sources/CLI"
        ),
        // Linux 仅运行无头（纯 Workspace）测试；其余测试仍依赖 AppKit，仅在 macOS 的 OpenMaestriTests 中运行。
        .testTarget(
            name: "OpenMaestriCoreTests",
            dependencies: ["OpenMaestriCore"],
            path: "Tests/OpenMaestriTests",
            sources: coreTestSources
        ),
    ]
)

#else

let package = Package(
    name: "open-maestri",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/migueldeicaza/SwiftTerm", "1.4.0"..<"2.0.0"),
        .package(url: "https://github.com/sparkle-project/Sparkle", "2.6.0"..<"3.0.0"),
    ],
    targets: [
        .executableTarget(
            name: "open-maestri",
            dependencies: [
                "SwiftTerm",
                .product(name: "Sparkle", package: "Sparkle"),
            ],
            path: "Sources",
            exclude: ["CLI"],
            resources: [.process("Resources")]
        ),
        .executableTarget(
            name: "omaestri",
            dependencies: [],
            path: "Sources/CLI"
        ),
        .testTarget(
            name: "OpenMaestriTests",
            dependencies: ["open-maestri"],
            path: "Tests/OpenMaestriTests"
        ),
    ]
)

#endif
