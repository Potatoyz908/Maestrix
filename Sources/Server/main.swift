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

    // K5: agents started here learn the `omaestri` CLI (idempotent; <dataDir>/skills when OPEN_MAESTRI_DATA_DIR is set)
    SkillInjector.shared.installSkillsIfNeeded()
    // K6: connections persisted in every workspace.json are live again (ask/list/check/note) right after a restart
    ConnectionManager.shared.restoreAllWorkspaces()

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
