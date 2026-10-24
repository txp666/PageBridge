// Compile alongside macOS/Sources/PageBridge/WindowChrome.swift.
// Only hidden, test-owned AppKit windows are used; no events are posted to UI.
import AppKit
import SwiftUI

private enum CheckFailure: Error { case failed(String) }
private func check(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    guard condition() else { throw CheckFailure.failed(message) }
}
private final class SpyWindow: NSWindow {
    var zoomCalls = 0
    var dragCalls = 0
    override func performZoom(_ sender: Any?) { zoomCalls += 1 }
    override func performDrag(with event: NSEvent) { dragCalls += 1 }
}
private func event(in window: NSWindow, point: NSPoint, count: Int = 2, modifiers: NSEvent.ModifierFlags = []) -> NSEvent {
    NSEvent.mouseEvent(with: .leftMouseDown, location: point, modifierFlags: modifiers,
                      timestamp: ProcessInfo.processInfo.systemUptime,
                      windowNumber: window.windowNumber, context: nil,
                      eventNumber: 0, clickCount: count, pressure: 1)!
}
@main
private struct VerifyWindowChrome {
    static func main() {
        do { try verify() }
        catch { fputs("Window chrome verification failed: \(error)\n", stderr); exit(1) }
    }
    static func verify() throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        let beforePreference = UserDefaults.standard.persistentDomain(forName: UserDefaults.globalDomain)?["AppleActionOnDoubleClick"] as? String
        let window = SpyWindow(contentRect: NSRect(x: 100, y: 100, width: 700, height: 240),
                               styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let anchor = NSView(frame: NSRect(x: 0, y: 0, width: 700, height: 240))
        window.contentView = anchor
        let titlebar = WindowChromeEventHandler(anchor: anchor)
        let titlePoint = NSPoint(x: window.frame.width * 0.7, y: window.frame.height - 8)
        try check(titlebar.handle(event(in: window, point: titlePoint)) == nil && window.zoomCalls == 1, "Native titlebar double-click did not route to zoom")
        try check(titlebar.handle(event(in: window, point: titlePoint, count: 1)) != nil, "Native titlebar drag must remain with AppKit")
        try check(titlebar.handle(event(in: window, point: titlePoint, count: 3)) != nil && window.zoomCalls == 1, "Third click must not zoom again")
        try check(titlebar.handle(event(in: window, point: titlePoint, modifiers: .command)) == nil && window.zoomCalls == 2, "Command background titlebar double-click must route to zoom")
        try check(titlebar.handle(event(in: window, point: titlePoint, count: 1, modifiers: .command)) != nil, "Command single-click/drag must remain with AppKit")
        for modifiers: NSEvent.ModifierFlags in [.control, .option, .shift, [.command, .control], [.command, .option], [.command, .shift]] {
            try check(titlebar.handle(event(in: window, point: titlePoint, modifiers: modifiers)) != nil && window.zoomCalls == 2, "Control/Option/Shift titlebar clicks must pass through")
        }
        try check(titlebar.handle(event(in: window, point: NSPoint(x: 350, y: 40))) != nil, "Content event must not be intercepted by native titlebar monitor")
        for type: NSWindow.ButtonType in [.closeButton, .miniaturizeButton, .zoomButton] {
            if let button = window.standardWindowButton(type) {
                let point = button.convert(NSPoint(x: button.bounds.midX, y: button.bounds.midY), to: nil)
                for modifiers: NSEvent.ModifierFlags in [[], .command] {
                    try check(titlebar.handle(event(in: window, point: point, modifiers: modifiers)) != nil, "Standard window button was intercepted")
                }
            }
        }
        let other = NSWindow(contentRect: NSRect(x: 50, y: 50, width: 400, height: 250), styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        other.isReleasedWhenClosed = false
        try check(titlebar.handle(event(in: other, point: titlePoint)) != nil, "Event from another window must pass through")
        let context = ToolbarWindowContext()
        context.window = window
        let down = event(in: window, point: NSPoint(x: 350, y: 210), count: 1)
        context.record(down); context.beginDrag(); context.beginDrag()
        try check(window.dragCalls == 1, "macOS 14 drag fallback must forward only one original mouseDown")
        context.endDrag(); context.beginDrag()
        try check(window.dragCalls == 1, "Ended drag must not reuse a stale mouseDown")
        context.zoom()
        try check(window.zoomCalls == 3, "Toolbar background zoom must use AppKit performZoom")
        let zoomWindow = NSWindow(contentRect: NSRect(x: 100, y: 100, width: 700, height: 400), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        zoomWindow.isReleasedWhenClosed = false
        let original = zoomWindow.frame
        zoomWindow.performZoom(nil)
        RunLoop.current.run(until: Date().addingTimeInterval(0.3))
        let enlarged = zoomWindow.frame
        try check(enlarged.width > original.width || enlarged.height > original.height, "AppKit zoom did not enlarge the window")
        try check(!zoomWindow.styleMask.contains(.fullScreen), "Zoom unexpectedly entered fullscreen")
        zoomWindow.performZoom(nil)
        RunLoop.current.run(until: Date().addingTimeInterval(0.3))
        let restored = zoomWindow.frame
        try check(abs(restored.width - original.width) < 2 && abs(restored.height - original.height) < 2, "Second zoom did not restore the original size")
        let afterPreference = UserDefaults.standard.persistentDomain(forName: UserDefaults.globalDomain)?["AppleActionOnDoubleClick"] as? String
        try check(beforePreference == afterPreference, "Global titlebar preference changed")
        try check(!window.isVisible && !other.isVisible && !zoomWindow.isVisible, "Verification must not show any window")
        print("Window chrome checks passed: ordinary/Command titlebar routing, other-modifier passthrough, standard-control/content/other-window exclusions, drag lifecycle, actual zoom/restore without fullscreen, unchanged global preference.")
        print("SwiftUI background gesture hit testing is validated separately in the real application UI.")
    }
}
