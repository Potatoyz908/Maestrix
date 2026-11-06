import Foundation

/// Nexus services of the headless server, started once from `Sources/Server/main.swift`.
///   * K3: the portal bridge (`LinuxPortalManager`) behind `PlatformHooks.portalsService`, so `omaestri portal …`
///     answers through the GTK window instead of hitting the `fatalError` of an empty hook.
@MainActor
public enum NexusServices {
    private static var started = false

    public static func start() {
        guard !started else { return }
        started = true
        if PlatformHooks.portalsService == nil {
            PlatformHooks.portalsService = LinuxPortalManager.shared
        }
    }
}
