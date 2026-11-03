import AppKit
import Combine
import PDFKit
import UniformTypeIdentifiers
import PageBridgeCore

@MainActor
final class ReaderWorkspace: ObservableObject {
    @Published private(set) var selectionText = ""
    @Published var currentPage = 1
    @Published var errorMessage: String?
    @Published private(set) var document: DocumentRecord?
    @Published private var items: [ReadingAnnotation] = []
    private var store: AnnotationStore?
    private var source: URL?
    private var binding: AnnotationBinding?
    private var selectionRegions: [AnnotationRegion] = []
    private var reader: WeakReader?
    private var lastDocumentKey = ""
    @MainActor private final class WeakReader {
        weak var view: PDFView?
        weak var pdf: PDFDocument?
        var url: URL
        init(_ view: PDFView, url: URL) { self.view = view; pdf = view.document; self.url = url }
    }

    init() {
        do { store = try AnnotationStore() } catch { errorMessage = error.localizedDescription }
    }
    var currentAnnotations: [ReadingAnnotation] {
        items.sorted { $0.page == $1.page ? $0.createdAt < $1.createdAt : $0.page < $1.page }
    }
    var bookmarkPageNumbers: [Int] { currentAnnotations.filter { $0.kind == .bookmark }.map(\.page).sorted() }
    var canAnnotateSelection: Bool { !selectionText.isEmpty && !selectionRegions.isEmpty && binding != nil }
    var hasDocument: Bool { binding != nil }

    func open(document record: DocumentRecord) {
        let key = [record.id.uuidString, record.inputPath].joined(separator: "\n")
        // Renaming changes display/export metadata without changing the PDF revision.
        // Refresh that metadata even when the loaded binding can be reused.
        document = record
        guard key != lastDocumentKey else { return }
        lastDocumentKey = key
        selectionText = ""; selectionRegions = []
        source = record.originalURL
        binding = nil; items = []
        currentPage = max(1, min(record.pageCount, currentPage))
        guard let store else { return }
        do {
            let hash = try DocumentRepository.sha256(record.originalURL)
            if hash != record.inputSHA256 { throw BridgeError.message("原文校验值发生变化，已停止载入批注。") }
            let binding = AnnotationBinding(documentID: record.id, sourceSHA256: hash)
            let loaded = try store.load(for: binding)
            self.binding = binding; items = loaded
        } catch { errorMessage = error.localizedDescription }
        // The reader document may still be from the previous selection. Registration
        // will apply overlays only after PDFReaderView opens the matching URL.
    }

    func register(_ view: PDFView, documentID: UUID, url: URL) {
        guard document?.id == documentID, source == url else { return }
        if let previous = reader, previous.view === view, previous.pdf === view.document, previous.url == url { return }
        reader = WeakReader(view, url: url)
        render()
    }
    func unregister(_ view: PDFView) {
        if reader?.view === view { reader = nil }
    }
    func activate(_ view: PDFView, documentID: UUID) {
        guard document?.id == documentID, reader?.view === view else { return }
        if let pdf = view.document, let page = view.currentPage { currentPage = max(1, pdf.index(for: page) + 1) }
        receiveSelection(from: view, documentID: documentID)
    }
    func receiveSelection(from view: PDFView, documentID: UUID) {
        guard document?.id == documentID, reader?.view === view else { return }
        guard let selection = view.currentSelection, let pdf = view.document, let text = selection.string, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            selectionText = ""; selectionRegions = []
            return
        }
        selectionText = text
        selectionRegions = PDFAnnotationRenderer.regions(for: selection, in: pdf)
        if let region = selectionRegions.first { currentPage = region.page }
    }
    func reportPage(_ page: Int) {
        if currentPage != page { currentPage = page }
    }
    func clearSelection() {
        selectionText = ""; selectionRegions = []
        reader?.view?.clearSelection()
    }
    func highlightSelection() { addMarkup(.highlight) }
    func underlineSelection() { addMarkup(.underline) }
    private func addMarkup(_ kind: ReadingAnnotationKind) {
        guard canAnnotateSelection else { return }
        let annotation = ReadingAnnotation(kind: kind, page: selectionRegions[0].page, regions: selectionRegions, selectedText: selectionText)
        if save(items + [annotation]) { reader?.view?.clearSelection() }
    }
    func addNote() {
        guard hasDocument else { return }
        // Snapshot the selection before a native editor takes keyboard focus.
        let regions = canAnnotateSelection ? selectionRegions : []
        let revision = binding
        let quote = canAnnotateSelection ? selectionText : ""
        let page = regions.first?.page ?? currentPage
        guard let text = editText(title: "第 \(page) 页 · 笔记", text: "", quote: quote),
              binding == revision,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let item = ReadingAnnotation(kind: .note, page: page, regions: regions, selectedText: quote, note: text)
        _ = save(items + [item])
    }
    /// Saves an assistant answer or supplied text without opening another dialog.
    func addNote(text: String) {
        guard hasDocument, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let regions = canAnnotateSelection ? selectionRegions : []
        let item = ReadingAnnotation(kind: .note, page: regions.first?.page ?? currentPage, regions: regions, selectedText: canAnnotateSelection ? selectionText : "", note: text)
        _ = save(items + [item])
    }
    func toggleBookmark() {
        guard hasDocument else { return }
        var list = items
        if list.contains(where: { $0.kind == .bookmark && $0.page == currentPage }) { list.removeAll { $0.kind == .bookmark && $0.page == currentPage } }
        else { list.append(ReadingAnnotation(kind: .bookmark, page: currentPage)) }
        _ = save(list)
    }
    func delete(_ item: ReadingAnnotation) {
        _ = save(items.filter { $0.id != item.id })
    }
    func edit(_ item: ReadingAnnotation) {
        let revision = binding
        guard let text = editText(title: "第 \(item.page) 页 · \(item.kind.title)", text: item.note, quote: item.selectedText),
              binding == revision else { return }
        var list = items
        guard let index = list.firstIndex(where: { $0.id == item.id }) else { return }
        list[index].note = text; list[index].modifiedAt = Date()
        _ = save(list)
    }
    func edit(_ annotation: PDFAnnotation, in view: PDFView, documentID: UUID) {
        guard document?.id == documentID, reader?.view === view,
              let pdf = view.document, let id = PDFAnnotationRenderer.overlayID(for: annotation, in: pdf),
              let item = items.first(where: { $0.id == id }) else { return }
        currentPage = item.page
        edit(item)
    }
    func exportAnnotatedPDF() { export(isPDF: true) }
    func exportNotes() { export(isPDF: false) }

    @discardableResult private func save(_ list: [ReadingAnnotation]) -> Bool {
        guard let store, let binding, let source else { return false }
        do {
            try store.save(list, for: binding, sourceURL: source)
            items = list; render(); return true
        } catch { errorMessage = error.localizedDescription; return false }
    }
    private func render() {
        guard let reader, reader.url == source, let pdf = reader.view?.document,
              reader.pdf === pdf, binding != nil else { return }
        do {
            try PDFAnnotationRenderer.apply(items, to: pdf, forDisplay: true)
            for index in 0..<pdf.pageCount {
                if let page = pdf.page(at: index) { reader.view?.annotationsChanged(on: page) }
            }
            reader.view?.needsDisplay = true
        }
        catch { errorMessage = error.localizedDescription }
    }
    private func export(isPDF: Bool) {
        guard let store, let record = document, let binding, let source else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = isPDF ? [.pdf] : [UTType(filenameExtension: "md") ?? .plainText]
        panel.nameFieldStringValue = (record.name as NSString).deletingPathExtension + "-" + (isPDF ? "批注.pdf" : "阅读笔记.md")
        guard panel.runModal() == .OK, let target = panel.url else { return }
        do {
            if isPDF { try store.exportAnnotatedPDF(source: source, binding: binding, destination: target, protectedURLs: [record.originalURL]) }
            else { try store.exportNotes(title: record.name, source: source, binding: binding, destination: target, protectedURLs: [record.originalURL]) }
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
