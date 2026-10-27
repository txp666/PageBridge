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
    @Published var usesStructuredReading = true
    @Published var structuredReading: StructuredReadingDocument?
    @Published var readingLoadError: String?
    @Published var readingLoading = false
    @Published private(set) var readingNavigationRequest: PDFPageNavigation?
    @Published var page = 1 {
        didSet {
            if let document = selectedDocument, page > 0 && page <= document.pageCount {
                UserDefaults.standard.set(page, forKey: "PageBridge.readingPage.\(document.id.uuidString)")
            }
        }
    }
    @Published var zoom = 1.0
    @Published var syncScrolling = true
    @Published var searchQuery = ""
    @Published var searchHits: [SearchHit] = []
    @Published var searchPresented = false
    @Published var translationPresented = false
    @Published var errorMessage: String?
    @Published var statusMessage = "正在检查翻译引擎…"
    @Published var progress: Double?
    @Published var isTranslating = false
    @Published var activeJobDocumentID: UUID?
    @Published var engineReady = false
    @Published var engineDescription = "正在检查…"
    @Published var services: [ServiceConfiguration] = []
    @Published var activeServiceID: UUID?
    private var repository: DocumentRepository?
    private var runner: EngineRunner?
    private var activeAttempt: UUID?
    private var cancelRequested = false
    private var healthCheckRunning = false
    private var searchRevision = 0

    var selectedDocument: DocumentRecord? { documents.first { $0.id == selectedID } }
    var activeService: ServiceConfiguration? { services.first { $0.id == activeServiceID } }

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
                let local = ServiceConfiguration.defaultOllama
                services = [local]; activeServiceID = local.id
            }
            if activeService == nil { activeServiceID = services.first?.id }
            selectedID = documents.first(where: { !library.isTrashed(id: $0.id) })?.id
            if let document = selectedDocument {
                page = max(1, min(document.pageCount, UserDefaults.standard.integer(forKey: "PageBridge.readingPage.\(document.id.uuidString)")))
                if document.hasTranslation { readingMode = .dual }
                usesStructuredReading = document.readingPath != nil && document.preferredOutputMode != "pdf"
            }
        } catch {
            repository = nil
            errorMessage = "无法打开 PageBridge 数据目录：\(error.localizedDescription)"
        }
        refreshEngine()
        reloadStructuredReading()
    }

    func selectDocument(_ id: UUID) {
        guard let document = documents.first(where: { $0.id == id }), !library.isTrashed(id: id) else { return }
        let savedPage = max(1, min(document.pageCount, UserDefaults.standard.integer(forKey: "PageBridge.readingPage.\(id.uuidString)")))
        selectedID = id; page = savedPage; zoom = 1
        library.markOpened(id: id)
        readingMode = document.hasTranslation ? .dual : .original
        usesStructuredReading = document.readingPath != nil && document.preferredOutputMode != "pdf"
        reloadStructuredReading()
        searchQuery = ""; searchHits = []; searchRevision += 1
    }

    func importPDFs() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf]; panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false; panel.prompt = "添加 PDF"
        if panel.runModal() == .OK { importURLs(panel.urls) }
    }
    func importURLs(_ urls: [URL]) {
        guard let repository else { return }
        var failures: [String] = []
        for url in urls {
            let scope = url.startAccessingSecurityScopedResource()
            defer { if scope { url.stopAccessingSecurityScopedResource() } }
            do {
                let record = try repository.importPDF(url)
                let updated = [record] + documents
                try repository.saveDocuments(updated)
                library.organizeImported(ids: [record.id])
                documents = updated; selectDocument(record.id)
                statusMessage = "已添加 \(record.name)，原文件保持不变。"
            } catch { failures.append(error.localizedDescription) }
        }
        if !failures.isEmpty { errorMessage = failures.joined(separator: "\n") }
    }

    /// Downloaded files are already validated; reuse identical local originals
    /// so opening a search result does not duplicate its notes or translations.
    func importLiteraturePDF(_ url: URL) throws {
        guard let repository else { throw BridgeError.message("文档库不可用，请重新打开应用后重试。") }
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
        statusMessage = "已下载并添加 \(record.name)。"
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
                if !isTranslating { statusMessage = "引擎就绪。选择 PDF 开始翻译。" }
            } catch {
                engineReady = false; engineDescription = error.localizedDescription
                if !isTranslating { statusMessage = "引擎未就绪，请检查安装。" }
            }
        }
    }

    func startTranslation(sourceLanguage: String, targetLanguage: String, pages: String?, outputMode: String = "reading", retryBlockIds: [String]? = nil) {
        guard !isTranslating, let document = selectedDocument, !library.isTrashed(id: document.id), let repository else { return }
        do {
            guard engineReady else { throw BridgeError.message("翻译引擎未就绪。\(engineDescription)") }
            guard let service = activeService else { throw BridgeError.message("请先配置翻译服务。") }
            try service.validate()
            guard let pdf = PDFDocument(url: document.originalURL), pdf.pageCount == document.pageCount else { throw BridgeError.message("输入副本不可用，请重新导入。") }
            if outputMode != "reading" {
                let hasText = (0..<pdf.pageCount).contains { !(pdf.page(at: $0)?.string?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true) }
                guard hasText else { throw BridgeError.message("这份 PDF 没有可读取的文本层。请使用“对照阅读”进行扫描识别。") }
            }
            let apiKey = try key(for: service.id)
            if service.kind == .openAI && apiKey.isEmpty { throw BridgeError.message("请在翻译服务设置中填写 API Key。") }
            let attempt = UUID()
            let staging = try repository.attemptDirectory(document: document, attempt: attempt)
            let location = try EngineLocation.find()
            let process = EngineRunner(location: location)
            let request = WorkerRequest(action: "translate", jobID: document.id, attemptID: attempt,
                inputPath: document.inputPath, outputDirectory: staging.path, sourceLanguage: sourceLanguage,
                targetLanguage: targetLanguage, pages: pages, service: EngineService(configuration: service, apiKey: apiKey),
                outputMode: outputMode, readingPath: retryBlockIds == nil ? nil : document.readingPath, retryBlockIds: retryBlockIds)
            var record = document; record.status = .running; record.attemptID = attempt; record.lastError = nil
            try replaceRecord(record)
            runner = process; activeAttempt = attempt; activeJobDocumentID = document.id
            cancelRequested = false; isTranslating = true; progress = nil
            statusMessage = outputMode == "reading" ? "正在准备对照阅读；首次使用会下载本地识别模型…" : "正在启动引擎；首次运行可能需要准备字体与版面模型…"
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
                    let completed: DocumentRecord
                    if outputMode == "reading" {
                        completed = try repository.commitReading(result: result, document: document, attempt: attempt, staging: staging)
                    } else {
                        completed = try repository.commit(result: result, document: document, attempt: attempt, staging: staging,
                            service: service, sourceLanguage: sourceLanguage, targetLanguage: targetLanguage, pages: pages)
                    }
                    try replaceRecord(completed)
                    if selectedID == document.id {
                        readingMode = .dual; usesStructuredReading = outputMode == "reading"
                        if outputMode == "reading" && retryBlockIds == nil { syncScrolling = true }
                        if retryBlockIds == nil && outputMode != "reading" { page = 1 }
                        reloadStructuredReading(navigateToFirst: retryBlockIds == nil && outputMode == "reading")
                    }
                    if outputMode == "reading" {
                        statusMessage = completed.status == .partial
                            ? "原页译文已保存，未完成的段落保留原文，可单独重试。"
                            : "\(document.name) 原页译文已保存。点击文字或图表可查看完整内容。"
                    } else { statusMessage = "\(document.name) 翻译完成，译文和双语 PDF 已保存。" }
                    // Only disposable scratch output; committed results are in a different directory.
                    try? FileManager.default.removeItem(at: staging)
                } catch {
                    var failed = documents.first { $0.id == document.id } ?? document
                    let summary: TranslationFailureSummary? = cancelRequested || outputMode == "reading" ? nil : (try? repository.retainFailureDiagnostics(document: document, attempt: attempt, staging: staging))
                    let failureMessage = summary?.message(hasPreviousResult: failed.hasTranslation)
                        ?? TranslationFailureSummary.fallbackMessage(error.localizedDescription, hasPreviousResult: failed.hasTranslation)
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
        guard isTranslating, activeJobDocumentID == documentID, activeAttempt == attempt, !cancelRequested else { return }
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

    func exportSelected(_ kind: ExportKind) {
        guard let record = selectedDocument, let repository else { return }
        let source: URL?
        switch kind { case .original: source = record.originalURL; case .translated: source = record.translatedURL; case .bilingual: source = record.bilingualURL }
        guard let source else { errorMessage = "请先完成翻译。"; return }
        let panel = NSSavePanel(); panel.allowedContentTypes = [.pdf]
        panel.nameFieldStringValue = (record.name as NSString).deletingPathExtension + "-" + kind.title.replacingOccurrences(of: " PDF", with: "") + ".pdf"
        panel.title = "导出 \(kind.title)"; panel.prompt = "导出"
        guard panel.runModal() == .OK, let destination = panel.url else { return }
        do {
            try DocumentRepository.export(source: source, destination: destination, protectedInput: record.originalURL, managedRoot: repository.root)
            statusMessage = "已导出至 \(destination.path)"
        } catch { errorMessage = "导出失败：\(error.localizedDescription)" }
    }

    func openOutputFolder() {
        guard let record = selectedDocument else { return }
        NSWorkspace.shared.activateFileViewerSelecting([usesStructuredReading ? (record.readingURL ?? record.originalURL) : (record.translatedURL ?? record.originalURL)])
    }

    func renameDocument(id: UUID, name: String) {
        guard id != activeJobDocumentID, var record = documents.first(where: { $0.id == id }) else { return }
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, clean.count <= 240, !clean.contains("/"), !clean.contains(":"),
              !clean.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else {
            errorMessage = "文档名称应为 1–240 个字符，不能包含斜杠、冒号或换行。"
            return
        }
        record.name = clean.lowercased().hasSuffix(".pdf") ? clean : clean + ".pdf"
        do { try replaceRecord(record) }
        catch { errorMessage = error.localizedDescription }
    }

    func removeDocument(_ id: UUID) {
        guard id != activeJobDocumentID, let repository else { return }
        let alert = NSAlert(); alert.messageText = "从文档列表移除？"
        alert.informativeText = "本地输入副本和翻译结果仍保留在 PageBridge 数据目录中。"
        alert.addButton(withTitle: "移除记录"); alert.addButton(withTitle: "取消")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        do {
            let updated = documents.filter { $0.id != id }
            try repository.saveDocuments(updated); documents = updated
            if selectedID == id { selectedID = updated.first?.id; page = 1; readingMode = .original }
        } catch { errorMessage = error.localizedDescription }
    }

    func search() {
        searchRevision += 1; let revision = searchRevision
        let query = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let selectedDocument, !query.isEmpty else { searchHits = []; return }
        if readingMode == .translated && usesStructuredReading, let reading = structuredReading {
            searchHits = reading.pages.compactMap { page in
                let content = page.blocks.map(\.displayText).joined(separator: "\n")
                guard let range = content.range(of: query, options: [.caseInsensitive, .diacriticInsensitive]) else { return nil }
                let start = content.index(range.lowerBound, offsetBy: -25, limitedBy: content.startIndex) ?? content.startIndex
                let end = content.index(range.upperBound, offsetBy: 65, limitedBy: content.endIndex) ?? content.endIndex
                return SearchHit(page: page.pageNumber, text: String(content[start..<end]).replacingOccurrences(of: "\n", with: " "))
            }
            return
        }
        let url = (readingMode == .translated ? selectedDocument.translatedURL : nil) ?? selectedDocument.originalURL
        Task {
            let hits = await Task.detached(priority: .userInitiated) { () -> [SearchHit] in
                guard let pdf = PDFDocument(url: url) else { return [] }
                var hits: [SearchHit] = []
                for index in 0..<pdf.pageCount {
                    guard let content = pdf.page(at: index)?.string,
                          let range = content.range(of: query, options: [.caseInsensitive, .diacriticInsensitive]) else { continue }
                    let start = content.index(range.lowerBound, offsetBy: -25, limitedBy: content.startIndex) ?? content.startIndex
                    let end = content.index(range.upperBound, offsetBy: 65, limitedBy: content.endIndex) ?? content.endIndex
                    hits.append(SearchHit(page: index + 1, text: String(content[start..<end]).replacingOccurrences(of: "\n", with: " ")))
                    if hits.count >= 100 { break }
                }
                return hits
            }.value
            guard revision == searchRevision else { return }
            searchHits = hits
        }
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
        guard let document = selectedDocument, let reading = structuredReading, document.readingPath != nil, block.status == "source_fallback" else { return }
        startTranslation(sourceLanguage: reading.sourceLanguage, targetLanguage: reading.targetLanguage,
                         pages: nil, outputMode: "reading", retryBlockIds: [block.id])
    }

    func key(for id: UUID) throws -> String { try KeychainStore.read(serviceID: id) ?? "" }
    func saveService(_ service: ServiceConfiguration, apiKey: String) throws {
        guard let repository else { throw BridgeError.message("本地存储不可用，无法保存服务。请先修复启动时报告的数据目录错误。") }
        let value = service.normalized(); try value.validate()
        let previous = try key(for: value.id)
        if apiKey != previous {
            if apiKey.isEmpty { try KeychainStore.delete(serviceID: value.id) }
            else { try KeychainStore.save(key: apiKey, serviceID: value.id) }
        }
        var updated = services
        if let index = updated.firstIndex(where: { $0.id == value.id }) { updated[index] = value }
        else { updated.append(value) }
        do { try repository.savePreferences(AppPreferences(services: updated, activeServiceID: value.id)) }
        catch {
            if apiKey != previous {
                if previous.isEmpty { try? KeychainStore.delete(serviceID: value.id) }
                else { try? KeychainStore.save(key: previous, serviceID: value.id) }
            }
            throw error
        }
        services = updated; activeServiceID = value.id
    }
    func activateService(_ id: UUID) {
        guard services.contains(where: { $0.id == id }), let repository else { return }
        do { try repository.savePreferences(AppPreferences(services: services, activeServiceID: id)); activeServiceID = id }
        catch { errorMessage = error.localizedDescription }
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
