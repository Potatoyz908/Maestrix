// Sources/Server/main.swift
import Foundation
import Dispatch
import OpenMaestriCore

@MainActor
func runServer() {
    print("🚀 Starting Open Maestri Core Server...")
    do {
        try PersistenceManager.shared.ensureDirectoriesExist()
    } catch {
        fputs("⚠️ Could not ensure app directories: \(error)\n", stderr)
    }

    do {
        try InterAgentServer.shared.start()
        print("✅ Open Maestri Server active on port \(InterAgentServer.shared.port)")
        print("📡 Unix Domain Socket: \(InterAgentServer.globalSocketPath)")
        fflush(stdout)
    } catch {
        fputs("❌ Failed to start InterAgentServer: \(error)\n", stderr)
        exit(1)
    }
}

Task { @MainActor in
    runServer()
}

dispatchMain()
