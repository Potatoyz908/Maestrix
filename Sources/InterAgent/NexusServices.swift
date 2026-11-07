import Foundation

/// Nexus services of the headless server, started once from `Sources/Server/main.swift`.
///   * K3: the portal bridge (`LinuxPortalManager`) behind `PlatformHooks.portalsService`, so `omaestri portal …`
///     answers through the GTK window instead of hitting the `fatalError` of an empty hook.
///   * K13: terminal scrollback saved to disk every `scrollbackInterval` seconds and on SIGTERM/SIGINT/SIGHUP.
@MainActor
public enum NexusServices {
    private static var started = false
    private static var scrollbackTimer: DispatchSourceTimer?
    private static var skillTimer: DispatchSourceTimer?
    private static var lastPreferencesData: Data?
    private static var signalSources: [DispatchSourceSignal] = []
    static let scrollbackInterval: TimeInterval = 30

    public static func start() {
        guard !started else { return }
        started = true
        if PlatformHooks.portalsService == nil {
            PlatformHooks.portalsService = LinuxPortalManager.shared
        }
        lastPreferencesData = PersistenceManager.shared.preferencesSnapshot()
        let skills = DispatchSource.makeTimerSource(queue: .main)
        skills.schedule(deadline: .now() + 2, repeating: 2)
        skills.setEventHandler {
            let data = PersistenceManager.shared.preferencesSnapshot()
            guard data != lastPreferencesData else { return }
            lastPreferencesData = data
            SkillInjector.shared.installSkillsIfNeeded()
        }
        skills.resume()
        skillTimer = skills
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + scrollbackInterval, repeating: scrollbackInterval)
        timer.setEventHandler { MainActor.assumeIsolated { LinuxTerminalManager.shared.saveAllScrollback() } }
        timer.resume()
        scrollbackTimer = timer
        for sig in [SIGTERM, SIGINT, SIGHUP] {
            signal(sig, SIG_IGN)   // handled by the dispatch source below
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler {
                MainActor.assumeIsolated { LinuxTerminalManager.shared.saveAllScrollback() }
                exit(0)
            }
            src.resume()
            signalSources.append(src)
        }
    }
}
