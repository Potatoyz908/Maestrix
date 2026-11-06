import Foundation
#if canImport(OSLog)
import OSLog
#endif

final class PortalHandler {
    static let shared = PortalHandler()
    private let logger = Logger.make(category: "PortalHandler")
    private init() {}

    func handle(args: [String], terminalId: UUID?) -> String {
        let semaphore = DispatchSemaphore(value: 0)
        var result = ""
        Task.detached { result = await self.handleAsync(args: args, terminalId: terminalId); semaphore.signal() }
        semaphore.wait()
        return result
    }

    func handleAsync(args: [String], terminalId: UUID?) async -> String {
        guard args.count >= 2 else {
            return """
            error: usage: omaestri portal <subcommand> ...
            Subcommands: create close edit navigate back forward reload(=refresh) screenshot snapshot html text info
                         click fill type key hover scroll drag wait evaluate
                         select check uncheck focus
                         scrollintoview selectall clear
            """
        }
        let subcommand = args[1]
        let args = await withImplicitPortalName(args, terminalId: terminalId)
        do {
            switch subcommand {
            case "create":     return await handleCreate(args: args, terminalId: terminalId)
            case "close":      return await handleClose(args: args, terminalId: terminalId)
            case "edit":       return try await handleEdit(args: args, terminalId: terminalId)
            case "navigate":   return try await handleNavigate(args: args, terminalId: terminalId)
            case "back":       return try await handleBack(args: args, terminalId: terminalId)
            case "forward":    return try await handleForward(args: args, terminalId: terminalId)
            case "reload", "refresh": return try await handleReload(args: args, terminalId: terminalId)
            case "screenshot": return try await handleScreenshot(args: args, terminalId: terminalId)
            case "snapshot":   return try await handleSnapshot(args: args, terminalId: terminalId)
            case "html":       return try await handleHTML(args: args, terminalId: terminalId)
            case "text":       return try await handleText(args: args, terminalId: terminalId)
            case "info":       return try await handleInfo(args: args, terminalId: terminalId)
            case "click":      return try await handleClick(args: args, terminalId: terminalId)
            case "fill":       return try await handleFill(args: args, terminalId: terminalId)
            case "type":       return try await handleType(args: args, terminalId: terminalId)
            case "key":        return try await handleKey(args: args, terminalId: terminalId)
            case "hover":      return try await handleHover(args: args, terminalId: terminalId)
            case "scroll":     return try await handleScroll(args: args, terminalId: terminalId)
            case "drag":       return try await handleDrag(args: args, terminalId: terminalId)
            case "wait":       return try await handleWait(args: args, terminalId: terminalId)
            case "evaluate":   return try await handleEvaluate(args: args, terminalId: terminalId)
            case "select":     return try await handleSelect(args: args, terminalId: terminalId)
            case "check":      return try await handleCheck(args: args, terminalId: terminalId)
            case "uncheck":    return try await handleUncheck(args: args, terminalId: terminalId)
            case "focus":      return try await handleFocus(args: args, terminalId: terminalId)
            case "scrollintoview": return try await handleScrollIntoView(args: args, terminalId: terminalId)
            case "selectall":     return try await handleSelectAll(args: args, terminalId: terminalId)
            case "clear":         return try await handleClear(args: args, terminalId: terminalId)
            case "logs-start": return try await handleLogsStart(args: args, terminalId: terminalId)
            case "logs":       return try await handleLogs(args: args, terminalId: terminalId)
            default:
                return "error: unknown portal subcommand '\(subcommand)'"
            }
        } catch {
            return "error: \(error.localizedDescription)"
        }
    }

    // MARK: - create

    private func handleCreate(args: [String], terminalId: UUID?) async -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal create \"URL\" [\"Name\"]" }
        let url = args[2]
        let name = args.count >= 4 ? args[3] : "Portal-\(UUID().uuidString.prefix(6))"
        if await PlatformHooks.portalsService is LinuxPortalManager {
            return await createOnLinux(url: url, name: name, terminalId: terminalId)
        }
        let portalId = UUID()
        await PlatformHooks.portals.ensureWebView(for: portalId, initialURL: url)

        var pc = PortalContent(name: name, url: url)
        pc.id = portalId
        let portalNode = CanvasNode(id: portalId, frame: .zero, content: .portal(pc))

        var userInfo: [AnyHashable: Any] = ["portalNode": portalNode]
        if let tid = terminalId { userInfo["terminalId"] = tid }
        NotificationCenter.default.post(name: .portalCreatedViaCLI, object: nil, userInfo: userInfo)

        return "Created portal '\(name)' [\(portalId.uuidString.prefix(8))]"
    }

    // MARK: - edit

    private func handleEdit(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal edit \"PortalName\" [--url URL]" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        var i = 3
        while i < args.count {
            if args[i] == "--url" && i + 1 < args.count {
                try await PlatformHooks.portals.navigate(portalId: portalId, to: args[i+1])
                i += 2
            } else { i += 1 }
        }
        return "Portal updated"
    }

    // MARK: - navigate

    private func handleNavigate(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal navigate \"PortalName\" \"URL\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        try await PlatformHooks.portals.navigate(portalId: portalId, to: args[3])
        return "Navigating to \(args[3])"
    }

    // MARK: - back

    private func handleBack(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal back \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        try await PlatformHooks.portals.goBack(portalId: portalId)
        return "went back"
    }

    // MARK: - forward

    private func handleForward(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal forward \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        try await PlatformHooks.portals.goForward(portalId: portalId)
        return "went forward"
    }

    // MARK: - reload

    private func handleReload(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal reload \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        try await PlatformHooks.portals.reload(portalId: portalId)
        return "reloaded"
    }

    // MARK: - screenshot

    private func handleScreenshot(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal screenshot \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let b64 = try await PlatformHooks.portals.screenshot(portalId: portalId)
        return b64.isEmpty ? "error: screenshot failed" : b64
    }

    // MARK: - snapshot (accessibility tree)

    private func handleSnapshot(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal snapshot \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        guard let tree = await PlatformHooks.portals.accessibilityTree(for: portalId) else {
            return "error: WebView not ready"
        }
        return tree
    }

    // MARK: - html

    private func handleHTML(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal html \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        return try await exec("html", portalId, js: "document.documentElement.outerHTML", argv: args)
    }

    // MARK: - text

    private func handleText(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal text \"PortalName\" @e1" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let ref = args[3]
        if ref.hasPrefix("@e") {
            let idx = ref.replacingOccurrences(of: "@e", with: "")
            guard let index = Int(idx) else { return "error: invalid element reference '\(ref)'" }
            let js = "(function(){const el=document.querySelectorAll('a,button,input,select,textarea,[role]')[\(index-1)];return el?el.innerText||el.value||el.textContent:'error: not found';})()"
            return try await exec("text", portalId, js: js, argv: args)
        } else {
            // CSS selector
            let escaped = ref.replacingOccurrences(of: "\"", with: "\\\"")
            let js = "(function(){const el=document.querySelector(\"\(escaped)\");return el?el.innerText||el.value||el.textContent:'error: not found';})()"
            return try await exec("text", portalId, js: js, argv: args)
        }
    }

    // MARK: - info

    private func handleInfo(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal info \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        if await PlatformHooks.portalsService is LinuxPortalManager {
            return try await exec("info", portalId, js: "'URL: ' + window.location.href + '\\nTitle: ' + document.title + '\\nViewport: ' + window.innerWidth + 'x' + window.innerHeight", argv: args)
        }
        let url   = try await PlatformHooks.portals.evaluate(portalId: portalId, javascript: "window.location.href")
        let title = try await PlatformHooks.portals.evaluate(portalId: portalId, javascript: "document.title")
        let vw    = try await PlatformHooks.portals.evaluate(portalId: portalId, javascript: "window.innerWidth + 'x' + window.innerHeight")
        return "URL: \(url)\nTitle: \(title)\nViewport: \(vw)"
    }

    // MARK: - click

    private func handleClick(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal click \"PortalName\" @e1" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let ref = args[3]
        let js = buildClickJS(ref: ref)
        return try await exec("click", portalId, js: js, argv: args)
    }

    private func buildClickJS(ref: String) -> String {
        if ref.hasPrefix("@e"), let index = Int(ref.replacingOccurrences(of: "@e", with: "")) {
            return "(function(){const el=document.querySelectorAll('a,button,input,select,textarea,[role]')[\(index-1)];if(!el)return'error: not found';el.click();return'clicked';})()"
        } else if ref.contains(",") {
            let parts = ref.split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
            if parts.count >= 2 {
                return "(function(){document.elementFromPoint(\(parts[0]),\(parts[1]))?.click();return'clicked';})()"
            }
        }
        let escaped = ref.replacingOccurrences(of: "\"", with: "\\\"")
        return "(function(){const el=document.querySelector(\"\(escaped)\");if(!el)return'error: not found';el.click();return'clicked';})()"
    }

    // MARK: - fill

    private func handleFill(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 5 else { return "error: usage: omaestri portal fill \"PortalName\" @e1 \"value\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let ref = args[3]
        let value = args[4].replacingOccurrences(of: "\"", with: "\\\"")
        let selector: String
        if ref.hasPrefix("@e"), let index = Int(ref.replacingOccurrences(of: "@e", with: "")) {
            selector = "document.querySelectorAll('input,textarea,select')[\(index-1)]"
        } else {
            let escaped = ref.replacingOccurrences(of: "\"", with: "\\\"")
            selector = "document.querySelector(\"\(escaped)\")"
        }
        let js = "(function(){const el=\(selector);if(!el)return'error: not found';el.value='';el.value=\"\(value)\";el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return'filled';})()"
        return try await exec("fill", portalId, js: js, argv: args)
    }

    // MARK: - type

    private func handleType(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal type \"PortalName\" \"text\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let text = args[3].replacingOccurrences(of: "\"", with: "\\\"")
        let js = """
        (function(){
          const el = document.activeElement;
          if(!el) return 'error: no focused element';
          const val = el.value || '';
          el.value = val + "\(text)";
          el.dispatchEvent(new Event('input',{bubbles:true}));
          return 'typed';
        })()
        """
        return try await exec("type", portalId, js: js, argv: args)
    }

    // MARK: - key

    private func handleKey(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal key \"PortalName\" \"Enter\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let keyName = args[3]
        let js = """
        (function(){
          const el = document.activeElement || document.body;
          const ev = new KeyboardEvent('keydown', {key:'\(keyName)', bubbles:true});
          el.dispatchEvent(ev);
          el.dispatchEvent(new KeyboardEvent('keyup', {key:'\(keyName)', bubbles:true}));
          return 'key pressed';
        })()
        """
        return try await exec("key", portalId, js: js, argv: args)
    }

    // MARK: - hover

    private func handleHover(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal hover \"PortalName\" @e1" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let ref = args[3]
        let selector = buildSelector(ref: ref)
        let js = "(function(){const el=\(selector);if(!el)return'error: not found';el.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));el.dispatchEvent(new MouseEvent('mouseenter',{bubbles:true}));return'hovered';})()"
        return try await exec("hover", portalId, js: js, argv: args)
    }

    // MARK: - scroll

    private func handleScroll(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal scroll \"PortalName\" down 300" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let direction = args[3]
        let amount = args.count >= 5 ? (Int(args[4]) ?? 300) : 300
        let (dx, dy): (Int, Int)
        switch direction {
        case "up":    (dx, dy) = (0, -amount)
        case "down":  (dx, dy) = (0, amount)
        case "left":  (dx, dy) = (-amount, 0)
        case "right": (dx, dy) = (amount, 0)
        default:      (dx, dy) = (0, amount)
        }
        let js = "window.scrollBy(\(dx), \(dy)); 'scrolled'"
        return try await exec("scroll", portalId, js: js, argv: args)
    }

    // MARK: - drag

    private func handleDrag(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 5 else { return "error: usage: omaestri portal drag \"PortalName\" @e1 @e2" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let fromSelector = buildSelector(ref: args[3])
        let toSelector   = buildSelector(ref: args[4])
        let js = """
        (function(){
          const src = \(fromSelector); const dst = \(toSelector);
          if(!src||!dst) return 'error: element not found';
          const r1 = src.getBoundingClientRect(), r2 = dst.getBoundingClientRect();
          const fire = (el,t,x,y) => el.dispatchEvent(new MouseEvent(t,{bubbles:true,clientX:x,clientY:y}));
          fire(src,'mousedown',r1.x+r1.width/2,r1.y+r1.height/2);
          fire(src,'dragstart',r1.x+r1.width/2,r1.y+r1.height/2);
          fire(dst,'dragover',r2.x+r2.width/2,r2.y+r2.height/2);
          fire(dst,'drop',r2.x+r2.width/2,r2.y+r2.height/2);
          fire(src,'dragend',r2.x+r2.width/2,r2.y+r2.height/2);
          return 'dragged';
        })()
        """
        return try await exec("drag", portalId, js: js, argv: args)
    }

    // MARK: - wait

    private func handleWait(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal wait \"PortalName\" @e1 [timeoutMs]" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let ref = args[3]
        let timeoutMs = args.count >= 5 ? (Int(args[4]) ?? 5000) : 5000
        let selector = buildSelectorString(ref: ref)
        let js = """
        new Promise((resolve) => {
          const deadline = Date.now() + \(timeoutMs);
          const check = () => {
            const el = document.querySelector('\(selector)');
            if(el) { resolve('found'); return; }
            if(Date.now() >= deadline) { resolve('timeout'); return; }
            setTimeout(check, 100);
          };
          check();
        })
        """
        return try await exec("wait", portalId, js: js, argv: args)
    }

    // MARK: - evaluate

    private func handleEvaluate(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal evaluate \"PortalName\" \"js\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        return try await exec("evaluate", portalId, js: args[3], argv: args)
    }



    // MARK: - logs-start

    private func handleLogsStart(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal logs-start \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let js = """
        (function(){
          if(window.__maestriLogsActive) return 'already started';
          window.__maestriLogs = [];
          window.__maestriLogsActive = true;
          const orig = {log: console.log, warn: console.warn, error: console.error, info: console.info};
          ['log','warn','error','info'].forEach(function(level){
            console[level] = function(){
              const args = Array.from(arguments).map(function(a){
                try { return typeof a === 'object' ? JSON.stringify(a) : String(a); } catch(e){ return String(a); }
              });
              window.__maestriLogs.push('[' + level.toUpperCase() + '] ' + args.join(' '));
              orig[level].apply(console, arguments);
            };
          });
          return 'started';
        })()
        """
        return try await exec("logs-start", portalId, js: js, argv: args)
    }

    // MARK: - logs

    private func handleLogs(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal logs \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let js = """
        (function(){
          const logs = window.__maestriLogs || [];
          window.__maestriLogs = [];
          return logs.length > 0 ? logs.join('\\n') : '(no logs)';
        })()
        """
        return try await exec("logs", portalId, js: js, argv: args)
    }

    // MARK: - select

    private func handleSelect(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 5 else { return "error: usage: omaestri portal select \"PortalName\" @e1 \"Option Text\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let option = args[4].replacingOccurrences(of: "\"", with: "\\\"")
        let selector = buildSelector(ref: args[3])
        let js = """
        (function(){
          const el = \(selector);
          if(!el) return 'error: not found';
          const options = Array.from(el.options || []);
          const opt = options.find(o => o.text === "\(option)" || o.value === "\(option)");
          if(!opt) return 'error: option not found';
          el.value = opt.value;
          el.dispatchEvent(new Event('change', {bubbles: true}));
          el.dispatchEvent(new Event('input', {bubbles: true}));
          return 'selected';
        })()
        """
        return try await exec("select", portalId, js: js, argv: args)
    }

    // MARK: - check

    private func handleCheck(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal check \"PortalName\" @e1" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let selector = buildSelector(ref: args[3])
        let js = "(function(){const el=\(selector);if(!el)return'error: not found';el.checked=true;el.dispatchEvent(new Event('change',{bubbles:true}));return'checked';})()"
        return try await exec("check", portalId, js: js, argv: args)
    }

    // MARK: - uncheck

    private func handleUncheck(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal uncheck \"PortalName\" @e1" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let selector = buildSelector(ref: args[3])
        let js = "(function(){const el=\(selector);if(!el)return'error: not found';el.checked=false;el.dispatchEvent(new Event('change',{bubbles:true}));return'unchecked';})()"
        return try await exec("check", portalId, js: js, argv: args)
    }

    // MARK: - focus

    private func handleFocus(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal focus \"PortalName\" @e1" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let selector = buildSelector(ref: args[3])
        let js = "(function(){const el=\(selector);if(!el)return'error: not found';el.focus();return'focused';})()"
        return try await exec("focus", portalId, js: js, argv: args)
    }

    // MARK: - scrollintoview / selectall / clear

    private func handleScrollIntoView(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal scrollintoview \"PortalName\" @e1" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let sel = buildSelector(ref: args[3])
        let js = "(function(){const el=\(sel);if(!el)return'error: not found';el.scrollIntoView({block:'center'});return'scrolled into view';})()"
        return try await exec("scroll", portalId, js: js, argv: args)
    }

    private func handleSelectAll(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal selectall \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let js = "document.execCommand('selectAll', false, null); 'selected all'"
        return try await exec("focus", portalId, js: js, argv: args)
    }

    private func handleClear(args: [String], terminalId: UUID?) async throws -> String {
        guard args.count >= 4 else { return "error: usage: omaestri portal clear \"PortalName\" @e1" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        let sel = buildSelector(ref: args[3])
        let js = "(function(){const el=\(sel);if(!el)return'error: not found';el.value='';el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return'cleared';})()"
        return try await exec("fill", portalId, js: js, argv: args)
    }

        // MARK: - Portal ID 解析

    /// PO15: the portal NAMED `name` among the nodes connected to the caller (exact name first, then
    /// case-insensitive, then substring). Without a caller (not run from a terminal) nothing resolves.
    @MainActor
    private func resolvePortalId(name: String, callerTid: UUID?) -> UUID? {
        guard let tid = callerTid else { return nil }
        let connected = NexusWorkspaces.connectedNodeIds(of: tid)
        var portals: [(id: UUID, name: String)] = []
        for id in connected.ids {
            guard let found = NexusWorkspaces.locate(id, preferring: connected.wsId),
                  case .portal(let p) = found.node.content else { continue }
            portals.append((found.node.id, p.name))
        }
        if portals.isEmpty {   // macOS: live WebViews connected to the caller
            return ConnectionManager.shared.connectedNodeIds(for: tid).first { PlatformHooks.portals.hasWebView(for: $0) }
        }
        let wanted = name.trimmingCharacters(in: .whitespaces)
        if let hit = portals.first(where: { $0.name == wanted }) { return hit.id }
        if let hit = portals.first(where: { $0.name.caseInsensitiveCompare(wanted) == .orderedSame }) { return hit.id }
        if let hit = portals.first(where: { $0.name.localizedCaseInsensitiveContains(wanted) }) { return hit.id }
        if let id = UUID(uuidString: wanted), portals.contains(where: { $0.id == id }) { return id }
        return nil
    }

    /// Argument count each subcommand needs WITH the portal name (`portal <sub> "Name" ...`).
    private static let arity: [String: Int] = [
        "navigate": 4, "back": 3, "forward": 3, "reload": 3, "refresh": 3, "screenshot": 3, "snapshot": 3, "html": 3,
        "text": 4, "info": 3, "click": 4, "fill": 5, "type": 4, "key": 4, "hover": 4, "scroll": 4, "drag": 5, "wait": 4,
        "evaluate": 4, "select": 5, "check": 4, "uncheck": 4, "focus": 4, "scrollintoview": 4, "selectall": 3, "clear": 4,
        "logs-start": 3, "logs": 3, "close": 3,
    ]

    /// `portal evaluate "js"` with the name left out: when the caller is connected to exactly ONE portal, that portal
    /// is meant (the name is inserted). Anything ambiguous keeps the original args (and fails with a usage error).
    @MainActor
    private func withImplicitPortalName(_ args: [String], terminalId: UUID?) -> [String] {
        guard args.count >= 2, let need = Self.arity[args[1]], args.count == need - 1, let tid = terminalId else { return args }
        let connected = NexusWorkspaces.connectedNodeIds(of: tid)
        let names: [String] = connected.ids.compactMap { id in
            guard let f = NexusWorkspaces.locate(id, preferring: connected.wsId), case .portal(let p) = f.node.content else { return nil }
            return p.name
        }
        guard names.count == 1 else { return args }
        var out = args
        out.insert(names[0], at: 2)
        return out
    }

    // MARK: - Linux (K3): ops to the GUI, node changes through workspace.json + K2

    /// Interaction/read ops: on Linux the GUI gets the high-level `op` plus the ready-made `js` (it may run either);
    /// elsewhere the JS runs in the local WebView as before.
    private func exec(_ op: String, _ portalId: UUID, js: String, argv: [String]) async throws -> String {
        if let bridge = await PlatformHooks.portalsService as? LinuxPortalManager {
            return try await bridge.command(op, portalId: portalId, args: ["js": js, "argv": Array(argv.dropFirst(3))])
        }
        return try await PlatformHooks.portals.evaluate(portalId: portalId, javascript: js)
    }

    @MainActor
    private func createOnLinux(url: String, name: String, terminalId: UUID?) -> String {
        guard let tid = terminalId, let caller = NexusWorkspaces.locate(tid) else {
            return "error: portal create must run from a terminal on the canvas"
        }
        var pc = PortalContent(name: name, url: url)
        let portalId = UUID()
        pc.id = portalId
        let frame = NexusWorkspaces.placement(rightOf: caller.node, size: CGSize(width: 800, height: 600), in: caller.doc)
        do {
            try ServerWorkspace.addNode(CanvasNode(id: portalId, frame: frame, content: .portal(pc)), to: caller.wsId)
            try ServerWorkspace.addConnection(in: caller.wsId, a: caller.node.id, b: portalId, kind: .terminalToPortal)
        } catch {
            return "error: \(error)"
        }
        return "Created portal '\(name)' [\(portalId.uuidString.prefix(8))]"
    }

    private func handleClose(args: [String], terminalId: UUID?) async -> String {
        guard args.count >= 3 else { return "error: usage: omaestri portal close \"PortalName\"" }
        guard let portalId = await resolvePortalId(name: args[2], callerTid: terminalId) else {
            return "error: portal '\(args[2])' not found"
        }
        guard let found = NexusWorkspaces.locate(portalId) else { return "error: portal '\(args[2])' not found" }
        do {
            try await MainActor.run { _ = try ServerWorkspace.removeNode(portalId, from: found.wsId) }
        } catch {
            return "error: \(error)"
        }
        return "Closed portal '\(args[2])'"
    }

    // MARK: - JS 工具

    private func buildSelector(ref: String) -> String {
        if ref.hasPrefix("@e"), let index = Int(ref.replacingOccurrences(of: "@e", with: "")) {
            return "document.querySelectorAll('a,button,input,select,textarea,[role]')[\(index-1)]"
        }
        let escaped = ref.replacingOccurrences(of: "\"", with: "\\\"")
        return "document.querySelector(\"\(escaped)\")"
    }

    private func buildSelectorString(ref: String) -> String {
        if ref.hasPrefix("@e"), let index = Int(ref.replacingOccurrences(of: "@e", with: "")) {
            return "a,button,input,select,textarea,[role]:nth-child(\(index))"
        }
        return ref.replacingOccurrences(of: "'", with: "\\'")
    }
}
