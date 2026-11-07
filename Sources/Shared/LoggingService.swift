#if canImport(OSLog)
#if canImport(OSLog)
import OSLog
#endif

extension Logger {
    static func make(category: String) -> Logger {
        Logger(subsystem: "com.open-maestri.app", category: category)
    }
}
#else
import Foundation

/// Linux 上没有 OSLog：提供 API 兼容的最小 Logger（输出到 stderr）。
/// 仅覆盖项目内使用的 debug/info/notice/warning/error/fault，接受字符串插值。
struct Logger: Sendable {
    let subsystem: String
    let category: String

    init(subsystem: String, category: String) {
        self.subsystem = subsystem
        self.category = category
    }

    static func make(category: String) -> Logger {
        Logger(subsystem: "com.open-maestri.app", category: category)
    }

    private func emit(_ level: String, _ message: String) {
        FileHandle.standardError.write(Data("[\(level)] \(category): \(message)\n".utf8))
    }

    func debug(_ message: @autoclosure () -> String) { emit("debug", message()) }
    func info(_ message: @autoclosure () -> String) { emit("info", message()) }
    func notice(_ message: @autoclosure () -> String) { emit("notice", message()) }
    func warning(_ message: @autoclosure () -> String) { emit("warn", message()) }
    func error(_ message: @autoclosure () -> String) { emit("error", message()) }
    func fault(_ message: @autoclosure () -> String) { emit("fault", message()) }
}
#endif
