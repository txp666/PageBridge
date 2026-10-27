import AppKit
import Combine
import PDFKit
import UniformTypeIdentifiers
import PageBridgeCore

@MainActor
final class ReaderWorkspace: ObservableObject {
    @Published private(set) var selectionText = ""
    @Published var currentPage = 1
    @Published var selectedVariant: PDFDocumentVariant = .original {
        didSet {
            if oldValue != selectedVariant && selectionVariant != selectedVariant {
                selectionText = ""; selectionRegions = []
            }
        }
    }
    @Published private(set) var annotations: [ReadingAnnotation] = []
    @Published var errorMessage: String?
    @Published private(set) var document: DocumentRecord?
    private var store: AnnotationStore?
    private var sources: [PDFDocumentVariant: URL] = [:]
    private var bindings: [PDFDocumentVariant: AnnotationBinding] = [:]
    private var items: [PDFDocumentVariant: [ReadingAnnotation]] = [:]
    private var selectionRegions: [AnnotationRegion] = []
    private var selectionVariant: PDFDocumentVariant = .original
    private var readers: [PDFDocumentVariant: WeakReader] = [:]
    private var lastDocumentKey = ""
    @MainActor private final class WeakReader {
        weak var view: PDFView?
        weak var pdf: PDFDocument?
        var url: URL
        init(_ view: PDFView, url: URL) { self.view = view; pdf = view.document; self.url = url }
    }

    init(root: URL? = nil) {
        do { store = try AnnotationStore(root: root) } catch { errorMessage = error.localizedDescription }
    }
    var currentAnnotations: [ReadingAnnotation] {
        (items[selectedVariant] ?? []).sorted { $0.page == $1.page ? $0.createdAt < $1.createdAt : $0.page < $1.page }
    }
    var bookmarkPageNumbers: [Int] { currentAnnotations.filter { $0.kind == .bookmark }.map(\.page).sorted() }
    var canAnnotateSelection: Bool { !selectionText.isEmpty && !selectionRegions.isEmpty && selectionVariant == selectedVariant && bindings[selectedVariant] != nil }
    var hasDocument: Bool { bindings[selectedVariant] != nil }

    func open(document record: DocumentRecord) {
        let key = [record.id.uuidString, record.inputPath, record.translatedPath ?? "", record.attemptID?.uuidString ?? ""].joined(separator: "\n")
        // Renaming changes display/export metadata without changing the PDF revision.
        // Refresh that metadata even when the loaded bindings can be reused.
        document = record
        guard key != lastDocumentKey else { return }
        lastDocumentKey = key
        selectionText = ""; selectionRegions = []
        sources = [.original: record.originalURL]
        if let translated = record.translatedURL { sources[.translated] = translated }
        bindings = [:]; items = [:]; annotations = []
        if sources[selectedVariant] == nil { selectedVariant = .original }
        currentPage = max(1, min(record.pageCount, currentPage))
        guard let store else { return }
        for (variant, url) in sources {
            do {
                let hash = try DocumentRepository.sha256(url)
                if variant == .original && hash != record.inputSHA256 { throw BridgeError.message("原文校验值发生变化，已停止载入批注。") }
                let binding = AnnotationBinding(documentID: record.id, variant: variant, sourceSHA256: hash)
                let loaded = try store.load(for: binding)
                bindings[variant] = binding; items[variant] = loaded
            } catch { errorMessage = error.localizedDescription }
        }
        refresh()
        // Reader documents may still be from the previous selection. Registration
        // will apply overlays only after PDFReaderView opens the matching URL.
    }

    func register(_ view: PDFView, documentID: UUID, variant: PDFDocumentVariant, url: URL) {
        guard document?.id == documentID, sources[variant] == url else { return }
        if let previous = readers[variant], previous.view === view, previous.pdf === view.document, previous.url == url { return }
        readers[variant] = WeakReader(view, url: url)
        render(variant)
    }
    func unregister(_ view: PDFView) {
        for variant in PDFDocumentVariant.allCases where readers[variant]?.view === view { readers[variant] = nil }
    }
    func activate(_ view: PDFView, documentID: UUID, variant: PDFDocumentVariant) {
        guard document?.id == documentID, readers[variant]?.view === view else { return }
        selectedVariant = variant
        if let pdf = view.document, let page = view.currentPage { currentPage = max(1, pdf.index(for: page) + 1) }
        receiveSelection(from: view, documentID: documentID, variant: variant)
    }
    func receiveSelection(from view: PDFView, documentID: UUID, variant: PDFDocumentVariant) {
        guard document?.id == documentID, readers[variant]?.view === view else { return }
        guard let selection = view.currentSelection, let pdf = view.document, let text = selection.string, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            if selectedVariant == variant { selectionText = ""; selectionRegions = [] }
            return
        }
        selectedVariant = variant; selectionVariant = variant
        selectionText = text
        selectionRegions = PDFAnnotationRenderer.regions(for: selection, in: pdf)
        if let region = selectionRegions.first { currentPage = region.page }
    }
    func reportPage(_ page: Int, variant: PDFDocumentVariant) {
        if selectedVariant == variant && currentPage != page { currentPage = page }
    }
    func clearSelection() {
        selectionText = ""; selectionRegions = []
        for reader in readers.values { reader.view?.clearSelection() }
    }
    func highlightSelection() { addMarkup(.highlight) }
    func underlineSelection() { addMarkup(.underline) }
    private func addMarkup(_ kind: ReadingAnnotationKind) {
        guard canAnnotateSelection else { return }
        let annotation = ReadingAnnotation(kind: kind, page: selectionRegions[0].page, regions: selectionRegions, selectedText: selectionText)
        if save((items[selectedVariant] ?? []) + [annotation], variant: selectedVariant) {
            readers[selectedVariant]?.view?.clearSelection()
        }
    }
    func addNote() {
        guard hasDocument else { return }
        // Snapshot the selection before a native editor takes keyboard focus.
        let variant = selectedVariant, regions = canAnnotateSelection ? selectionRegions : []
        let binding = bindings[variant]
        let quote = canAnnotateSelection ? selectionText : ""
        let page = regions.first?.page ?? currentPage
        guard let text = editText(title: "第 \(page) 页 · \(variant.title) · 笔记", text: "", quote: quote),
              bindings[variant] == binding,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let item = ReadingAnnotation(kind: .note, page: page, regions: regions, selectedText: quote, note: text)
        _ = save((items[variant] ?? []) + [item], variant: variant)
    }
    /// Saves an assistant answer or supplied text without opening another dialog.
    func addNote(text: String) {
        guard hasDocument, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let regions = canAnnotateSelection ? selectionRegions : []
        let item = ReadingAnnotation(kind: .note, page: regions.first?.page ?? currentPage, regions: regions, selectedText: canAnnotateSelection ? selectionText : "", note: text)
        _ = save((items[selectedVariant] ?? []) + [item], variant: selectedVariant)
    }
    func toggleBookmark() {
        guard hasDocument else { return }
        var list = items[selectedVariant] ?? []
        if list.contains(where: { $0.kind == .bookmark && $0.page == currentPage }) { list.removeAll { $0.kind == .bookmark && $0.page == currentPage } }
        else { list.append(ReadingAnnotation(kind: .bookmark, page: currentPage)) }
        _ = save(list, variant: selectedVariant)
    }
    func delete(_ item: ReadingAnnotation) {
        _ = save((items[selectedVariant] ?? []).filter { $0.id != item.id }, variant: selectedVariant)
    }
    func edit(_ item: ReadingAnnotation) {
        let variant = selectedVariant
        let binding = bindings[variant]
        guard let text = editText(title: "第 \(item.page) 页 · \(variant.title) · \(item.kind.title)", text: item.note, quote: item.selectedText),
              bindings[variant] == binding else { return }
        updateNote(id: item.id, text: text, variant: variant)
    }
    func edit(_ annotation: PDFAnnotation, in view: PDFView, documentID: UUID, variant: PDFDocumentVariant) {
        guard document?.id == documentID, readers[variant]?.view === view,
              let pdf = view.document, let id = PDFAnnotationRenderer.overlayID(for: annotation, in: pdf),
              let item = items[variant]?.first(where: { $0.id == id }) else { return }
        selectedVariant = variant; currentPage = item.page
        edit(item)
    }
    func updateNote(id: UUID, text: String, variant: PDFDocumentVariant? = nil) {
        let variant = variant ?? selectedVariant
        var list = items[variant] ?? []
        guard let index = list.firstIndex(where: { $0.id == id }) else { return }
        list[index].note = text; list[index].modifiedAt = Date()
        _ = save(list, variant: variant)
    }
    func navigate(to item: ReadingAnnotation) { currentPage = item.page }
    func exportAnnotatedPDF() { export(isPDF: true) }
    func exportNotes() { export(isPDF: false) }

    private func refresh() { annotations = PDFDocumentVariant.allCases.flatMap { items[$0] ?? [] } }
    @discardableResult private func save(_ list: [ReadingAnnotation], variant: PDFDocumentVariant) -> Bool {
        guard let store, let binding = bindings[variant], let source = sources[variant] else { return false }
        do {
            try store.save(list, for: binding, sourceURL: source)
            items[variant] = list; refresh(); render(variant); return true
        } catch { errorMessage = error.localizedDescription; return false }
    }
    private func render(_ variant: PDFDocumentVariant) {
        guard let reader = readers[variant], reader.url == sources[variant], let pdf = reader.view?.document,
              reader.pdf === pdf, bindings[variant] != nil else { return }
        do {
            try PDFAnnotationRenderer.apply(items[variant] ?? [], to: pdf, forDisplay: true)
            for index in 0..<pdf.pageCount {
                if let page = pdf.page(at: index) { reader.view?.annotationsChanged(on: page) }
            }
            reader.view?.needsDisplay = true
        }
        catch { errorMessage = error.localizedDescription }
    }
    private func export(isPDF: Bool) {
        guard let store, let record = document, let binding = bindings[selectedVariant], let source = sources[selectedVariant] else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = isPDF ? [.pdf] : [UTType(filenameExtension: "md") ?? .plainText]
        panel.nameFieldStringValue = (record.name as NSString).deletingPathExtension + "-\(selectedVariant.title)-" + (isPDF ? "批注.pdf" : "读书笔记.md")
        guard panel.runModal() == .OK, let target = panel.url else { return }
        do {
            let protected = [record.originalURL, record.translatedURL, record.bilingualURL].compactMap { $0 }
            if isPDF { try store.exportAnnotatedPDF(source: source, binding: binding, destination: target, protectedURLs: protected) }
            else { try store.exportNotes(title: record.name, source: source, binding: binding, destination: target, protectedURLs: protected) }
        } catch { errorMessage = error.localizedDescription }
    }
    private func editText(title: String, text: String, quote: String = "") -> String? {
        let alert = NSAlert(); alert.messageText = title
        alert.informativeText = quote.isEmpty ? "记录想法、问题或阅读要点。⌘ Return 保存，Esc 取消。" : "选中文字：\n\(quote.prefix(600))\(quote.count > 600 ? "…" : "")\n\n在下方编辑笔记。⌘ Return 保存，Esc 取消。"
        alert.addButton(withTitle: "保存"); alert.addButton(withTitle: "取消")
        alert.buttons.first?.keyEquivalent = "\r"
        alert.buttons.first?.keyEquivalentModifierMask = .command
        alert.buttons.last?.keyEquivalent = "\u{1b}"
        alert.buttons.last?.keyEquivalentModifierMask = []
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 460, height: 220))
        scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true
        scroll.borderType = .bezelBorder; scroll.drawsBackground = true
        scroll.backgroundColor = .textBackgroundColor
        let editor = NoteEditorTextView(frame: scroll.bounds)
        editor.onSave = { [weak alert] in alert?.buttons.first?.performClick(nil) }
        editor.onCancel = { [weak alert] in alert?.buttons.last?.performClick(nil) }
        editor.isRichText = false; editor.font = .systemFont(ofSize: 14); editor.string = text
        editor.drawsBackground = true; editor.backgroundColor = .textBackgroundColor
        editor.textColor = .textColor; editor.insertionPointColor = .textColor
        editor.textContainerInset = NSSize(width: 10, height: 10)
        editor.setAccessibilityLabel("笔记内容")
        editor.minSize = NSSize(width: 0, height: scroll.contentSize.height)
        editor.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        editor.autoresizingMask = [.width]; editor.isVerticallyResizable = true
        editor.isHorizontallyResizable = false
        editor.textContainer?.containerSize = NSSize(width: scroll.contentSize.width, height: CGFloat.greatestFiniteMagnitude)
        editor.textContainer?.widthTracksTextView = true
        scroll.documentView = editor; alert.accessoryView = scroll
        alert.window.initialFirstResponder = editor
        guard alert.runModal() == .alertFirstButtonReturn else { return nil }
        return editor.string
    }
}

private final class NoteEditorTextView: NSTextView {
    var onSave: (() -> Void)?
    var onCancel: (() -> Void)?
    override func keyDown(with event: NSEvent) {
        // NSTextView otherwise consumes Escape for completion. Leave marked
        // text to the input method, so Chinese composition is not discarded.
        if !hasMarkedText() {
            if event.keyCode == 53 { onCancel?(); return }
            if (event.keyCode == 36 || event.keyCode == 76), event.modifierFlags.contains(.command) { onSave?(); return }
        }
        super.keyDown(with: event)
    }
}
