import AppKit
import PDFKit
import SwiftUI

/// Explicit navigation is separate from the page number reported while scrolling.
struct PDFPageNavigation: Equatable {
    var page = 1
    var id = UUID()
}

struct PDFScrollAnchor: Equatable {
    var page: Int
    // May extend into the page gap: clamping here creates a pause at every boundary.
    var fraction: CGFloat
}

/// Native views exchange scroll positions directly, without publishing SwiftUI state.
final class PDFScrollSynchronizer: ObservableObject {
    private final class Peer {
        weak var value: PDFReaderView.Coordinator?
        init(_ value: PDFReaderView.Coordinator) { self.value = value }
    }
    private var peers: [Peer] = []
    private weak var leader: PDFReaderView.Coordinator?
    private(set) var isSynchronizing = false

    func register(_ peer: PDFReaderView.Coordinator) {
        peers.removeAll { $0.value == nil || $0.value === peer }
        peers.append(Peer(peer))
    }

    func unregister(_ peer: PDFReaderView.Coordinator) {
        peers.removeAll { $0.value == nil || $0.value === peer }
        if leader === peer { leader = nil }
    }

    func reset() { leader = nil }

    func scrolled(_ anchor: PDFScrollAnchor, from source: PDFReaderView.Coordinator) {
        guard !isSynchronizing else { return }
        leader = source
        guard source.parent.synchronizesScrolling else { return }
        isSynchronizing = true
        defer { isSynchronizing = false }
        for peer in peers.compactMap(\.value) where peer !== source && peer.parent.synchronizesScrolling {
            peer.applyAnchor(anchor)
        }
    }

    func align() {
        let source = leader ?? peers.compactMap(\.value).first
        guard let source, let anchor = source.visibleAnchor() else { return }
        scrolled(anchor, from: source)
        source.reportPage(source.pageNumber(for: anchor))
    }
}

struct PDFReaderView: NSViewRepresentable {
    let url: URL
    @Binding var page: Int
    var navigation: PDFPageNavigation
    var acceptsNavigation = true
    var zoom: Double
    var synchronizesScrolling: Bool
    let synchronizer: PDFScrollSynchronizer
    var searchQuery: String

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> PDFReaderContainer {
        let container = PDFReaderContainer()
        context.coordinator.attach(container)
        return container
    }

    func updateNSView(_ container: PDFReaderContainer, context: Context) {
        context.coordinator.parent = self
        context.coordinator.update(container)
    }

    static func dismantleNSView(_ nsView: PDFReaderContainer, coordinator: Coordinator) {
        coordinator.detach()
    }

    final class Coordinator: NSObject {
        var parent: PDFReaderView
        private weak var container: PDFReaderContainer?
        private var openedURL: URL?
        private var observedScrollView: NSScrollView?
        private var scrollObserver: NSObjectProtocol?
        private var applyingChange = false
        private var lastOrigin: NSPoint?
        private var lastNavigation: UUID?
        private var needsInitialPosition = true
        private var wasSynchronizing = false
        private var lastZoom: Double = 0
        private var lastWidth: CGFloat = 0
        private var reportedPage = 1
        private var pendingPage: Int?
        private var pageReportGeneration = 0
        private var highlightedQuery = ""
        private var searchWork: DispatchWorkItem?

        init(_ parent: PDFReaderView) { self.parent = parent; reportedPage = parent.page }

        func attach(_ container: PDFReaderContainer) {
            self.container = container
            let pdf = container.pdfView
            pdf.displayMode = .singlePageContinuous
            pdf.displayDirection = .vertical
            pdf.displayBox = .cropBox
            pdf.displaysPageBreaks = true
            pdf.pageBreakMargins = NSEdgeInsets(top: 14, left: 12, bottom: 14, right: 12)
            pdf.backgroundColor = NSColor.windowBackgroundColor
            pdf.autoScales = false
            pdf.minScaleFactor = 0.1
            pdf.maxScaleFactor = 6
            container.onLayout = { [weak self] in self?.layoutReader() }
            parent.synchronizer.register(self)
        }

        func detach() {
            if let scrollObserver { NotificationCenter.default.removeObserver(scrollObserver) }
            scrollObserver = nil
            searchWork?.cancel()
            invalidatePageReport()
            parent.synchronizer.unregister(self)
            container?.onLayout = nil
        }

        func update(_ container: PDFReaderContainer) {
            let pdf = container.pdfView
            if openedURL != parent.url {
                applyingChange = true
                invalidatePageReport()
                searchWork?.cancel()
                searchWork = nil
                openedURL = parent.url
                pdf.document = PDFDocument(url: parent.url)
                container.errorLabel.isHidden = pdf.document != nil
                pdf.highlightedSelections = nil
                highlightedQuery = ""
                lastWidth = 0
                lastZoom = 0
                lastOrigin = nil
                lastNavigation = parent.navigation.id
                needsInitialPosition = true
                reportedPage = parent.page
                applyingChange = false
            }
            layoutReader()
            if lastNavigation != parent.navigation.id {
                lastNavigation = parent.navigation.id
                invalidatePageReport()
                if parent.acceptsNavigation {
                    applyAnchor(PDFScrollAnchor(page: parent.navigation.page, fraction: 0))
                }
            }
            if parent.synchronizesScrolling && !wasSynchronizing {
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.parent.synchronizesScrolling else { return }
                    self.parent.synchronizer.align()
                }
            }
            wasSynchronizing = parent.synchronizesScrolling
            scheduleHighlights()
        }

        private func layoutReader() {
            guard !applyingChange, let pdf = container?.pdfView, pdf.bounds.width > 60 else { return }
            installScrollObserver()
            applyZoom()
            if needsInitialPosition, observedScrollView != nil {
                needsInitialPosition = false
                applyAnchor(PDFScrollAnchor(page: parent.page, fraction: 0))
            }
        }

        private func installScrollObserver() {
            guard let scroll = container?.pdfView.documentView?.enclosingScrollView,
                  observedScrollView !== scroll else { return }
            if let scrollObserver { NotificationCenter.default.removeObserver(scrollObserver) }
            observedScrollView = scroll
            lastOrigin = scroll.contentView.bounds.origin
            scroll.contentView.postsBoundsChangedNotifications = true
            scrollObserver = NotificationCenter.default.addObserver(
                forName: NSView.boundsDidChangeNotification, object: scroll.contentView, queue: .main
            ) { [weak self] _ in self?.scrollDidChange() }
        }

        private func applyZoom() {
            guard let pdf = container?.pdfView,
                  let document = pdf.document, let first = document.page(at: 0),
                  pdf.bounds.width > 60 else { return }
            let width = pdf.bounds.width
            guard abs(width - lastWidth) > 0.5 || parent.zoom != lastZoom else { return }
            let anchor = needsInitialPosition ? nil : visibleAnchor()
            lastWidth = width
            lastZoom = parent.zoom
            let base = (width - 34) / max(first.bounds(for: .cropBox).width, 1)
            applyingChange = true
            pdf.scaleFactor = min(6, max(0.1, base * parent.zoom))
            pdf.layoutDocumentView()
            applyingChange = false
            installScrollObserver()
            if let anchor { applyAnchor(anchor) }
            lastOrigin = observedScrollView?.contentView.bounds.origin
        }

        /// Native clip scrolling preserves momentum and avoids PDFDestination relayout.
        func applyAnchor(_ anchor: PDFScrollAnchor) {
            guard let pdf = container?.pdfView, let document = pdf.document,
                  let documentView = pdf.documentView, let scroll = observedScrollView,
                  let target = document.page(at: max(0, min(anchor.page - 1, document.pageCount - 1))) else { return }
            let pageRect = documentView.convert(pdf.convert(target.bounds(for: .cropBox), from: target), from: pdf)
            guard pageRect.height > 0 else { return }
            let clip = scroll.contentView
            var bounds = clip.bounds
            bounds.origin.y = documentView.isFlipped
                ? pageRect.minY + anchor.fraction * pageRect.height
                : pageRect.maxY - anchor.fraction * pageRect.height - bounds.height
            let constrained = clip.constrainBoundsRect(bounds)
            invalidatePageReport()
            applyingChange = true
            // No animator proxy: every source event applies its latest offset immediately.
            clip.scroll(to: constrained.origin)
            scroll.reflectScrolledClipView(clip)
            lastOrigin = clip.bounds.origin
            reportedPage = pageNumber(for: anchor)
            applyingChange = false
        }

        func visibleAnchor() -> PDFScrollAnchor? {
            guard let pdf = container?.pdfView, let document = pdf.document,
                  let documentView = pdf.documentView else { return nil }
            let rect = documentView.visibleRect
            let top = NSPoint(x: rect.midX, y: documentView.isFlipped ? rect.minY : rect.maxY)
            let viewportPoint = pdf.convert(top, from: documentView)
            guard let visiblePage = pdf.page(for: viewportPoint, nearest: true) else { return nil }
            let pageRect = documentView.convert(pdf.convert(visiblePage.bounds(for: .cropBox), from: visiblePage), from: pdf)
            guard pageRect.height > 0 else { return nil }
            let fraction = documentView.isFlipped
                ? (top.y - pageRect.minY) / pageRect.height
                : (pageRect.maxY - top.y) / pageRect.height
            return PDFScrollAnchor(page: document.index(for: visiblePage) + 1, fraction: fraction)
        }

        private func scrollDidChange() {
            guard let clip = observedScrollView?.contentView else { return }
            let origin = clip.bounds.origin
            let changed = lastOrigin != origin
            lastOrigin = origin
            guard changed, !applyingChange, !needsInitialPosition,
                  !parent.synchronizer.isSynchronizing, let anchor = visibleAnchor() else { return }
            parent.synchronizer.scrolled(anchor, from: self)
            reportPage(pageNumber(for: anchor))
        }

        func pageNumber(for anchor: PDFScrollAnchor) -> Int {
            let count = container?.pdfView.document?.pageCount ?? 1
            // At the bottom of a document the top of the viewport can sit in
            // the preceding page gap, although the visible content is the last page.
            return max(1, min(count, anchor.page + (anchor.fraction >= 1 ? 1 : 0)))
        }

        func reportPage(_ number: Int) {
            guard number != reportedPage || parent.page != number || pendingPage != nil else { return }
            reportedPage = number
            if pendingPage != nil { pendingPage = number; return }
            pendingPage = number
            let generation = pageReportGeneration
            DispatchQueue.main.async { [weak self] in
                guard let self, generation == self.pageReportGeneration else { return }
                guard let latest = self.pendingPage else { return }
                self.pendingPage = nil
                if self.parent.page != latest { self.parent.page = latest }
            }
        }

        private func invalidatePageReport() {
            pageReportGeneration += 1
            pendingPage = nil
        }

        private func scheduleHighlights() {
            let query = parent.searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
            guard query != highlightedQuery else { return }
            highlightedQuery = query
            searchWork?.cancel()
            let work = DispatchWorkItem { [weak self] in
                guard let self, let pdf = self.container?.pdfView else { return }
                guard !query.isEmpty else { pdf.highlightedSelections = nil; return }
                let selections = pdf.document?.findString(query, withOptions: .caseInsensitive) ?? []
                selections.forEach { $0.color = NSColor.systemYellow.withAlphaComponent(0.4) }
                pdf.highlightedSelections = Array(selections.prefix(1000))
            }
            searchWork = work
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: work)
        }
    }
}

final class PDFReaderContainer: NSView {
    let pdfView = PDFView()
    let errorLabel = NSTextField(labelWithString: "无法读取此 PDF。请检查文件是否仍可访问。")
    var onLayout: (() -> Void)?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        pdfView.autoresizingMask = [.width, .height]
        addSubview(pdfView)
        errorLabel.alignment = .center
        errorLabel.textColor = .secondaryLabelColor
        errorLabel.isHidden = true
        addSubview(errorLabel)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func layout() {
        super.layout()
        pdfView.frame = bounds
        errorLabel.frame = NSRect(x: 20, y: bounds.midY - 20, width: max(0, bounds.width - 40), height: 40)
        onLayout?()
    }
}
