import Foundation

/// Entry point of the Nexus CLI commands (`partitura`, `routine`, `workspace`, `floor`).
enum NexusCLI {
    static func route(args: [String], terminalId: UUID?) async -> String {
        switch args.first {
        case "partitura":
            return await PartituraHandler.shared.handleAsync(args: args, terminalId: terminalId)
        default:
            return "error: '\(args.first ?? "")' is not available yet"
        }
    }
}
