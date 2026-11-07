import Foundation

/// Synchronous read/write of a terminal's scrollback in the macOS `ScrollbackStore` JSONL format (one
/// `{"attributes":[],"text":"<line>"}` per line), at `<dataDir>/workspaces/<wsId>/terminals/<terminalId>.scrollback`.
enum TerminalScrollbackFile {
    static let maxLines = 10_000

    static func save(_ text: String, terminalId: UUID, workspaceId: UUID) {
        var lines = text.components(separatedBy: "\n").map { $0.hasSuffix("\r") ? String($0.dropLast()) : $0 }
        while let last = lines.last, last.trimmingCharacters(in: .whitespaces).isEmpty { lines.removeLast() }
        guard !lines.isEmpty else { return }
        if lines.count > maxLines { lines = Array(lines.suffix(maxLines)) }
        let encoder = JSONEncoder()
        var data = Data()
        for line in lines {
            guard let row = try? encoder.encode(ScrollbackEntry(attributes: [], text: line)) else { continue }
            data.append(row)
            data.append(0x0A)
        }
        let url = PersistenceManager.shared.scrollbackURL(terminalId: terminalId, workspaceId: workspaceId)
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: url, options: .atomic)
        } catch {
            fputs("⚠️ scrollback save failed for \(terminalId.uuidString.prefix(8)): \(error)\n", stderr)
        }
    }

    static func load(terminalId: UUID, workspaceId: UUID) -> String {
        guard let entries = try? ScrollbackStore().load(terminalId: terminalId, workspaceId: workspaceId), !entries.isEmpty else {
            return ""
        }
        return entries.map(\.text).joined(separator: "\n")
    }
}
