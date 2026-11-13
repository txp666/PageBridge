import AppKit
import Combine
import Foundation
import PDFKit
import UniformTypeIdentifiers
import PageBridgeCore

@MainActor
final class AppModel: ObservableObject {
    let library = LibraryModel()
    @Published var documents: [DocumentRecord] = []
    @Published var selectedID: UUID?
    @Published var settingsPresented = false
    @Published var readingMode: ReadingMode = .original
    @Published var structuredReading: StructuredReadingDocument?
    @Published var readingLoadError: String?
    @Published var readingLoading = false
    @Published private(set) var readingNavigationRequest: PDFPageNavigation?
    @Published var page = 1 {
        didSet {
            if let document = selectedDocument, page > 0 && page <= document.pageCount {
                UserDefaults.standard.set(page, forKey: Self.pageKey(document.id))
            }
        }
    }
    @Published var zoom = 1.0
    @Published var syncScrolling = true
    @Published var searchQuery = ""
    @Published var searchHits: [SearchHit] = []
    @Published var searchPresented = false
    @Published var translationPresented = false
    @Published var glossaryManagerPresented = false
    @Published var addTermRequest: AddTermRequest?
    @Published var librarySearchPresented = false
    @Published private(set) var glossaries = GlossaryLibrary()
    @Published var errorMessage: String?
    @Published var statusMessage = "正在检查翻译引擎…"
    @Published var progress: Double?
    @Published var isTranslating = false
    @Published var isExporting = false
    @Published private(set) var cancelRequested = false
    @Published var activeJobDocumentID: UUID?
    @Published var engineReady = false
    @Published var engineDescription = "正在检查…"
    @Published var services: [ServiceConfiguration] = []
    @Published var activeServiceID: UUID?
    private var repository: DocumentRepository?
    private var runner: EngineRunner?
    private var activeAttempt: UUID?
    private var healthCheckRunning = false
    private var searchRevision = 0

    var selectedDocument: DocumentRecord? { documents.first { $0.id == selectedID } }
    var activeService: ServiceConfiguration? { services.first { $0.id == activeServiceID } }
    /// The worker runs one job at a time: a translation or a reading export.
    var isWorkerBusy: Bool { isTranslating || isExporting }

    init() {
        do {
            let repository = try DocumentRepository(); self.repository = repository
            documents = try repository.loadDocuments().map { record in
                var record = record
                if record.status == .running || record.status == .cancelling {
                    record.status = .interrupted; record.lastError = "应用在上次任务结束前退出，可重新翻译。"
                }
                return record
            }
            try repository.saveDocuments(documents)
            if let preferences = try repository.loadPreferences(), !preferences.services.isEmpty {
                services = preferences.services; activeServiceID = preferences.activeServiceID
            } else {
                let local = ServiceConfiguration.preset(.ollama)
                services = [local]; activeServiceID = local.id
            }
            if activeService == nil { activeServiceID = services.first?.id }
            selectedID = documents.first(where: { !library.isTrashed(id: $0.id) })?.id
            if let document = selectedDocument { restoreReadingState(document) }
            reloadGlossaries()
        } catch {
            repository = nil
            errorMessage = "无法打开 PageBridge 数据目录：\(error.localizedDescription)"
        }
        refreshEngine()
        reloadStructuredReading()
    }

    func selectDocument(_ id: UUID) {
        guard let document = documents.first(where: { $0.id == id }), !library.isTrashed(id: id) else { return }
        selectedID = id; zoom = 1
        restoreReadingState(document)
        library.markOpened(id: id)
        reloadStructuredReading()
        searchQuery = ""; searchHits = []; searchRevision += 1
    }

    private static func pageKey(_ id: UUID) -> String { "PageBridge.readingPage.\(id.uuidString)" }
    private func restoreReadingState(_ document: DocumentRecord) {
        page = max(1, min(document.pageCount, UserDefaults.standard.integer(forKey: Self.pageKey(document.id))))
        readingMode = document.hasTranslation ? .dual : .original
    }

    func importPDFs() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf]; panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false; panel.prompt = "添加 PDF"
        if panel.runModal() == .OK { importURLs(panel.urls) }
    }
    func importURLs(_ urls: [URL]) {
        guard repository != nil else { return }
        var failures: [String] = []
        for url in urls {
            let scope = url.startAccessingSecurityScopedResource()
            defer { if scope { url.stopAccessingSecurityScopedResource() } }
            do {
                let record = try add(url)
                statusMessage = "已添加 \(record.name)，原文件保持不变。"
            } catch { failures.append(error.localizedDescription) }
        }
        if !failures.isEmpty { errorMessage = failures.joined(separator: "\n") }
    }

    /// Downloaded files are already validated; reuse identical local originals
    /// so opening a search result does not duplicate its notes or translations.
    func importLiteraturePDF(_ url: URL) throws {
        guard repository != nil else { throw BridgeError.message("文档库不可用，请重新打开应用后重试。") }
        let hash = try DocumentRepository.sha256(url)
        if let existing = documents.first(where: {
            $0.inputSHA256 == hash && !library.isTrashed(id: $0.id)
                && (try? DocumentRepository.sha256($0.originalURL)) == hash
        }) {
            library.filter = .all; library.searchQuery = ""
            selectDocument(existing.id)
            statusMessage = "已打开文档库中的 \(existing.name)。"
            return
        }
        let record = try add(url)
        statusMessage = "已下载并添加 \(record.name)。"
    }

    private func add(_ url: URL) throws -> DocumentRecord {
        guard let repository else { throw BridgeError.message("文档库不可用，请重新打开应用后重试。") }
        let record = try repository.importPDF(url)
        let updated = [record] + documents
        do { try repository.saveDocuments(updated) }
        catch {
            // This directory was created only for the failed import above.
            try? FileManager.default.removeItem(at: record.originalURL.deletingLastPathComponent())
            throw error
        }
        library.organizeImported(ids: [record.id])
        documents = updated; selectDocument(record.id)
        return record
    }

    func refreshEngine() {
        guard !healthCheckRunning else { return }
        healthCheckRunning = true
        Task {
            defer { healthCheckRunning = false }
            do {
                let location = try EngineLocation.find()
                let health = EngineRunner(location: location)
                let event = try await health.run(WorkerRequest(action: "health"))
                engineReady = true
                engineDescription = event.engineVersion ?? "翻译引擎就绪"
                if !isWorkerBusy { statusMessage = "引擎就绪。选择 PDF 开始翻译。" }
            } catch {
                engineReady = false; engineDescription = error.localizedDescription
                if !isWorkerBusy { statusMessage = "引擎未就绪，请检查安装。" }
            }
        }
    }

    enum TranslationScope { case full, append, retryBlocks([String]), retryTerms([String]) }

    func startTranslation(sourceLanguage: String, targetLanguage: String, pages: String?, translateTables: Bool? = nil, glossaryIDs: [String] = [], scope: TranslationScope = .full) {
        guard !isWorkerBusy, let document = selectedDocument, !library.isTrashed(id: document.id), let repository else { return }
        do {
            guard engineReady else { throw BridgeError.message("翻译引擎未就绪。\(engineDescription)") }
            guard let service = activeService else { throw BridgeError.message("请先配置翻译服务。") }
            try service.validate()
            guard let pdf = PDFDocument(url: document.originalURL), pdf.pageCount == document.pageCount else { throw BridgeError.message("输入副本不可用，请重新导入。") }
            let apiKey = try key(for: service.id)
            if service.kind == .openAI && apiKey.isEmpty { throw BridgeError.message("请在翻译服务设置中填写 API Key。") }
            let attempt = UUID()
            let staging = try repository.attemptDirectory(document: document, attempt: attempt)
            let location = try EngineLocation.find()
            let process = EngineRunner(location: location)
            var retryBlockIds: [String]?, retryTerms: [String]?, building = false
            switch scope {
            case .full: break
            case .append: building = true
            case .retryBlocks(let ids): retryBlockIds = ids
            case .retryTerms(let terms): retryTerms = terms
            }
            // Retries reuse the draft's glossaries; deleted ones are simply skipped.
            let chosen = retryBlockIds != nil || retryTerms != nil ? (document.glossaryIDs ?? []) : glossaryIDs
            let terms = glossaries.combinedText(chosen)
            let request = WorkerRequest(action: "translate", jobID: document.id, attemptID: attempt,
                inputPath: document.inputPath, outputDirectory: staging.path, sourceLanguage: sourceLanguage,
                targetLanguage: targetLanguage, pages: pages, service: EngineService(configuration: service, apiKey: apiKey),
                readingPath: retryBlockIds != nil || retryTerms != nil || building ? document.readingPath : nil, retryBlockIds: retryBlockIds,
                glossary: terms.isEmpty ? nil : terms, retryTerms: retryTerms, appendPages: building ? true : nil,
                translateTables: translateTables)
            var record = document; record.status = .running; record.attemptID = attempt; record.lastError = nil
            let retry = retryBlockIds != nil || retryTerms != nil
            if !retry { try? changeGlossaries { $0.lastUsedIDs = glossaryIDs } }
            try replaceRecord(record)
            // The glossaries belong to the new draft once it is committed; a failed run keeps the old pairing.
            var finished = document
            if !retry { finished.glossaryIDs = glossaryIDs }
            runner = process; activeAttempt = attempt; activeJobDocumentID = document.id
            cancelRequested = false; isTranslating = true; progress = nil
            statusMessage = "正在准备对照阅读；首次使用会下载本地识别模型…"
            translationPresented = false
            Task { [self] in
                defer {
                    runner = nil; isTranslating = false; activeJobDocumentID = nil
                    activeAttempt = nil; progress = nil
                }
                do {
                    let result = try await process.run(request) { [weak self] event in
                        Task { @MainActor in self?.receive(event, documentID: document.id, attempt: attempt) }
                    }
                    guard !cancelRequested else { throw BridgeError.cancelled }
                    statusMessage = "正在验证输出并保存…"
                    let completed = try repository.commitReading(result: result, document: finished, attempt: attempt, staging: staging)
                    try replaceRecord(completed)
                    if selectedID == document.id {
                        readingMode = .dual
                        if !retry && !building { syncScrolling = true }
                        reloadStructuredReading(navigateToFirst: !retry && !building)
                    }
                    statusMessage = retryTerms != nil ? (result.message ?? "已按术语重译。") : completed.status == .partial
                        ? "原页译文已保存，未完成的段落保留原文，可单独重试。"
                        : "\(document.name) 原页译文已保存。点击文字或图表可查看完整内容。"
                    // Only disposable scratch output; committed results are in a different directory.
                    try? FileManager.default.removeItem(at: staging)
                } catch {
                    var failed = documents.first { $0.id == document.id } ?? document
                    let failureMessage = error.localizedDescription
                    failed.status = cancelRequested ? .cancelled : .failed
                    failed.lastError = cancelRequested ? nil : failureMessage
                    do { try replaceRecord(failed) } catch { errorMessage = "任务记录保存失败：\(error.localizedDescription)" }
                    statusMessage = cancelRequested
                        ? (failed.hasTranslation ? "已取消；上次成功结果仍可使用。" : "已取消，未生成译文。")
                        : "翻译失败：\(failureMessage)"
                    if !cancelRequested { errorMessage = failureMessage }
                    try? FileManager.default.removeItem(at: staging)
                }
            }
        } catch { errorMessage = error.localizedDescription }
    }

    private func receive(_ event: WorkerEvent, documentID: UUID, attempt: UUID) {
        guard isWorkerBusy, activeJobDocumentID == documentID, activeAttempt == attempt, !cancelRequested else { return }
        if event.type == "stage" || event.type == "progress" || event.type == "ready" {
            if let value = event.progress { progress = value }
            else if event.type == "stage" { progress = nil }
            if let message = event.message, !message.isEmpty { statusMessage = message }
            else if let stage = event.stage { statusMessage = stage }
        }
    }

    func cancelTranslation() {
        guard isTranslating, !cancelRequested else { return }
        cancelRequested = true; statusMessage = "正在取消，等待翻译进程退出…"; progress = nil
        if let index = documents.firstIndex(where: { $0.id == activeJobDocumentID }) {
            var record = documents[index]; record.status = .cancelling
            do { try replaceRecord(record) } catch { errorMessage = error.localizedDescription }
        }
        runner?.cancel()
    }

    func canExport(_ kind: ExportKind) -> Bool {
        guard let record = selectedDocument else { return false }
        guard kind != .original else { return FileManager.default.fileExists(atPath: record.inputPath) }
        return !isWorkerBusy && record.readingURL.map { FileManager.default.fileExists(atPath: $0.path) } == true
    }

    /// The original is copied; translated and bilingual PDFs are built by the worker from the reading draft.
    func exportSelected(_ kind: ExportKind) {
        guard canExport(kind), let record = selectedDocument, let repository else { return }
        let panel = NSSavePanel(); panel.allowedContentTypes = [.pdf]
        panel.nameFieldStringValue = (record.name as NSString).deletingPathExtension + "-" + kind.title.replacingOccurrences(of: " PDF", with: "") + ".pdf"
        panel.title = "导出\(kind.title)"; panel.prompt = "导出"
        guard panel.runModal() == .OK, let destination = panel.url else { return }
        do {
            if kind == .original {
                try DocumentRepository.export(source: record.originalURL, destination: destination, protectedInput: record.originalURL, managedRoot: repository.root)
                statusMessage = "已导出至 \(destination.path)"
            } else {
                try exportReading(kind, record: record, destination: destination, repository: repository)
            }
        } catch { errorMessage = "导出失败：\(error.localizedDescription)" }
    }

    private func exportReading(_ kind: ExportKind, record: DocumentRecord, destination: URL, repository: DocumentRepository) throws {
        guard !isWorkerBusy, let reading = record.readingURL else { return }
        guard engineReady else { throw BridgeError.message("翻译引擎未就绪。\(engineDescription)") }
        try DocumentRepository.validateExportDestination(destination, protectedInput: record.originalURL, managedRoot: repository.root)
        let attempt = UUID()
        let process = EngineRunner(location: try EngineLocation.find())
        let request = WorkerRequest(action: "export_reading", jobID: record.id, attemptID: attempt, inputPath: record.inputPath,
            readingPath: reading.path, outputPath: destination.path, kind: kind.rawValue)
        runner = process; activeAttempt = attempt; activeJobDocumentID = record.id
        cancelRequested = false; isExporting = true; progress = nil
        statusMessage = "正在导出\(kind.title)…"
        Task { [self] in
            defer {
                runner = nil; isExporting = false; activeJobDocumentID = nil
                activeAttempt = nil; progress = nil
            }
            do {
                let result = try await process.run(request) { [weak self] event in
                    Task { @MainActor in self?.receive(event, documentID: record.id, attempt: attempt) }
                }
                guard !cancelRequested else { throw BridgeError.cancelled }
                guard result.artifactType == "reading-export", let count = result.pageCount,
                      PDFDocument(url: destination)?.pageCount == count else {
                    throw BridgeError.message("导出的 PDF 未通过完整性检查。")
                }
                statusMessage = "\(kind.title) 已导出至 \(destination.path)"
            } catch {
                // The worker replaces the target only after a complete export, so nothing partial is left.
                statusMessage = cancelRequested ? "已取消导出。" : "导出失败：\(error.localizedDescription)"
                if !cancelRequested { errorMessage = "导出失败：\(error.localizedDescription)" }
            }
        }
    }

    func cancelExport() {
        guard isExporting, !cancelRequested else { return }
        cancelRequested = true; statusMessage = "正在取消导出…"; progress = nil
        runner?.cancel()
    }

    func openOutputFolder() {
        guard let record = selectedDocument else { return }
        NSWorkspace.shared.activateFileViewerSelecting([record.readingURL ?? record.originalURL])
    }

    func renameDocument(id: UUID, name: String) -> Bool {
        guard id != activeJobDocumentID, var record = documents.first(where: { $0.id == id }) else { return false }
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, clean.count <= 240, !clean.contains("/"), !clean.contains(":"),
              !clean.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else {
            errorMessage = "文档名称应为 1–240 个字符，不能包含斜杠、冒号或换行。"
            return false
        }
        record.name = clean.lowercased().hasSuffix(".pdf") ? clean : clean + ".pdf"
        do { try replaceRecord(record); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    func search() {
        searchRevision += 1; let revision = searchRevision
        let query = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let selectedDocument, !query.isEmpty else { searchHits = []; return }
        if readingMode == .translated, let reading = structuredReading {
            searchHits = reading.pages.compactMap { page in
                Self.hit(page: page.pageNumber, content: page.blocks.map(\.displayText).joined(separator: "\n"), query: query)
            }
            return
        }
        let url = selectedDocument.originalURL
        Task {
            let hits = await Task.detached(priority: .userInitiated) { () -> [SearchHit] in
                guard let pdf = PDFDocument(url: url) else { return [] }
                var hits: [SearchHit] = []
                for index in 0..<pdf.pageCount {
                    guard let hit = Self.hit(page: index + 1, content: pdf.page(at: index)?.string ?? "", query: query) else { continue }
                    hits.append(hit)
                    if hits.count >= 100 { break }
                }
                return hits
            }.value
            guard revision == searchRevision else { return }
            searchHits = hits
        }
    }

    private nonisolated static func hit(page: Int, content: String, query: String) -> SearchHit? {
        guard let range = content.range(of: query, options: [.caseInsensitive, .diacriticInsensitive]) else { return nil }
        let start = content.index(range.lowerBound, offsetBy: -25, limitedBy: content.startIndex) ?? content.startIndex
        let end = content.index(range.upperBound, offsetBy: 65, limitedBy: content.endIndex) ?? content.endIndex
        return SearchHit(page: page, text: String(content[start..<end]).replacingOccurrences(of: "\n", with: " "))
    }

    func reloadStructuredReading(navigateToFirst: Bool = false) {
        structuredReading = nil; readingLoadError = nil
        guard let record = selectedDocument, let url = record.readingURL else { readingLoading = false; return }
        readingLoading = true
        Task {
            let result = await Task.detached(priority: .userInitiated) {
                Result { try StructuredReadingDocument.load(url, for: record) }
            }.value
            guard selectedID == record.id, selectedDocument?.readingPath == record.readingPath else { return }
            readingLoading = false
            switch result {
            case .success(let reading):
                structuredReading = reading
                if navigateToFirst, let first = reading.selectedPages.first {
                    page = first
                    // Page reports from scrolling do not move PDFKit. Completion
                    // must also issue an explicit navigation request for the source.
                    readingNavigationRequest = PDFPageNavigation(page: first)
                }
            case .failure(let error): readingLoadError = error.localizedDescription
            }
        }
    }

    func retryReadingBlock(_ block: StructuredReadingBlock) {
        guard let document = selectedDocument, let reading = structuredReading, document.readingPath != nil, block.canRetry else { return }
        startTranslation(sourceLanguage: reading.sourceLanguage, targetLanguage: reading.targetLanguage,
                         pages: nil, scope: .retryBlocks([block.id]))
    }

    struct AddTermRequest: Identifiable { let id = UUID(); var source: String; var context = "" }

    /// Own glossaries that suit the current draft's languages, for adding a term while reading.
    var writableGlossaries: [Glossary] {
        let source = structuredReading?.sourceLanguage ?? selectedDocument?.sourceLanguage ?? "en"
        let target = structuredReading?.targetLanguage ?? selectedDocument?.targetLanguage ?? "zh"
        return glossaries.user.filter { $0.applies(source: source, target: target) }
    }

    /// Adds a term to an own glossary (or a new one for this document), links it to the
    /// document's draft, and optionally re-translates the paragraphs that contain it.
    func addTerm(source: String, target: String, glossaryID: String?, retranslate: Bool) throws {
        guard let document = selectedDocument else { throw BridgeError.message("请先选择文档。") }
        let term = source.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty, term.count <= 200, target.count <= 200 else { throw BridgeError.message("原文不能为空，原文和译文各最多 200 字符。") }
        let languages = (structuredReading?.sourceLanguage ?? document.sourceLanguage, structuredReading?.targetLanguage ?? document.targetLanguage)
        var chosen = glossaryID
        try changeGlossaries { library in
            if chosen == nil {
                let name = ((document.name as NSString).deletingPathExtension + " 术语")
                chosen = try library.create(name: name, sourceLanguage: languages.0, targetLanguage: languages.1).id
            }
            try library.addEntry(chosen!, source: term, target: target)
        }
        var record = document
        var ids = record.glossaryIDs ?? []
        if let chosen, !ids.contains(chosen) { ids.append(chosen) }
        record.glossaryIDs = ids
        try replaceRecord(record)
        if retranslate, record.readingPath != nil {
            startTranslation(sourceLanguage: languages.0, targetLanguage: languages.1, pages: nil, scope: .retryTerms([term]))
        }
    }

    /// Links an own glossary to the selected document's draft, for its retries and later runs.
    func linkGlossaryToSelectedDocument(_ id: String) {
        guard var record = selectedDocument else { return }
        var ids = record.glossaryIDs ?? []
        guard !ids.contains(id) else { return }
        ids.append(id); record.glossaryIDs = ids
        do { try replaceRecord(record) } catch { errorMessage = error.localizedDescription }
    }

    /// Asks the engine for candidate terms of the selected document; nothing is saved here.
    func suggestTerms(excluding text: String) async throws -> [TermSuggestion] {
        guard let document = selectedDocument else { throw BridgeError.message("请先选择文档。") }
        guard let service = activeService else { throw BridgeError.message("请先配置翻译服务。") }
        try service.validate()
        let apiKey = try key(for: service.id)
        if service.kind == .openAI && apiKey.isEmpty { throw BridgeError.message("请在翻译服务设置中填写 API Key。") }
        let reading = structuredReading
        let request = WorkerRequest(action: "suggest_terms", inputPath: document.inputPath,
                                    sourceLanguage: reading?.sourceLanguage ?? document.sourceLanguage,
                                    targetLanguage: reading?.targetLanguage ?? document.targetLanguage,
                                    service: EngineService(configuration: service, apiKey: apiKey),
                                    readingPath: document.readingPath, glossary: text.isEmpty ? nil : text)
        let result = try await EngineRunner(location: try EngineLocation.find()).run(request) { _ in }
        return result.terms ?? []
    }

    /// Opens a document at a page, from a library search hit.
    func openDocument(_ id: UUID, page target: Int, translation: Bool) {
        selectDocument(id)
        guard let document = selectedDocument, document.id == id else { return }
        page = max(1, min(document.pageCount, target))
        readingMode = translation && document.hasTranslation ? .dual : readingMode
        readingNavigationRequest = PDFPageNavigation(page: page)
    }

    func reloadGlossaries() {
        guard let repository else { return }
        let builtIn = (try? EngineLocation.find())?.worker.deletingLastPathComponent().appendingPathComponent("glossaries")
        do { glossaries = try GlossaryLibrary.load(root: repository.root, builtInDirectory: builtIn) }
        catch { errorMessage = error.localizedDescription }
    }
    /// Saves a changed copy first, so a failed write leaves the shown glossaries unchanged.
    func changeGlossaries(_ change: (inout GlossaryLibrary) throws -> Void) throws {
        guard let repository else { throw BridgeError.message("文档库不可用。") }
        var copy = glossaries
        try change(&copy)
        try copy.save(root: repository.root)
        glossaries = copy
    }
    func key(for id: UUID) throws -> String { try KeychainStore.read(serviceID: id) ?? "" }
    func saveService(_ service: ServiceConfiguration, apiKey: String) throws {
        guard let repository else { throw BridgeError.message("本地存储不可用，无法保存服务。请先修复启动时报告的数据目录错误。") }
        let value = service.normalized(); try value.validate()
        let previous = try key(for: value.id)
        // An empty key deletes the item.
        if apiKey != previous { try KeychainStore.save(key: apiKey, serviceID: value.id) }
        var updated = services
        if let index = updated.firstIndex(where: { $0.id == value.id }) { updated[index] = value }
        else { updated.append(value) }
        do { try repository.savePreferences(AppPreferences(services: updated, activeServiceID: value.id)) }
        catch {
            if apiKey != previous { try? KeychainStore.save(key: previous, serviceID: value.id) }
            throw error
        }
        services = updated; activeServiceID = value.id
    }
    func deleteService(_ id: UUID) throws {
        guard let repository else { throw BridgeError.message("本地存储不可用，无法删除服务。") }
        guard services.count > 1 else { throw BridgeError.message("至少保留一个翻译服务配置。") }
        let updated = services.filter { $0.id != id }
        let active = activeServiceID == id ? updated.first?.id : activeServiceID
        let previousKey = try key(for: id)
        try KeychainStore.delete(serviceID: id)
        do { try repository.savePreferences(AppPreferences(services: updated, activeServiceID: active)) }
        catch {
            if !previousKey.isEmpty { try? KeychainStore.save(key: previousKey, serviceID: id) }
            throw error
        }
        services = updated; activeServiceID = active
    }
    private func replaceRecord(_ record: DocumentRecord) throws {
        guard let repository else { throw BridgeError.message("本地存储不可用，无法保存任务记录。") }
        var updated = documents
        guard let index = updated.firstIndex(where: { $0.id == record.id }) else { throw BridgeError.message("文档记录不存在。") }
        updated[index] = record
        try repository.saveDocuments(updated); documents = updated
    }
}
