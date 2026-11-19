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
    @Published var cacheSettingsPresented = false
    @Published private(set) var isClearingCache = false
    @Published private var parseTiming: (documentID: UUID, seconds: Double)?
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
    @Published private(set) var preparingTranslation = false
    @Published var isExporting = false
    @Published private(set) var cancelRequested = false
    @Published var activeJobDocumentID: UUID?
    @Published var engineReady = false
    @Published var engineDescription = "正在检查…"
    @Published var services: [ServiceConfiguration] = []
    @Published var activeServiceID: UUID?
    private var repository: DocumentRepository?
    private var runner: EngineRunner?
    private var sessionAPIKeys: [UUID: String] = [:]
    private var keyUnlocks: [UUID: Task<String, Error>] = [:]
    private lazy var macHasTouchID = DeepSeekCredentialStore.hasTouchID
    private var activeAttempt: UUID?
    private var activeBatchNumber: Int?
    private var healthCheckRunning = false
    private var searchRevision = 0

    var selectedDocument: DocumentRecord? { documents.first { $0.id == selectedID } }
    var activeService: ServiceConfiguration? { services.first { $0.id == activeServiceID } }
    var libraryDataRoot: URL? { repository?.root }
    /// The worker runs one job at a time: a translation or a reading export.
    var isWorkerBusy: Bool { preparingTranslation || isTranslating || isExporting }
    var canCancelJob: Bool { (isTranslating || runner != nil) && !cancelRequested }

    func parseTimeLabel(for documentID: UUID?) -> String? {
        guard let parseTiming, parseTiming.documentID == documentID else { return nil }
        let time = parseTiming.seconds.formatted(.number.precision(.fractionLength(1)))
        return "MinerU 解析耗时 \(time) 秒"
    }

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

    static let translationBatchLimit = 50
    enum TranslationScope { case full, append, retryBlocks([String]), retryTerms([String]), rebuildLayout }

    func rebuildReadingLayout() {
        guard let reading = structuredReading, selectedDocument?.readingPath != nil else { return }
        startTranslation(sourceLanguage: reading.sourceLanguage, targetLanguage: reading.targetLanguage,
                         pages: nil, scope: .rebuildLayout)
    }

    func startTranslation(sourceLanguage: String, targetLanguage: String, pages: String?, translateTables: Bool? = nil,
                          parseWorkers: Int? = nil, glossaryIDs: [String] = [], scope: TranslationScope = .full,
                          automaticallyContinue: Bool = false) {
        guard !isWorkerBusy, !isClearingCache, let document = selectedDocument, !library.isTrashed(id: document.id) else { return }
        let service = activeService
        preparingTranslation = true
        Task {
            defer { preparingTranslation = false }
            await prepareTranslation(documentID: document.id, service: service, sourceLanguage: sourceLanguage,
                                     targetLanguage: targetLanguage, pages: pages, translateTables: translateTables,
                                     parseWorkers: parseWorkers, glossaryIDs: glossaryIDs, scope: scope,
                                     automaticallyContinue: automaticallyContinue)
        }
    }

    private func prepareTranslation(documentID: UUID, service: ServiceConfiguration?, sourceLanguage: String, targetLanguage: String,
                                    pages: String?, translateTables: Bool?, parseWorkers: Int?, glossaryIDs: [String], scope: TranslationScope,
                                    automaticallyContinue: Bool) async {
        guard let repository else { return }
        do {
            guard engineReady else { throw BridgeError.message("翻译引擎未就绪。\(engineDescription)") }
            let engineService: EngineService?
            if case .rebuildLayout = scope {
                engineService = nil
            } else {
                guard let service else { throw BridgeError.message("请先配置翻译服务。") }
                try service.validate()
                if usesTouchID(for: service), sessionAPIKeys[service.id] == nil {
                    statusMessage = "正在等待 Touch ID 授权…"
                }
                let apiKey = try await key(for: service)
                if service.kind == .openAI && apiKey.isEmpty { throw BridgeError.message("请在翻译服务设置中填写 API Key。") }
                engineService = EngineService(configuration: service, apiKey: apiKey)
            }
            guard var document = documents.first(where: { $0.id == documentID }), !library.isTrashed(id: document.id) else {
                throw BridgeError.message("文档已移出文档库，请重新选择。")
            }
            guard let pdf = PDFDocument(url: document.originalURL), pdf.pageCount == document.pageCount else {
                throw BridgeError.message("输入副本不可用，请重新导入。")
            }
            let location = try EngineLocation.find()
            isTranslating = true; activeJobDocumentID = documentID; cancelRequested = false
            preparingTranslation = false; translationPresented = false
            defer {
                isTranslating = false; activeJobDocumentID = nil; activeBatchNumber = nil
                runner = nil; activeAttempt = nil; progress = nil
            }
            var nextPages = pages
            var batchScope = scope
            var batch = 1
            var savedPageCount = 0
            while !cancelRequested {
                activeBatchNumber = automaticallyContinue ? batch : nil
                guard let (saved, result) = try await translateBatch(document: document, repository: repository,
                    location: location, service: engineService, sourceLanguage: sourceLanguage, targetLanguage: targetLanguage,
                    pages: nextPages, translateTables: batch == 1 ? translateTables : nil, parseWorkers: parseWorkers,
                    glossaryIDs: glossaryIDs, scope: batchScope)
                else { return }
                document = saved
                guard automaticallyContinue, !cancelRequested else { return }
                guard let selectedPages = result.selectedPages, selectedPages.count > savedPageCount else {
                    statusMessage = "第 \(batch) 批已保存。页码进度未推进，自动翻译已暂停。"
                    return
                }
                savedPageCount = selectedPages.count
                let translated = Set(selectedPages)
                let next = (1...document.pageCount).lazy.filter { !translated.contains($0) }.prefix(Self.translationBatchLimit)
                guard !next.isEmpty else {
                    let savedMessage = "已保存全部 \(document.pageCount) 页的阅读稿。"
                    statusMessage = document.status == .partial
                        ? savedMessage + "部分内容保留原文，未译段落可单独重试。" : savedMessage
                    return
                }
                nextPages = next.map(String.init).joined(separator: ",")
                batchScope = .append
                batch += 1
            }
        } catch {
            statusMessage = error.localizedDescription
            errorMessage = error.localizedDescription
        }
    }

    private func translateBatch(document: DocumentRecord, repository: DocumentRepository, location: EngineLocation,
                                service: EngineService?, sourceLanguage: String, targetLanguage: String,
                                pages: String?, translateTables: Bool?, parseWorkers: Int?, glossaryIDs: [String], scope: TranslationScope)
                                async throws -> (DocumentRecord, WorkerEvent)? {
        parseTiming = nil
        let attempt = UUID()
        let staging = try repository.attemptDirectory(document: document, attempt: attempt)
        var hasUnsavedReading = false
        defer {
            if !hasUnsavedReading { try? FileManager.default.removeItem(at: staging) }
            runner = nil; activeAttempt = nil; progress = nil
        }
        var retryBlockIds: [String]?, retryTerms: [String]?, building = false, rebuildLayout = false
        switch scope {
        case .full: break
        case .append: building = true
        case .retryBlocks(let ids): retryBlockIds = ids
        case .retryTerms(let terms): retryTerms = terms
        case .rebuildLayout: rebuildLayout = true
        }
        let reusesDraft = retryBlockIds != nil || retryTerms != nil || rebuildLayout
        // Retries reuse the draft's glossaries; deleted ones are simply skipped.
        let terms = rebuildLayout ? "" : glossaries.combinedText(reusesDraft ? (document.glossaryIDs ?? []) : glossaryIDs)
        let request = WorkerRequest(action: rebuildLayout ? "rebuild_reading" : "translate", jobID: document.id, attemptID: attempt,
            inputPath: document.inputPath, outputDirectory: staging.path, sourceLanguage: sourceLanguage,
            targetLanguage: targetLanguage, pages: pages, service: service, readingPath: reusesDraft || building ? document.readingPath : nil,
            retryBlockIds: retryBlockIds, glossary: terms.isEmpty ? nil : terms, retryTerms: retryTerms,
            appendPages: building ? true : nil, translateTables: translateTables, parseWorkers: parseWorkers)
        if !rebuildLayout {
            var record = document; record.status = .running; record.attemptID = attempt; record.lastError = nil
            if !reusesDraft && (activeBatchNumber ?? 1) == 1 { try? changeGlossaries { $0.lastUsedIDs = glossaryIDs } }
            try replaceRecord(record)
        }
        var finished = document
        if !reusesDraft { finished.glossaryIDs = glossaryIDs }
        let process = EngineRunner(location: location)
        runner = process; activeAttempt = attempt; progress = nil
        statusMessage = rebuildLayout ? "正在准备重新排版…" : batchMessage("正在准备对照阅读…")
        do {
            let result = try await process.run(request) { [weak self] event in
                Task { @MainActor in self?.receive(event, documentID: document.id, attempt: attempt) }
            }
            guard !cancelRequested else { throw BridgeError.cancelled }
            setParseTiming(result.parseSeconds, for: document.id)
            if rebuildLayout && result.readingPath == nil {
                statusMessage = result.message ?? "已有译文排版检查完成。"
                return nil
            }
            hasUnsavedReading = true
            runner = nil
            statusMessage = batchMessage("正在验证输出并保存…")
            let root = repository.root, recordToSave = finished
            let completed = try await Task.detached(priority: .userInitiated) {
                try DocumentRepository(root: root).commitReading(result: result, document: recordToSave, attempt: attempt, staging: staging)
            }.value
            do { try replaceRecord(completed) }
            catch {
                if let url = completed.readingURL { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
                throw error
            }
            hasUnsavedReading = false
            if selectedID == document.id {
                if !rebuildLayout && (activeBatchNumber == nil || activeBatchNumber == 1) { readingMode = .dual }
                if !reusesDraft && !building { syncScrolling = true }
                reloadStructuredReading(navigateToFirst: !reusesDraft && !building)
            }
            if rebuildLayout { statusMessage = result.message ?? "已重新排版并保存。" }
            else if cancelRequested { statusMessage = "已停止，已保存本批译稿。" }
            else if retryTerms != nil { statusMessage = result.message ?? "已按术语重译。" }
            else if completed.status == .partial { statusMessage = "原页译文已保存，未完成的段落保留原文，可单独重试。" }
            else { statusMessage = batchMessage("原页译文已保存。点击文字或图表可查看完整内容。") }
            return (completed, result)
        } catch {
            var failed = documents.first { $0.id == document.id } ?? document
            let failureMessage = error.localizedDescription
            let cancelled = cancelRequested && !hasUnsavedReading
            failed.status = cancelled ? .cancelled : .failed
            let message = hasUnsavedReading
                ? "译稿已生成，保存到文档库失败。\n\(failureMessage)\n译稿已保留在以下任务目录。\n\(staging.path)"
                : failureMessage
            failed.lastError = cancelled ? nil : message
            if !rebuildLayout {
                do { try replaceRecord(failed) } catch { errorMessage = "任务记录保存失败：\(error.localizedDescription)" }
            }
            let failure = rebuildLayout ? "重新排版失败：\(failureMessage)" : batchMessage("翻译失败：\(failureMessage)")
            statusMessage = cancelled
                ? (failed.hasTranslation ? "已取消，已保存的译稿可继续使用。" : "已取消，未生成译文。")
                : hasUnsavedReading ? "保存到文档库失败，已保留本次译稿。" : failure
            if !cancelled { errorMessage = message }
            return nil
        }
    }

    private func batchMessage(_ message: String) -> String {
        guard let activeBatchNumber else { return message }
        return "第 \(activeBatchNumber) 批 · \(message)"
    }

    private func receive(_ event: WorkerEvent, documentID: UUID, attempt: UUID) {
        guard runner != nil, activeJobDocumentID == documentID, activeAttempt == attempt, !cancelRequested else { return }
        setParseTiming(event.parseSeconds, for: documentID)
        if event.type == "stage" || event.type == "progress" || event.type == "ready" {
            if let value = event.progress { progress = value }
            else if event.type == "stage" { progress = nil }
            if let message = event.message, !message.isEmpty { statusMessage = batchMessage(message) }
            else if let stage = event.stage { statusMessage = batchMessage(stage) }
        }
    }

    private func setParseTiming(_ seconds: Double?, for documentID: UUID) {
        guard let seconds, seconds.isFinite, seconds >= 0 else { return }
        parseTiming = (documentID, seconds)
    }

    func cancelTranslation() {
        guard isTranslating, !cancelRequested else { return }
        cancelRequested = true; statusMessage = runner == nil ? "本批保存后停止。" : "正在取消，等待翻译进程退出…"; progress = nil
        if let index = documents.firstIndex(where: { $0.id == activeJobDocumentID && $0.status == .running }) {
            var record = documents[index]; record.status = .cancelling
            do { try replaceRecord(record) } catch { errorMessage = error.localizedDescription }
        }
        runner?.cancel()
    }

    func canExport(_ kind: ExportKind) -> Bool {
        guard let record = selectedDocument else { return false }
        guard kind != .original else { return FileManager.default.fileExists(atPath: record.inputPath) }
        return !isWorkerBusy && !isClearingCache && record.readingURL.map { FileManager.default.fileExists(atPath: $0.path) } == true
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
        guard !isWorkerBusy, !isClearingCache, let reading = record.readingURL else { return }
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
                statusMessage = result.message ?? "\(kind.title) 已导出至 \(destination.path)"
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

    func clearCache(_ category: CacheCategory) async throws -> CacheCleanup {
        guard !isWorkerBusy, !isClearingCache, let root = libraryDataRoot else {
            throw BridgeError.message("请在当前任务结束后清理缓存。")
        }
        isClearingCache = true
        defer { isClearingCache = false }
        return try await Task.detached(priority: .utility) {
            try CacheStorage(root: root).clear(category)
        }.value
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
            case .success(var reading):
                structuredReading = reading
                if navigateToFirst, let first = reading.selectedPages.first {
                    page = first
                    // Page reports from scrolling do not move PDFKit. Completion
                    // must also issue an explicit navigation request for the source.
                    readingNavigationRequest = PDFPageNavigation(page: first)
                }
                // Older immutable drafts already contain the cleared background needed to measure their lines.
                if reading.pages.contains(where: \.needsLayoutMeasurement) {
                    let request = WorkerRequest(action: "reading_layout", inputPath: record.inputPath, readingPath: url.path)
                    guard let result = try? await EngineRunner(location: try EngineLocation.find()).run(request),
                          let edges = result.layoutRightEdges, selectedID == record.id,
                          selectedDocument?.readingPath == record.readingPath else { return }
                    for index in reading.pages.indices {
                        let key = String(reading.pages[index].pageNumber)
                        if let measured = edges[key] {
                            reading.pages[index].layoutRightEdges = measured
                            reading.pages[index].cellLayouts = result.cellLayouts?[key]
                        }
                    }
                    structuredReading = reading
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
        let apiKey = try await key(for: service)
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
    func usesTouchID(for service: ServiceConfiguration) -> Bool {
        guard service.kind == .openAI, service.provider == .deepSeek else { return false }
        return hasProtectedKey(for: service) || macHasTouchID
    }

    func hasProtectedKey(for service: ServiceConfiguration) -> Bool {
        guard let repository else { return false }
        return DeepSeekCredentialStore(root: repository.root).contains(service.id)
    }

    func legacyKey(for id: UUID) throws -> String {
        if let key = sessionAPIKeys[id] { return key }
        let key = try KeychainStore.read(serviceID: id)
        sessionAPIKeys[id] = key
        return key ?? ""
    }

    func key(for service: ServiceConfiguration) async throws -> String {
        guard let saved = services.first(where: { $0.id == service.id }), saved.hasSameCredentialDestination(as: service) else {
            throw BridgeError.message("服务地址已改变，请保存新的 API Key 后重试。")
        }
        guard service.kind == .openAI else { return "" }
        let id = service.id
        guard usesTouchID(for: saved) else { return try legacyKey(for: id) }
        if let key = sessionAPIKeys[id] { return key }
        if let unlock = keyUnlocks[id] { return try await unlock.value }
        guard let repository else { throw BridgeError.message("文档库不可用，无法读取 API Key。") }
        let store = DeepSeekCredentialStore(root: repository.root)
        let unlock = Task { [self] in
            let key = try await store.unlock(for: saved)
            sessionAPIKeys[id] = key
            return key
        }
        keyUnlocks[id] = unlock
        defer { keyUnlocks[id] = nil }
        return try await unlock.value
    }

    func saveService(_ service: ServiceConfiguration, apiKey: String?) throws {
        guard let repository else { throw BridgeError.message("本地存储不可用，无法保存服务。请先修复启动时报告的数据目录错误。") }
        let value = service.normalized(); try value.validate()
        let id = value.id
        guard keyUnlocks[id] == nil else { throw BridgeError.message("请完成正在进行的 Touch ID 授权后再保存配置。") }
        let store = DeepSeekCredentialStore(root: repository.root)
        let previousSealed = try store.encryptedKey(for: id)
        let protected = usesTouchID(for: value)
        if apiKey == nil {
            guard protected, previousSealed != nil,
                  services.first(where: { $0.id == id })?.hasSameCredentialDestination(as: value) == true else {
                throw DeepSeekCredentialError.setupRequired
            }
        }
        let changesSealed = protected ? apiKey != nil : previousSealed != nil
        let sealed = try protected ? apiKey.flatMap { $0.isEmpty ? nil : try store.seal($0, for: value) } : nil
        let previousLegacy: String?
        if protected || apiKey == nil { previousLegacy = nil }
        else if previousSealed == nil { previousLegacy = try legacyKey(for: id) }
        else { previousLegacy = try KeychainStore.read(serviceID: id) ?? "" }
        let changesLegacy = !protected && apiKey != nil && apiKey != previousLegacy
        var updated = services
        if let index = updated.firstIndex(where: { $0.id == id }) { updated[index] = value }
        else { updated.append(value) }
        if changesLegacy, let apiKey { try KeychainStore.save(key: apiKey, serviceID: id) }
        do {
            if changesSealed { try store.replaceEncryptedKey(sealed, for: id) }
            try repository.savePreferences(AppPreferences(services: updated, activeServiceID: id))
        } catch {
            if changesSealed { try? store.replaceEncryptedKey(previousSealed, for: id) }
            if changesLegacy, let previousLegacy { try? KeychainStore.save(key: previousLegacy, serviceID: id) }
            if changesSealed || changesLegacy { sessionAPIKeys[id] = nil }
            throw error
        }
        if let apiKey { sessionAPIKeys[id] = protected || apiKey.isEmpty ? nil : apiKey }
        if changesSealed && protected { try? KeychainStore.delete(serviceID: id, allowAuthentication: false) }
        services = updated; activeServiceID = id
    }
    func deleteService(_ id: UUID) throws {
        guard let repository else { throw BridgeError.message("本地存储不可用，无法删除服务。") }
        guard services.count > 1 else { throw BridgeError.message("至少保留一个翻译服务配置。") }
        guard keyUnlocks[id] == nil else { throw BridgeError.message("请完成正在进行的 Touch ID 授权后再删除配置。") }
        let updated = services.filter { $0.id != id }
        let active = activeServiceID == id ? updated.first?.id : activeServiceID
        let store = DeepSeekCredentialStore(root: repository.root)
        let protected = services.first(where: { $0.id == id }).map { usesTouchID(for: $0) } == true
        let sealed = try store.encryptedKey(for: id)
        let previousKey = protected ? "" : try legacyKey(for: id)
        if protected { try store.replaceEncryptedKey(nil, for: id) }
        else { try KeychainStore.delete(serviceID: id) }
        sessionAPIKeys.removeValue(forKey: id)
        do { try repository.savePreferences(AppPreferences(services: updated, activeServiceID: active)) }
        catch {
            if protected { try? store.replaceEncryptedKey(sealed, for: id) }
            else if !previousKey.isEmpty { try? KeychainStore.save(key: previousKey, serviceID: id) }
            throw error
        }
        if protected { try? KeychainStore.delete(serviceID: id, allowAuthentication: false) }
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
