import Foundation
import AppKit
import WebKit
import Darwin

final class LocalBooksApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, WKScriptMessageHandler {
    let fm = FileManager.default
    var support: URL!
    var runtime: URL!
    var data: URL!
    var server: Process?
    var desktopLock: FileHandle?
    var mainWindow: NSWindow?
    var windows: [NSWindow] = []
    var webViews: [ObjectIdentifier: WKWebView] = [:]
    var localPort: Int?
    var allowMainClose = false
    let noOpen = CommandLine.arguments.contains("--no-open")

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            support = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("LocalBooks", isDirectory: true)
            if let index = CommandLine.arguments.firstIndex(of: "--test-support"), CommandLine.arguments.count > index + 1 {
                support = URL(fileURLWithPath: CommandLine.arguments[index + 1], isDirectory: true)
            }
            runtime = support.appendingPathComponent("app", isDirectory: true)
            data = support.appendingPathComponent("data", isDirectory: true)
            try fm.createDirectory(at: runtime, withIntermediateDirectories: true)
            try fm.createDirectory(at: data, withIntermediateDirectories: true)
            try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: support.path)
            try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: data.path)
            if !noOpen {
                guard try claimDesktopInstance() else { NSApp.terminate(nil); return }
            }
            let running = serverIsRunning()
            if !running { try installRuntime(); try startServer() }
            if noOpen { NSApp.terminate(nil); return }
            installMenu()
            let configuration = WKWebViewConfiguration()
            configuration.userContentController.add(self, name: "localbooks")
            let view = createWindow(title: "LocalBooks", configuration: configuration, main: true)
            NSApp.activate(ignoringOtherApps: true)
            waitForServer(view, expectedPID: running ? nil : server?.processIdentifier, attempt: 0)
        } catch { fail(error.localizedDescription) }
    }

    func claimDesktopInstance() throws -> Bool {
        let file = support.appendingPathComponent("desktop.lock")
        // The server outlives the window; it must not inherit the desktop lock.
        let fd = Darwin.open(file.path, O_RDWR | O_CREAT | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        if flock(fd, LOCK_EX | LOCK_NB) != 0 {
            let error = errno
            Darwin.close(fd)
            guard error == EWOULDBLOCK else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(error)) }
            if let owner = try? String(contentsOf: file, encoding: .utf8),
               let pid = Int32(owner.trimmingCharacters(in: .whitespacesAndNewlines)),
               let existing = NSRunningApplication(processIdentifier: pid) {
                existing.activate(options: [.activateAllWindows])
            }
            return false
        }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        try handle.truncate(atOffset: 0)
        try handle.write(contentsOf: Data("\(ProcessInfo.processInfo.processIdentifier)\n".utf8))
        desktopLock = handle
        return true
    }

    func serverIsRunning() -> Bool {
        let fd = Darwin.open(data.appendingPathComponent("server.lock").path, O_RDWR | O_CREAT, 0o600)
        guard fd >= 0 else { return false }
        defer { Darwin.close(fd) }
        if flock(fd, LOCK_EX | LOCK_NB) != 0 { return true }
        flock(fd, LOCK_UN)
        return false
    }

    var bundledBackend: URL? {
        guard let executable = Bundle.main.resourceURL?.appendingPathComponent("backend/LocalBooksBackend"),
              fm.isExecutableFile(atPath: executable.path) else { return nil }
        return executable
    }

    func installRuntime() throws {
        if bundledBackend != nil { return }
        guard let bundled = Bundle.main.resourceURL?.appendingPathComponent("runtime"),
              let files = fm.enumerator(at: bundled, includingPropertiesForKeys: [.isDirectoryKey]) else {
            throw NSError(domain: "LocalBooks", code: 1, userInfo: [NSLocalizedDescriptionKey: "The bundled runtime is missing."])
        }
        for case let source as URL in files {
            let relative = String(source.path.dropFirst(bundled.path.count + 1))
            let target = runtime.appendingPathComponent(relative)
            if try source.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true {
                try fm.createDirectory(at: target, withIntermediateDirectories: true)
            } else {
                let bytes = try Data(contentsOf: source)
                if (try? Data(contentsOf: target)) != bytes { try bytes.write(to: target, options: .atomic) }
            }
        }
    }

    func startServer() throws {
        let log = data.appendingPathComponent("app.log")
        if !fm.fileExists(atPath: log.path) { fm.createFile(atPath: log.path, contents: nil) }
        let handle = try FileHandle(forWritingTo: log)
        try handle.seekToEnd()
        let process = Process()
        process.executableURL = bundledBackend ?? URL(fileURLWithPath: "/opt/homebrew/bin/python3.11")
        process.arguments = (bundledBackend == nil ? [runtime.appendingPathComponent("app.py").path] : []) + ["--data", data.path]
        process.currentDirectoryURL = runtime
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = handle
        process.standardError = handle
        try process.run()
        server = process
    }

    func waitForServer(_ view: WKWebView, expectedPID: Int32?, attempt: Int) {
        let file = data.appendingPathComponent("runtime.json")
        if let bytes = try? Data(contentsOf: file),
           let info = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any],
           let pid = info["pid"] as? Int32, (expectedPID == nil || pid == expectedPID),
           let address = info["url"] as? String, let url = URL(string: address),
           url.scheme == "http", url.host == "127.0.0.1", let port = url.port, (1...65535).contains(port) {
            localPort = port
            view.load(URLRequest(url: url))
            return
        }
        if attempt >= 150 || (server != nil && server?.isRunning == false) {
            fail("The local server could not start. Check the LocalBooks log in Application Support.")
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self, weak view] in
            guard let self = self, let view = view else { return }
            self.waitForServer(view, expectedPID: expectedPID, attempt: attempt + 1)
        }
    }

    @discardableResult func createWindow(title: String, configuration: WKWebViewConfiguration, main: Bool = false) -> WKWebView {
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        view.allowsBackForwardNavigationGestures = true
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: main ? 1280 : 900, height: main ? 850 : 1000), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = title
        window.minSize = NSSize(width: 680, height: 500)
        window.contentView = view
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        windows.append(window)
        webViews[ObjectIdentifier(window)] = view
        if main { mainWindow = window }
        window.makeKeyAndOrderFront(nil)
        return view
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard sender === mainWindow, !allowMainClose, let view = webViews[ObjectIdentifier(sender)] else { return true }
        view.evaluateJavaScript("Boolean(document.querySelector('#main form, dialog[open]'))") { [weak self, weak sender] result, _ in
            guard let self = self, let sender = sender else { return }
            if (result as? Bool) == true {
                let alert = NSAlert()
                alert.messageText = "Close LocalBooks?"
                alert.informativeText = "You have an editor open. Unsaved changes will be lost."
                alert.addButton(withTitle: "Keep editing")
                alert.addButton(withTitle: "Close")
                if alert.runModal() != .alertSecondButtonReturn { return }
            }
            self.allowMainClose = true
            sender.performClose(nil)
        }
        return false
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        webViews.removeValue(forKey: ObjectIdentifier(window))
        windows.removeAll { $0 === window }
        if window === mainWindow { NSApp.terminate(nil) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        mainWindow?.deminiaturize(nil)
        mainWindow?.makeKeyAndOrderFront(nil)
        return true
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !allowMainClose, let window = mainWindow, let view = webViews[ObjectIdentifier(window)] else { return .terminateNow }
        view.evaluateJavaScript("Boolean(document.querySelector('#main form, dialog[open]'))") { result, _ in
            if (result as? Bool) == true {
                let alert = NSAlert(); alert.messageText = "Quit LocalBooks?"
                alert.informativeText = "You have an editor open. Unsaved changes will be lost."
                alert.addButton(withTitle: "Keep editing"); alert.addButton(withTitle: "Quit")
                sender.reply(toApplicationShouldTerminate: alert.runModal() == .alertSecondButtonReturn)
            } else { sender.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "localbooks", message.body as? String == "quit" { allowMainClose = true; NSApp.terminate(nil) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" { decisionHandler(.allow); return }
        guard url.scheme == "http", url.host == "127.0.0.1", url.port == localPort else { decisionHandler(.cancel); return }
        if #available(macOS 11.3, *), navigationAction.shouldPerformDownload { decisionHandler(.download) }
        else { decisionHandler(.allow) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if #available(macOS 11.3, *), let response = navigationResponse.response as? HTTPURLResponse,
           response.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().contains("attachment") == true {
            decisionHandler(.download)
        } else { decisionHandler(.allow) }
    }

    @available(macOS 11.3, *) func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    @available(macOS 11.3, *) func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    @available(macOS 11.3, *) func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.begin { result in completionHandler(result == .OK ? panel.url : nil) }
    }
    @available(macOS 11.3, *) func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { showAlert("Download failed", error.localizedDescription) }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = navigationAction.request.url, url.scheme == "http", url.host == "127.0.0.1", url.port == localPort else { return nil }
        return createWindow(title: "LocalBooks Document", configuration: configuration)
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.begin { result in completionHandler(result == .OK ? panel.urls : nil) }
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        showAlert("LocalBooks", message); completionHandler()
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.addButton(withTitle: "OK"); alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func installMenu() {
        let menu = NSMenu(); let appItem = NSMenuItem(); menu.addItem(appItem)
        let appMenu = NSMenu(); appMenu.addItem(withTitle: "Quit LocalBooks", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); appItem.submenu = appMenu
        let fileItem = NSMenuItem(); menu.addItem(fileItem); let fileMenu = NSMenu(title: "File")
        let printItem = NSMenuItem(title: "Print…", action: #selector(printDocument(_:)), keyEquivalent: "p"); printItem.target = self; fileMenu.addItem(printItem); fileItem.submenu = fileMenu
        let editItem = NSMenuItem(); menu.addItem(editItem); let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSResponder.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu; NSApp.mainMenu = menu
    }
    @objc func printDocument(_ sender: Any?) {
        guard let window = NSApp.keyWindow, let view = webViews[ObjectIdentifier(window)] else { return }
        view.printOperation(with: NSPrintInfo.shared).runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
    }
    func showAlert(_ title: String, _ message: String) { let alert = NSAlert(); alert.messageText = title; alert.informativeText = message; alert.runModal() }
    func fail(_ message: String) { showAlert("LocalBooks could not open", message); NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = LocalBooksApp()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
