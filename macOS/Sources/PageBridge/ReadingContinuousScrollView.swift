import AppKit
import PDFKit
import SwiftUI
import PageBridgeCore

/// Native continuous scrolling with only nearby page canvases mounted.
struct ReadingContinuousScrollView: NSViewRepresentable {
    let url: URL
    @Binding var page: Int
    let navigation: PDFPageNavigation
    let acceptsNavigation: Bool
    let zoom: Double
    let synchronizesScrolling: Bool
    let synchronizer: PDFScrollSynchronizer
    let content: (Int, CGFloat) -> AnyView

    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> ReadingContinuousScrollContainer {
        let scroll = ReadingContinuousScrollContainer()
        context.coordinator.attach(scroll)
        return scroll
    }
    func updateNSView(_ view: ReadingContinuousScrollContainer, context: Context) {
        context.coordinator.parent = self
        context.coordinator.update()
    }
    static func dismantleNSView(_ view: ReadingContinuousScrollContainer, coordinator: Coordinator) { coordinator.detach() }

    @MainActor final class Coordinator: NSObject, ReadingScrollPeer {
        var parent: ReadingContinuousScrollView
        var synchronizesScrolling: Bool { parent.synchronizesScrolling }
        private weak var scroll: ReadingContinuousScrollContainer?
        private let documentView = ReadingContinuousDocumentView()
        private var openedURL: URL?
        private var sizes: [CGSize] = []
        private var geometry: ReadingScrollLayout?
        private var hosts: [Int: NSHostingView<AnyView>] = [:]
        private var hostScales: [Int: CGFloat] = [:]
        private var observer: NSObjectProtocol?
        private var applyingChange = false
        private var layingOut = false
        private var handlingUserScroll = false
        private var pendingAnchor: ReadingScrollAnchor?
        private var lastKnownAnchor: ReadingScrollAnchor?
        private var lastViewport: CGSize = .zero
        private var lastOrigin: CGPoint?
        private var lastNavigation: UUID?
        private var wasSynchronizing = false
        private var pendingPage: Int?
        private var pageReportGeneration = 0

        init(_ parent: ReadingContinuousScrollView) { self.parent = parent }

        func attach(_ scroll: ReadingContinuousScrollContainer) {
            self.scroll = scroll
            scroll.hasVerticalScroller = true; scroll.hasHorizontalScroller = true
            scroll.autohidesScrollers = true; scroll.scrollerStyle = .overlay
            scroll.drawsBackground = true; scroll.backgroundColor = .windowBackgroundColor
            scroll.documentView = documentView
            documentView.autoresizesSubviews = false
            scroll.contentView.postsBoundsChangedNotifications = true
            observer = NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification,
                object: scroll.contentView, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.scrollDidChange() }
                }
            scroll.onLayout = { [weak self] in self?.layoutPages() }
            scroll.onUserScroll = { [weak self] in
                guard let self, self.geometry != nil else { return }
                self.pendingAnchor = nil
                self.handlingUserScroll = true
                DispatchQueue.main.async { [weak self] in self?.handlingUserScroll = false }
            }
            parent.synchronizer.register(self)
        }

        func detach() {
            invalidatePageReport()
            if let observer { NotificationCenter.default.removeObserver(observer) }
            observer = nil
            scroll?.onLayout = nil; scroll?.onUserScroll = nil
            parent.synchronizer.unregister(self)
        }

        func update() {
            if openedURL != parent.url {
                invalidatePageReport()
                openedURL = parent.url
                let pdf = PDFDocument(url: parent.url)
                sizes = (0..<(pdf?.pageCount ?? 0)).compactMap { index in
                    guard let page = pdf?.page(at: index) else { return nil }
                    let size = page.bounds(for: .cropBox).size
                    return abs(page.rotation % 180) == 90 ? CGSize(width: size.height, height: size.width) : size
                }
                hosts.values.forEach { $0.removeFromSuperview() }
                hosts.removeAll(); hostScales.removeAll(); geometry = nil
                pendingAnchor = ReadingScrollAnchor(page: parent.page, fraction: 0)
                lastKnownAnchor = nil; lastViewport = .zero
                lastNavigation = parent.navigation.id; lastOrigin = nil
            }
            layoutPages()
            if lastNavigation != parent.navigation.id {
                lastNavigation = parent.navigation.id
                invalidatePageReport()
                if parent.acceptsNavigation { applyAnchor(ReadingScrollAnchor(page: parent.navigation.page, fraction: 0)) }
            }
            if parent.synchronizesScrolling && !wasSynchronizing {
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.parent.synchronizesScrolling else { return }
                    self.parent.synchronizer.align()
                }
            }
            wasSynchronizing = parent.synchronizesScrolling
        }

        private func layoutPages() {
            guard !layingOut, let scroll, scroll.window != nil,
                  scroll.contentView.bounds.width > 60, scroll.contentView.bounds.height > 60, !sizes.isEmpty else { return }
            layingOut = true
            defer { layingOut = false }
            let anchor = pendingAnchor ?? lastKnownAnchor ?? visibleAnchor() ?? ReadingScrollAnchor(page: parent.page, fraction: 0)
            let previous = geometry
            // Adding a document can reveal legacy scrollbars and shrink the clip
            // view. Finish that native tiling before measuring the page geometry.
            for _ in 0..<3 {
                scroll.tile()
                let viewport = scroll.contentView.bounds.size
                let next = ReadingScrollLayout(pageSizes: sizes, viewport: viewport, zoom: parent.zoom)
                geometry = next
                documentView.frame = CGRect(origin: .zero, size: next.contentSize)
                scroll.tile()
                if scroll.contentView.bounds.size == viewport { break }
            }
            lastViewport = scroll.contentView.bounds.size
            if previous != geometry {
                applyAnchor(anchor)
            } else if pendingAnchor != nil {
                applyAnchor(anchor)
            }
            refreshVisiblePages()
        }

        private func refreshVisiblePages() {
            guard let scroll, let geometry else { return }
            let visible = Set(geometry.visiblePages(in: scroll.contentView.bounds))
            for number in hosts.keys.filter({ !visible.contains($0) }) {
                hosts.removeValue(forKey: number)?.removeFromSuperview(); hostScales[number] = nil
            }
            for number in visible.sorted() {
                let frame = geometry.pageFrames[number - 1]
                if let host = hosts[number] {
                    host.frame = frame
                    if hostScales[number] != geometry.scale {
                        host.rootView = parent.content(number, geometry.scale)
                        hostScales[number] = geometry.scale
                    }
                } else {
                    let host = NSHostingView(rootView: parent.content(number, geometry.scale))
                    host.frame = frame
                    host.autoresizingMask = []
                    documentView.addSubview(host)
                    hosts[number] = host; hostScales[number] = geometry.scale
                }
            }
        }

        func applyAnchor(_ anchor: ReadingScrollAnchor) {
            guard let scroll, let geometry else { pendingAnchor = anchor; return }
            invalidatePageReport()
            applyingChange = true
            var rect = scroll.contentView.bounds
            rect.origin.y = geometry.offset(for: anchor)
            rect.origin.x = anchor.horizontalOffset(contentWidth: geometry.contentSize.width, viewportWidth: rect.width)
            let constrained = scroll.contentView.constrainBoundsRect(rect)
            scroll.contentView.scroll(to: constrained.origin)
            scroll.reflectScrolledClipView(scroll.contentView)
            lastOrigin = scroll.contentView.bounds.origin
            lastKnownAnchor = geometry.anchor(at: scroll.contentView.bounds.origin, viewport: scroll.contentView.bounds.size)
            pendingAnchor = nil
            applyingChange = false
            refreshVisiblePages()
        }

        func visibleAnchor() -> ReadingScrollAnchor? {
            guard pendingAnchor == nil, let scroll else { return nil }
            if !layingOut, scroll.contentView.bounds.size != lastViewport { layoutPages() }
            guard let geometry else { return nil }
            return geometry.anchor(at: scroll.contentView.bounds.origin, viewport: scroll.contentView.bounds.size)
        }

        func pageNumber(for anchor: ReadingScrollAnchor) -> Int { geometry?.pageNumber(for: anchor) ?? max(1, anchor.page) }

        private func scrollDidChange() {
            guard let scroll else { return }
            let origin = scroll.contentView.bounds.origin, changed = origin != lastOrigin
            lastOrigin = origin
            guard !layingOut else { return }
            if scroll.contentView.bounds.size != lastViewport {
                // A newly visible scrollbar can resize the clip during the first
                // wheel event. Preserve that input's position, not the old frame.
                if handlingUserScroll, let anchor = geometry?.anchor(at: origin, viewport: lastViewport) { lastKnownAnchor = anchor }
                layoutPages()
                if !handlingUserScroll { return }
            }
            refreshVisiblePages()
            guard changed, !applyingChange, !parent.synchronizer.isSynchronizing,
                  let anchor = visibleAnchor() else { return }
            lastKnownAnchor = anchor
            parent.synchronizer.scrolled(anchor, from: self)
            reportPage(pageNumber(for: anchor))
        }

        func reportPage(_ number: Int) {
            guard parent.page != number || pendingPage != nil else { return }
            if pendingPage != nil { pendingPage = number; return }
            pendingPage = number
            let generation = pageReportGeneration
            DispatchQueue.main.async { [weak self] in
                guard let self, generation == self.pageReportGeneration, let latest = self.pendingPage else { return }
                self.pendingPage = nil
                if self.parent.page != latest { self.parent.page = latest }
            }
        }

        private func invalidatePageReport() { pageReportGeneration += 1; pendingPage = nil }
    }
}

final class ReadingContinuousScrollContainer: NSScrollView {
    var onLayout: (() -> Void)?
    var onUserScroll: (() -> Void)?
    override func layout() { super.layout(); onLayout?() }
    override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); onLayout?() }
    override func scrollWheel(with event: NSEvent) { onUserScroll?(); super.scrollWheel(with: event) }
}
private final class ReadingContinuousDocumentView: NSView {
    override var isFlipped: Bool { true }
}
