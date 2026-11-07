import Foundation

/// 定时 Routine 配置
struct Routine: Codable, Identifiable {
    var id: UUID
    var name: String
    var prompts: [String]           // 用 && 分隔的多条提示
    var intervalSeconds: TimeInterval
    var targetTerminalId: UUID
    var isActive: Bool
    var createdAt: Date

    init(id: UUID = UUID(), name: String, prompt: String, intervalSeconds: TimeInterval, targetTerminalId: UUID) {
        self.id = id
        self.name = name
        self.prompts = Routine.parsePrompts(prompt)
        self.intervalSeconds = intervalSeconds
        self.targetTerminalId = targetTerminalId
        self.isActive = true
        self.createdAt = Date()
    }

    /// Splits a chained routine prompt. Per the reference, `&&` separates prompts only when it stands on its own line, so
    /// inline uses such as `make && make test` stay inside a single prompt. Empty prompts are dropped.
    static func parsePrompts(_ text: String) -> [String] {
        var prompts: [String] = []
        var current: [String] = []
        func flush() {
            let joined = current.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { prompts.append(joined) }
            current.removeAll()
        }
        for line in text.components(separatedBy: "\n") {
            if line.trimmingCharacters(in: .whitespacesAndNewlines) == "&&" {
                flush()
            } else {
                current.append(line)
            }
        }
        flush()
        return prompts
    }
}

struct RoutinesContainer: Codable {
    var routines: [Routine]
    init() { self.routines = [] }
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        // 兼容两种字段名：新格式用 routines，旧格式（Maestri原生）用 payload
        if let r = try? container.decode([Routine].self, forKey: .routines) {
            self.routines = r
        } else if let p = try? container.decode([Routine].self, forKey: .payload) {
            self.routines = p
        } else {
            self.routines = []
        }
    }
    private enum CodingKeys: String, CodingKey { case routines, payload }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(routines, forKey: .payload)  // 写入时用 payload 与 Maestri 格式一致
    }
}
