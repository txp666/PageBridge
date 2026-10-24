import AppKit
import SwiftUI

extension View {
    func pageBridgeWindowChrome() -> some View {
        background(WindowChromeAnchor(region: .titlebar, toolbarContext: nil))
    }

    func documentToolbarWindowChrome() -> some View {
        modifier(DocumentToolbarWindowChrome())
    }
}

/// Gestures belong to the background, not the toolbar HStack. SwiftUI therefore
/// keeps buttons, menus and text fields ahead of this surface in hit testing.
private struct DocumentToolbarWindowChrome: ViewModifier {
    @StateObject private var context = ToolbarWindowContext()

    func body(content: Content) -> some View {
        content.background {
            Color.clear
                .contentShape(Rectangle())
                .background(WindowChromeAnchor(region: .documentToolbar, toolbarContext: context))
                .onTapGesture(count: 2) { context.zoom() }
                .modifier(ToolbarBackgroundDrag(context: context))
        }
    }
}

private struct ToolbarBackgroundDrag: ViewModifier {
    let context: ToolbarWindowContext
    @ViewBuilder func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content.simultaneousGesture(WindowDragGesture())
        } else {
            content.simultaneousGesture(DragGesture(minimumDistance: 2)
                .onChanged { _ in context.beginDrag() }
                .onEnded { _ in context.endDrag() })
        }
    }
}

/// macOS 14's fallback receives the original mouseDown event for AppKit's native
/// drag API. The recorder never consumes events; only the background gesture
/// above can initiate a drag, so controls cannot accidentally move the window.
final class ToolbarWindowContext: ObservableObject {
    weak var window: NSWindow?
    private var mouseDown: NSEvent?
    private var dragging = false

    func record(_ event: NSEvent) {
        guard event.window === window else { return }
        mouseDown = event; dragging = false
    }
    func zoom() {
        guard let window, window.attachedSheet == nil, !window.styleMask.contains(.fullScreen),
              window.styleMask.contains(.resizable) else { return }
        window.performZoom(nil)
    }
    func beginDrag() {
        guard !dragging, let window, let mouseDown, mouseDown.window === window,
              window.attachedSheet == nil, !window.styleMask.contains(.fullScreen) else { return }
        dragging = true
        window.performDrag(with: mouseDown)
    }
    func endDrag() { dragging = false; mouseDown = nil }
}

enum WindowChromeRegion { case titlebar, documentToolbar }

private struct WindowChromeAnchor: NSViewRepresentable {
    let region: WindowChromeRegion
    let toolbarContext: ToolbarWindowContext?
    func makeNSView(context: Context) -> WindowChromeAnchorView {
        WindowChromeAnchorView(region: region, toolbarContext: toolbarContext)
    }
    func updateNSView(_ nsView: WindowChromeAnchorView, context: Context) {}
}

/// A transparent geometry/lifetime anchor that never receives a mouse hit.
final class WindowChromeAnchorView: NSView {
    let region: WindowChromeRegion
    private let toolbarContext: ToolbarWindowContext?
    private var handler: WindowChromeEventHandler?
    private var recorder: Any?

    init(region: WindowChromeRegion, toolbarContext: ToolbarWindowContext? = nil) {
        self.region = region; self.toolbarContext = toolbarContext
        super.init(frame: .zero)
        setAccessibilityElement(false)
    }
    required init?(coder: NSCoder) { nil }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        handler = nil
        if let recorder { NSEvent.removeMonitor(recorder); self.recorder = nil }
        toolbarContext?.window = window
        guard window != nil else { return }
        if region == .titlebar {
            handler = WindowChromeEventHandler(anchor: self)
            handler?.startMonitoring()
        } else if let toolbarContext {
            recorder = NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) { [weak toolbarContext] event in
                toolbarContext?.record(event)
                return event
            }
        }
    }
    deinit { if let recorder { NSEvent.removeMonitor(recorder) } }
}

/// Only the true native titlebar is intercepted. Command is allowed so a
/// background-window double-click behaves like a foreground double-click.
/// Single clicks/drags and all controls retain native handling.
final class WindowChromeEventHandler {
    private weak var anchor: NSView?
    private var monitor: Any?
    init(anchor: NSView) { self.anchor = anchor }
    deinit { if let monitor { NSEvent.removeMonitor(monitor) } }
    func startMonitoring() {
        guard monitor == nil else { return }
        monitor = NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) { [weak self] event in
            guard let self else { return event }
            return self.handle(event)
        }
    }
    func handle(_ event: NSEvent) -> NSEvent? {
        guard event.type == .leftMouseDown, event.clickCount == 2,
              let window = anchor?.window, event.window === window,
              window.styleMask.contains(.resizable), !window.styleMask.contains(.fullScreen),
              window.attachedSheet == nil,
              event.modifierFlags.intersection([.control, .option, .shift]).isEmpty else { return event }
        let point = event.locationInWindow
        let titlebar = NSRect(x: 0, y: window.contentLayoutRect.maxY,
                              width: window.frame.width,
                              height: max(0, window.frame.height - window.contentLayoutRect.maxY))
        guard titlebar.contains(point), !Self.isInteractive(point: point, in: window) else { return event }
        // Standard AppKit zoom toggles user/standard frames, without fullscreen.
        window.performZoom(nil)
        return nil
    }

    static func isInteractive(point: NSPoint, in window: NSWindow) -> Bool {
        for type: NSWindow.ButtonType in [.closeButton, .miniaturizeButton, .zoomButton,
                                          .toolbarButton, .documentIconButton, .documentVersionsButton] {
            if let button = window.standardWindowButton(type), !button.isHidden,
               button.convert(button.bounds, to: nil).insetBy(dx: -2, dy: -2).contains(point) { return true }
        }
        if let frameView = window.contentView?.superview {
            var view = frameView.hitTest(frameView.convert(point, from: nil))
            while let current = view {
                if let text = current as? NSTextField {
                    if text.isEditable || text.isSelectable { return true }
                } else if current is NSControl { return true }
                view = current.superview
            }
        }
        var element = window.accessibilityHitTest(window.convertPoint(toScreen: point))
        var visited = Set<ObjectIdentifier>()
        for _ in 0..<32 {
            guard let accessible = element as? NSAccessibilityProtocol else { break }
            let identity = ObjectIdentifier(accessible as AnyObject)
            guard visited.insert(identity).inserted else { break }
            if let role = accessible.accessibilityRole(), interactiveRoles.contains(role) { return true }
            element = accessible.accessibilityParent()
        }
        return false
    }
    private static let interactiveRoles: Set<NSAccessibility.Role> = [
        .button, .radioButton, .checkBox, .slider, .textField, .textArea,
        .popUpButton, .menuButton, .comboBox, .incrementor, .link,
        .tabGroup, .disclosureTriangle, .scrollBar, .splitter,
    ]
}
