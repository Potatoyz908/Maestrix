import XCTest
@testable import OpenMaestriCore

final class LinuxPtyAndServerTests: XCTestCase {

    func testInterAgentServerDynamicPortAndUnixSocket() async throws {
        try await MainActor.run {
            let server = InterAgentServer.shared
            try server.start()
            defer { server.stop() }

            XCTAssertGreaterThan(server.port, 0, "InterAgentServer should be assigned a positive dynamic TCP port")
            XCTAssertTrue(FileManager.default.fileExists(atPath: InterAgentServer.globalSocketPath),
                          "Unix socket file should exist at \(InterAgentServer.globalSocketPath)")
        }
    }

    func testPOSIXPtyProcessLifecycleAndBuffer() async throws {
        let tid = UUID()
        let proc = POSIXPtyProcess(terminalId: tid, command: "echo SENTINELA_PTY_READY", workingDirectory: "/tmp")

        let exp = expectation(description: "PTY data received")
        var received = ""

        proc.onDataReceived = { text in
            received += text
            if received.contains("SENTINELA_PTY_READY") {
                exp.fulfill()
            }
        }

        try proc.start()
        defer { proc.terminate() }

        await fulfillment(of: [exp], timeout: 5.0)

        let buffer = proc.bufferText()
        XCTAssertNotNil(buffer, "bufferText should not be nil")
        XCTAssertTrue(buffer?.contains("SENTINELA_PTY_READY") == true, "Buffer should contain echo output")
    }
}
