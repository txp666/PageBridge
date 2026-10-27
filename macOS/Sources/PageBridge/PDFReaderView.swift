import AppKit
import PDFKit
import SwiftUI
import PageBridgeCore

/// Explicit navigation is separate from the page number reported while scrolling.
struct PDFPageNavigation: Equatable {
    var page = 1
    var sourceBounds: [Double]? = nil
    var id = UUID()
}

typealias PDFScrollAnchor = ReadingScrollAnchor
typealias PDFScrollSynchronizer = ReadingScrollSynchronizer

struct PDFReaderView: NSViewRepresentable {
    let url: URL
    @Binding var page: Int
    var navigation: PDFPageNavigation
    var acceptsNavigation = true
    var zoom: Double
    var synchronizesScrolling: Bool
    let synchronizer: PDFScrollSynchronizer
    var searchQuery: String
    var workspace: ReaderWorkspace? = nil
    var documentID: UUID? = nil
    var variant: PDFDocumentVariant = .original

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

    @MainActor
    final class Coordinator: NSObject, ReadingScrollPeer {
        var parent: PDFReaderView
        var synchronizesScrolling: Bool { parent.synchronizesScrolling }
        private weak var container: PDFReaderContainer?
        private var openedURL: URL?
        private var observedScrollView: NSScrollView?
        private var scrollObserver: NSObjectProtocol?
        private var applyingChange = false
        private var lastOrigin: NSPoint?
        private var lastKnownAnchor: PDFScrollAnchor?
        private var settledScrollGeneration = 0
        private var settlingScroll = false
        private var lastNavigation: UUID?
        private var needsInitialPosition = true
        private var initialPage = 1
        private var initialPositionGeneration = 0
        private var initialPositionWork: DispatchWorkItem?
        private var initialGeometry: InitialGeometry?
        private struct InitialGeometry: Equatable {
            var documentSize: CGSize
            var viewportSize: CGSize
            var scale: CGFloat

            static func == (lhs: Self, rhs: Self) -> Bool {
                abs(lhs.documentSize.width - rhs.documentSize.width) < 0.5 &&
                abs(lhs.documentSize.height - rhs.documentSize.height) < 0.5 &&
                abs(lhs.viewportSize.width - rhs.viewportSize.width) < 0.5 &&
                abs(lhs.viewportSize.height - rhs.viewportSize.height) < 0.5 &&
                abs(lhs.scale - rhs.scale) < 0.0001
            }
        }
        private var wasSynchronizing = false
        private var lastZoom: Double = 0
        private var lastWidth: CGFloat = 0
        private var reportedPage = 1
        private var pendingPage: Int?
        private var pageReportGeneration = 0
        private var highlightedQuery = ""
        private var searchWork: DispatchWorkItem?
        private var selectionObserver: NSObjectProtocol?
        private var liveScrollObserver: NSObjectProtocol?
        private weak var annotationWorkspace: ReaderWorkspace?

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
            selectionObserver = NotificationCenter.default.addObserver(forName: .PDFViewSelectionChanged, object: pdf, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self, let view = self.container?.pdfView, let id = self.parent.documentID else { return }
                    self.parent.workspace?.receiveSelection(from: view, documentID: id, variant: self.parent.variant)
                }
            }
            pdf.onActivate = { [weak self] in
                guard let self, let view = self.container?.pdfView else { return }
                self.cancelInitialPosition()
                self.cancelSettledScroll()
                if let id = self.parent.documentID {
                    self.parent.workspace?.activate(view, documentID: id, variant: self.parent.variant)
                }
            }
            pdf.onScrollFocus = { [weak self] in
                guard let self else { return }
                self.cancelInitialPosition()
                self.cancelSettledScroll()
                guard let workspace = self.parent.workspace, workspace.document?.id == self.parent.documentID else { return }
                if workspace.selectedVariant != self.parent.variant { workspace.selectedVariant = self.parent.variant }
            }
            pdf.onOpenAnnotation = { [weak self] annotation in
                guard let self, let view = self.container?.pdfView, let id = self.parent.documentID else { return }
                self.parent.workspace?.edit(annotation, in: view, documentID: id, variant: self.parent.variant)
            }
        }

        func detach() {
            cancelInitialPosition()
            cancelSettledScroll()
            if let scrollObserver { NotificationCenter.default.removeObserver(scrollObserver) }
            scrollObserver = nil
            searchWork?.cancel()
            if let selectionObserver { NotificationCenter.default.removeObserver(selectionObserver) }
            selectionObserver = nil
            if let liveScrollObserver { NotificationCenter.default.removeObserver(liveScrollObserver) }
            liveScrollObserver = nil
            if let view = container?.pdfView { annotationWorkspace?.unregister(view) }
            container?.pdfView.onActivate = nil
            container?.pdfView.onScrollFocus = nil
            container?.pdfView.onOpenAnnotation = nil
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
                lastKnownAnchor = nil
                cancelSettledScroll()
                lastNavigation = parent.navigation.id
                beginInitialPosition(page: parent.page)
                reportedPage = parent.page
                applyingChange = false
            }
            if annotationWorkspace !== parent.workspace {
                annotationWorkspace?.unregister(pdf)
                annotationWorkspace = parent.workspace
            }
            if let id = parent.documentID {
                parent.workspace?.register(pdf, documentID: id, variant: parent.variant, url: parent.url)
            }
            layoutReader()
            if lastNavigation != parent.navigation.id {
                lastNavigation = parent.navigation.id
                invalidatePageReport()
                if parent.acceptsNavigation {
                    if needsInitialPosition {
                        // A user's explicit jump replaces the saved startup target.
                        beginInitialPosition(page: parent.navigation.page)
                        layoutReader()
                    }
                    applyAnchor(PDFScrollAnchor(page: parent.navigation.page, fraction: 0))
                    if let box = parent.navigation.sourceBounds, box.count == 4,
                       let target = pdf.document?.page(at: parent.navigation.page - 1) {
                        // Coordinates describe the displayed crop box from its top-left.
                        // For rotated pages a page jump remains reliable; avoid guessing a transform.
                        if target.rotation % 360 == 0 {
                            let crop = target.bounds(for: .cropBox)
                            let region = NSRect(x: crop.minX + box[0], y: crop.maxY - box[3],
                                                width: box[2] - box[0], height: box[3] - box[1]).intersection(crop)
                            if !region.isEmpty {
                                applyingChange = true
                                pdf.go(to: region, on: target)
                                if let selection = target.selection(for: region) { pdf.setCurrentSelection(selection, animate: true) }
                                applyingChange = false
                                if let anchor = visibleAnchor() { applyAnchor(anchor) }
                            }
                        }
                    }
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
            // PDFKit can expose a clip view before the native window has attached
            // and before its first document layout has established the scroll range.
            guard !needsInitialPosition || container?.window != nil else { return }
            installScrollObserver()
            applyZoom()
            if needsInitialPosition { scheduleInitialPosition() }
        }

        private func beginInitialPosition(page: Int) {
            initialPositionGeneration += 1
            initialPositionWork?.cancel(); initialPositionWork = nil
            initialGeometry = nil
            initialPage = page
            needsInitialPosition = true
        }

        private func cancelInitialPosition() {
            initialPositionGeneration += 1
            initialPositionWork?.cancel(); initialPositionWork = nil
            initialGeometry = nil
            needsInitialPosition = false
        }

        private func scheduleInitialPosition() {
            guard needsInitialPosition, initialPositionWork == nil,
                  container?.window != nil else { return }
            let generation = initialPositionGeneration, url = openedURL
            let work = DispatchWorkItem { [weak self] in
                guard let self, self.needsInitialPosition,
                      generation == self.initialPositionGeneration, url == self.openedURL else { return }
                self.initialPositionWork = nil
                guard let pdf = self.container?.pdfView, self.container?.window != nil,
                      pdf.bounds.width > 60, pdf.bounds.height > 60 else { return }
                self.applyingChange = true
                pdf.layoutSubtreeIfNeeded()
                pdf.layoutDocumentView()
                self.applyingChange = false
                self.installScrollObserver()
                guard let document = pdf.document, document.pageCount > 0,
                      let documentView = pdf.documentView, let clip = self.observedScrollView?.contentView,
                      documentView.bounds.height > 0, clip.bounds.height > 0 else { return }
                let geometry = InitialGeometry(documentSize: documentView.bounds.size,
                                               viewportSize: clip.bounds.size, scale: pdf.scaleFactor)
                let target = max(1, min(document.pageCount, self.initialPage))
                guard let targetPage = document.page(at: target - 1) else { return }
                let pageRect = documentView.convert(pdf.convert(targetPage.bounds(for: .cropBox), from: targetPage), from: pdf)
                guard pageRect.height > 0, pageRect.minY >= documentView.bounds.minY - 1,
                      pageRect.maxY <= documentView.bounds.maxY + 1 else { return }
                self.applyAnchorImmediately(PDFScrollAnchor(page: target, fraction: 0))
                if self.initialGeometry == geometry {
                    // Keep PDFKit's startup bounds notifications from overwriting
                    // the saved page until two main-loop layouts agree. This gate
                    // never delays ordinary scrolling and is cancelled by input.
                    self.cancelInitialPosition()
                    self.reportPage(target)
                } else {
                    self.initialGeometry = geometry
                    self.scheduleInitialPosition()
                }
            }
            initialPositionWork = work
            DispatchQueue.main.async(execute: work)
        }

        private func installScrollObserver() {
            guard let scroll = container?.pdfView.documentView?.enclosingScrollView,
                  observedScrollView !== scroll else { return }
            if let scrollObserver { NotificationCenter.default.removeObserver(scrollObserver) }
            if let liveScrollObserver { NotificationCenter.default.removeObserver(liveScrollObserver) }
            observedScrollView = scroll
            lastOrigin = scroll.contentView.bounds.origin
            scroll.contentView.postsBoundsChangedNotifications = true
            liveScrollObserver = NotificationCenter.default.addObserver(forName: NSScrollView.willStartLiveScrollNotification,
                object: scroll, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.cancelInitialPosition(); self?.cancelSettledScroll() }
                }
            scrollObserver = NotificationCenter.default.addObserver(
                forName: NSView.boundsDidChangeNotification, object: scroll.contentView, queue: .main
            ) { [weak self] _ in
                // This observer explicitly runs on the main queue. Stay synchronous
                // so the established per-scroll-event synchronization is unchanged.
                MainActor.assumeIsolated { self?.scrollDidChange() }
            }
        }

        private func applyZoom() {
            guard let pdf = container?.pdfView,
                  let document = pdf.document, let first = document.page(at: 0),
                  pdf.bounds.width > 60 else { return }
            let width = viewportWidth(in: pdf)
            guard abs(width - lastWidth) > 0.5 || parent.zoom != lastZoom else { return }
            let anchor = needsInitialPosition ? nil : (lastKnownAnchor ?? visibleAnchor())
            lastWidth = width
            lastZoom = parent.zoom
            let firstSize = first.bounds(for: .cropBox).size
            let firstWidth = abs(first.rotation % 180) == 90 ? firstSize.height : firstSize.width
            let base = (width - 34) / max(firstWidth, 1)
            applyingChange = true
            pdf.scaleFactor = min(6, max(0.1, base * parent.zoom))
            pdf.layoutDocumentView()
            applyingChange = false
            installScrollObserver()
            if let anchor { applyAnchor(anchor) }
            lastOrigin = observedScrollView?.contentView.bounds.origin
        }

        private func viewportWidth(in pdf: PDFView) -> CGFloat {
            guard let clip = observedScrollView?.contentView else { return pdf.bounds.width }
            // PDFKit's clip bounds are in scaled document coordinates. Convert
            // them to display points so both readers fit the actual visible width.
            return pdf.convert(clip.bounds, from: clip).width
        }

        /// Native clip scrolling preserves momentum and avoids PDFDestination relayout.
        func applyAnchor(_ anchor: PDFScrollAnchor) {
            // A newer explicit or peer scroll supersedes the saved startup page.
            cancelInitialPosition()
            applyAnchorImmediately(anchor)
            // PDFKit may enqueue a constrained scroll after this native clip move.
            // Reapply on the next two layouts without treating those callbacks as input.
            settledScrollGeneration += 1
            let generation = settledScrollGeneration
            settlingScroll = true
            settleScroll(anchor, generation: generation, remaining: 2)
        }

        private func settleScroll(_ anchor: PDFScrollAnchor, generation: Int, remaining: Int) {
            DispatchQueue.main.async { [weak self] in
                guard let self, generation == self.settledScrollGeneration else { return }
                if remaining == 0 { self.settlingScroll = false; return }
                self.applyAnchorImmediately(anchor)
                self.settleScroll(anchor, generation: generation, remaining: remaining - 1)
            }
        }

        private func cancelSettledScroll() {
            settledScrollGeneration += 1
            settlingScroll = false
        }

        private func applyAnchorImmediately(_ anchor: PDFScrollAnchor) {
            guard let pdf = container?.pdfView, let document = pdf.document,
                  let documentView = pdf.documentView, let scroll = observedScrollView,
                  let target = document.page(at: max(0, min(anchor.page - 1, document.pageCount - 1))) else { return }
            let pageRect = documentView.convert(pdf.convert(target.bounds(for: .cropBox), from: target), from: pdf)
            guard pageRect.height > 0 else { return }
            let clip = scroll.contentView
            var bounds = clip.bounds
            bounds.origin.x = documentView.bounds.minX + anchor.horizontalOffset(
                contentWidth: documentView.bounds.width, viewportWidth: bounds.width)
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
            lastKnownAnchor = visibleAnchor() ?? anchor
            applyingChange = false
        }

        func visibleAnchor() -> PDFScrollAnchor? {
            guard !needsInitialPosition else { return nil }
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
            let viewport = observedScrollView?.contentView.bounds ?? rect
            return PDFScrollAnchor(page: document.index(for: visiblePage) + 1, fraction: fraction,
                horizontalFraction: PDFScrollAnchor.horizontalFraction(at: viewport.minX - documentView.bounds.minX,
                    contentWidth: documentView.bounds.width, viewportWidth: viewport.width,
                    pageMargins: pdf.pageBreakMargins.left + pdf.pageBreakMargins.right))
        }

        private func scrollDidChange() {
            guard let clip = observedScrollView?.contentView else { return }
            let origin = clip.bounds.origin
            let changed = lastOrigin != origin
            lastOrigin = origin
            if let pdf = container?.pdfView, abs(viewportWidth(in: pdf) - lastWidth) > 0.5 {
                layoutReader()
                return
            }
            if needsInitialPosition {
                if !applyingChange { scheduleInitialPosition() }
                return
            }
            guard changed, !applyingChange, !settlingScroll,
                  !parent.synchronizer.isSynchronizing, let anchor = visibleAnchor() else { return }
            lastKnownAnchor = anchor
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
            guard !needsInitialPosition else { return }
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
                self.parent.workspace?.reportPage(latest, variant: self.parent.variant)
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

final class AnnotationPDFView: PDFView {
    var onActivate: (() -> Void)?
    var onScrollFocus: (() -> Void)?
    var onOpenAnnotation: ((PDFAnnotation) -> Void)?

    private func managedAnnotation(at point: NSPoint, notesOnly: Bool = true) -> PDFAnnotation? {
        guard onOpenAnnotation != nil, let document,
              let page = page(for: point, nearest: false) else { return nil }
        let location = convert(point, to: page)
        // Preserve PDFKit's native link/widget behavior, even where a markup overlaps it.
        if page.annotations.contains(where: { ($0.type == "Link" || $0.type == "Widget") && $0.bounds.contains(location) }) { return nil }
        return page.annotations.reversed().first {
            (!notesOnly || $0.type == "Text") && $0.type != "Popup" && $0.bounds.contains(location) && PDFAnnotationRenderer.overlayID(for: $0, in: document) != nil
        }
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        // PDFKit's document subview otherwise handles the click before PDFView can
        // route it to the sidecar-backed editor, opening an unpersisted native popup.
        let location = convert(point, from: superview)
        // Left-drag/double-click on markup remains ordinary text selection.
        // Right-click offers the same editor without hijacking that selection.
        let notesOnly = NSApp.currentEvent?.type != .rightMouseDown
        if bounds.contains(location), managedAnnotation(at: location, notesOnly: notesOnly) != nil { return self }
        return super.hitTest(point)
    }

    override func mouseDown(with event: NSEvent) {
        onActivate?()
        if let annotation = managedAnnotation(at: convert(event.locationInWindow, from: nil)) {
            onOpenAnnotation?(annotation)
            return
        }
        super.mouseDown(with: event)
    }

    override func menu(for event: NSEvent) -> NSMenu? {
        guard let annotation = managedAnnotation(at: convert(event.locationInWindow, from: nil), notesOnly: false) else { return super.menu(for: event) }
        // PDFKit's Remove Highlight/Add Note actions bypass our persistence and
        // can reopen the broken native popup. Offer only the managed operation.
        let result = NSMenu()
        let item = NSMenuItem(title: "编辑批注笔记…", action: #selector(editManagedAnnotation(_:)), keyEquivalent: "")
        item.target = self; item.representedObject = annotation
        result.insertItem(item, at: 0)
        return result
    }

    @objc private func editManagedAnnotation(_ sender: NSMenuItem) {
        guard let annotation = sender.representedObject as? PDFAnnotation else { return }
        onActivate?()
        onOpenAnnotation?(annotation)
    }
    override func scrollWheel(with event: NSEvent) {
        onScrollFocus?()
        super.scrollWheel(with: event)
    }
}

final class PDFReaderContainer: NSView {
    let pdfView = AnnotationPDFView()
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

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard window != nil else { return }
        needsLayout = true
        DispatchQueue.main.async { [weak self] in self?.onLayout?() }
    }

    override func layout() {
        super.layout()
        pdfView.frame = bounds
        errorLabel.frame = NSRect(x: 20, y: bounds.midY - 20, width: max(0, bounds.width - 40), height: 40)
        onLayout?()
    }
}
