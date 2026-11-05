import AppKit
import Foundation

extension TerminalManager: TerminalManaging {
    func bufferText(for terminalId: UUID) -> String? {
        guard let termView = providers[terminalId]?.terminalView else { return nil }
        let data = termView.getTerminal().getBufferAsData()
        return String(decoding: data, as: UTF8.self)
    }

    func observeOutput(for terminalId: UUID, _ handler: @escaping (String) -> Void) -> (() -> Void)? {
        guard let provider = providers[terminalId] else { return nil }
        let previous = provider.onDataReceived
        provider.onDataReceived = { text in
            previous?(text)
            handler(text)
        }
        return { [weak provider] in provider?.onDataReceived = previous }
    }
}
