// Menu bar item for nightblock. A moon that fills in while the block is on; the menu has the override.
import Cocoa

final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var item: NSStatusItem!
    let menu = NSMenu()
    let header = NSMenuItem(title: "Night block", action: nil, keyEquivalent: "")
    let detail = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let overrideItem = NSMenuItem(title: "Override… (type the paragraph)", action: #selector(doOverride), keyEquivalent: "")
    let exceptionsItem = NSMenuItem(title: "Exceptions", action: nil, keyEquivalent: "")
    let exceptionsMenu = NSMenu()
    var timer: Timer?
    var blocked = false
    var lists: [String: [String]] = [:]

    func applicationDidFinishLaunching(_ n: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        header.isEnabled = false
        detail.isEnabled = false
        overrideItem.target = self
        let status = NSMenuItem(title: "Show status in Terminal", action: #selector(doStatus), keyEquivalent: "")
        status.target = self
        let denied = NSMenuItem(title: "Show recently refused hosts", action: #selector(doDenied), keyEquivalent: "")
        denied.target = self
        let folder = NSMenuItem(title: "Open config folder", action: #selector(doFolder), keyEquivalent: "")
        folder.target = self
        let quit = NSMenuItem(title: "Quit menu icon", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        exceptionsItem.submenu = exceptionsMenu
        for m in [header, detail, .separator(), overrideItem, .separator(), exceptionsItem, status, denied, folder, .separator(), quit] { menu.addItem(m) }
        menu.delegate = self
        item.menu = menu
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func menuWillOpen(_ menu: NSMenu) { refresh() }

    func refresh() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["/usr/local/bin/nightblock", "state-json"]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { setUnknown(); return }
        p.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let state = j["state"] as? String else { setUnknown(); return }
        let until = j["until"] as? String ?? "?"
        let why = j["why"] as? String ?? ""
        for k in ["allow_hosts", "direct_hosts", "cover_apps"] { lists[k] = j[k] as? [String] ?? [] }
        rebuildExceptions()
        blocked = state == "blocked"
        setIcon(symbol: blocked ? "moon.zzz.fill" : "moon")
        header.title = blocked ? "Internet blocked until \(until)" : "Internet open · blocks at \(until)"
        detail.title = why
        detail.isHidden = why.isEmpty
        overrideItem.isEnabled = blocked
        overrideItem.title = blocked ? "Override… (type the paragraph)" : "Override (nothing to override right now)"
        item.button?.toolTip = header.title
    }

    func setUnknown() {
        setIcon(symbol: "moon.circle")
        header.title = "nightblock not installed or not responding"
        detail.isHidden = true
        overrideItem.isEnabled = false
    }

    func setIcon(symbol: String) {
        let img = NSImage(systemSymbolName: symbol, accessibilityDescription: "Night block")
        img?.isTemplate = true
        item.button?.image = img
    }

    // MARK: exceptions

    struct Section { let key: String; let title: String; let addTitle: String; let prompt: String; let hint: String }
    let sections = [
        Section(key: "allow_hosts", title: "Websites allowed in Chrome", addTitle: "Add website…",
                prompt: "Hostname to allow in Chrome during the block", hint: "e.g. docs.google.com or *.gstatic.com"),
        Section(key: "direct_hosts", title: "API hosts allowed for every app", addTitle: "Add API host…",
                prompt: "Hostname to open in the firewall for all apps (by IP, port 443)", hint: "e.g. api.ouraring.com"),
        Section(key: "cover_apps", title: "Apps hidden while blocked", addTitle: "Add app…",
                prompt: "Application name exactly as shown in the menu bar", hint: "e.g. Slack"),
    ]

    func rebuildExceptions() {
        exceptionsMenu.removeAllItems()
        for (i, sec) in sections.enumerated() {
            if i > 0 { exceptionsMenu.addItem(.separator()) }
            let h = NSMenuItem(title: sec.title + " — click one to remove", action: nil, keyEquivalent: "")
            h.isEnabled = false
            exceptionsMenu.addItem(h)
            for v in lists[sec.key] ?? [] {
                let it = NSMenuItem(title: "    " + v, action: #selector(removeEntry(_:)), keyEquivalent: "")
                it.target = self
                it.representedObject = [sec.key, v]
                exceptionsMenu.addItem(it)
            }
            let add = NSMenuItem(title: "    " + sec.addTitle, action: #selector(addEntry(_:)), keyEquivalent: "")
            add.target = self
            add.representedObject = sec.key
            exceptionsMenu.addItem(add)
        }
        exceptionsMenu.addItem(.separator())
        let edit = NSMenuItem(title: "Edit config file in Terminal…", action: #selector(doEditConfig), keyEquivalent: "")
        edit.target = self
        exceptionsMenu.addItem(edit)
    }

    @objc func removeEntry(_ sender: NSMenuItem) {
        guard let pair = sender.representedObject as? [String], pair.count == 2 else { return }
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = "Remove \"\(pair[1])\" from the exceptions?"
        a.informativeText = pair[0] == "cover_apps" ? "It will no longer be hidden during the block." : "It will be blocked from midnight to 6:00."
        a.addButton(withTitle: "Remove")
        a.addButton(withTitle: "Cancel")
        if a.runModal() == .alertFirstButtonReturn { runAsAdmin(["config-remove", pair[0], pair[1]]) }
    }

    @objc func addEntry(_ sender: NSMenuItem) {
        guard let key = sender.representedObject as? String, let sec = sections.first(where: { $0.key == key }) else { return }
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = sec.addTitle.replacingOccurrences(of: "…", with: "")
        a.informativeText = sec.prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        field.placeholderString = sec.hint
        a.accessoryView = field
        a.addButton(withTitle: "Add")
        a.addButton(withTitle: "Cancel")
        a.window.initialFirstResponder = field
        if a.runModal() == .alertFirstButtonReturn {
            let v = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            if !v.isEmpty { runAsAdmin(["config-add", key, v]) }
        }
    }

    func runAsAdmin(_ args: [String]) {
        // Native admin-password dialog; the change is logged by nightblock and applied within a minute.
        let quoted = (["/usr/bin/python3", "/usr/local/bin/nightblock"] + args)
            .map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }.joined(separator: " ")
        let src = "do shell script \"" + quoted.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\" with administrator privileges"
        var err: NSDictionary?
        NSAppleScript(source: src)?.executeAndReturnError(&err)
        if let err = err, (err["NSAppleScriptErrorNumber"] as? Int) != -128 {  // -128 = user cancelled
            let a = NSAlert()
            a.messageText = "Could not change the exceptions"
            a.informativeText = err["NSAppleScriptErrorMessage"] as? String ?? "\(err)"
            a.runModal()
        }
        refresh()
    }

    @objc func doEditConfig() { runCommandFile("edit-config") }

    func runCommandFile(_ name: String) {
        // Opening a .command file launches Terminal and runs it: no Automation permission needed.
        NSWorkspace.shared.open(URL(fileURLWithPath: "/Library/Application Support/nightblock/\(name).command"))
    }
    @objc func doOverride() { runCommandFile("override") }
    @objc func doStatus() { runCommandFile("status") }
    @objc func doDenied() { runCommandFile("denied") }
    @objc func doFolder() { NSWorkspace.shared.open(URL(fileURLWithPath: "/Library/Application Support/nightblock")) }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
