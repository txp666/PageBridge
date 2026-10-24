import Foundation
import CryptoKit
import PDFKit

public struct AppPreferences: Codable {
    public var services: [ServiceConfiguration]
    public var activeServiceID: UUID?
    public init(services: [ServiceConfiguration], activeServiceID: UUID?) {
        self.services = services; self.activeServiceID = activeServiceID
    }
}

public final class DocumentRepository {
    public let root: URL
    private let fm = FileManager.default
    public init(root: URL? = nil) throws {
        self.root = try root ?? fm.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("PageBridge", isDirectory: true)
        try fm.createDirectory(at: self.root, withIntermediateDirectories: true)
    }
    public func loadDocuments() throws -> [DocumentRecord] {
        let file = root.appendingPathComponent("documents.json")
        guard fm.fileExists(atPath: file.path) else { return [] }
        return try JSONDecoder().decode([DocumentRecord].self, from: Data(contentsOf: file))
    }
    public func saveDocuments(_ documents: [DocumentRecord]) throws {
        try JSONEncoder().encode(documents).write(to: root.appendingPathComponent("documents.json"), options: .atomic)
    }
    public func loadPreferences() throws -> AppPreferences? {
        let file = root.appendingPathComponent("services.json")
        guard fm.fileExists(atPath: file.path) else { return nil }
        return try JSONDecoder().decode(AppPreferences.self, from: Data(contentsOf: file))
    }
    public func savePreferences(_ preferences: AppPreferences) throws {
        try JSONEncoder().encode(preferences).write(to: root.appendingPathComponent("services.json"), options: .atomic)
    }
    public func importPDF(_ source: URL) throws -> DocumentRecord {
        guard source.isFileURL else { throw BridgeError.message("只能导入本机 PDF 文件。") }
        let values = try source.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
        guard values.isRegularFile == true else { throw BridgeError.message("请选择一个 PDF 文件。") }
        guard (values.fileSize ?? 0) > 0, (values.fileSize ?? 0) <= 500 * 1024 * 1024 else { throw BridgeError.message("文件为空或超过 500 MB。") }
        guard let pdf = PDFDocument(url: source), pdf.pageCount > 0 else { throw BridgeError.message("无法读取 \(source.lastPathComponent)，文件可能已损坏。") }
        guard !pdf.isLocked, !pdf.isEncrypted else { throw BridgeError.message("暂不支持加密 PDF。请先在预览中解密并保存副本。") }
        let id = UUID(), folder = root.appendingPathComponent("documents/\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        let input = folder.appendingPathComponent("input.pdf")
        do {
            try fm.copyItem(at: source, to: input)
            return DocumentRecord(id: id, name: source.lastPathComponent, pageCount: pdf.pageCount, inputPath: input.path, inputSHA256: try Self.sha256(input))
        } catch { try? fm.removeItem(at: folder); throw error }
    }
    public func attemptDirectory(document: DocumentRecord, attempt: UUID) throws -> URL {
        let directory = document.originalURL.deletingLastPathComponent().appendingPathComponent("attempts/\(attempt.uuidString)", isDirectory: true)
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }
    /// Retain only a validated, text-free summary before disposable staging is
    /// removed. Neither rejected PDFs nor arbitrary worker files are copied.
    public func retainFailureDiagnostics(document: DocumentRecord, attempt: UUID, staging: URL) throws -> TranslationFailureSummary? {
        let folder = document.originalURL.deletingLastPathComponent().resolvingSymlinksInPath().standardizedFileURL
        let managed = root.resolvingSymlinksInPath().standardizedFileURL.appendingPathComponent("documents", isDirectory: true)
        let expected = folder.appendingPathComponent("attempts/\(attempt.uuidString)", isDirectory: true)
        let expectedRaw = document.originalURL.deletingLastPathComponent().appendingPathComponent("attempts/\(attempt.uuidString)", isDirectory: true).standardizedFileURL
        guard folder.path.hasPrefix(managed.path + "/"),
              staging.standardizedFileURL == expectedRaw,
              staging.resolvingSymlinksInPath().standardizedFileURL == expected,
              document.inputSHA256.count == 64,
              document.inputSHA256.allSatisfy({ "0123456789abcdef".contains($0) }) else {
            throw BridgeError.message("失败诊断目录与任务不匹配。")
        }
        let source = staging.appendingPathComponent("rejected-quality-\(attempt.uuidString).json")
        guard fm.fileExists(atPath: source.path) else { return nil }
        let values = try source.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              let size = values.fileSize, size > 0, size <= TranslationFailureSummary.maximumReportBytes else {
            throw BridgeError.message("失败诊断文件格式无效。")
        }
        let summary = try TranslationFailureSummary.decodeWorkerReport(Data(contentsOf: source))
        let directory = folder.appendingPathComponent("diagnostics", isDirectory: true)
        guard directory.resolvingSymlinksInPath().standardizedFileURL == directory else {
            throw BridgeError.message("失败诊断目录不可用。")
        }
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)
        let target = directory.appendingPathComponent(attempt.uuidString + ".json")
        guard target.resolvingSymlinksInPath().standardizedFileURL == target else {
            throw BridgeError.message("失败诊断文件不可用。")
        }
        struct Record: Encodable {
            let documentID: UUID
            let attemptID: UUID
            let inputSHA256: String
            let recordedAt: Date
            let summary: TranslationFailureSummary
        }
        let record = Record(documentID: document.id, attemptID: attempt, inputSHA256: document.inputSHA256,
                            recordedAt: Date(), summary: summary)
        try JSONEncoder().encode(record).write(to: target, options: .atomic)
        return summary
    }
    public func commit(result: WorkerEvent, document: DocumentRecord, attempt: UUID, staging: URL, service: ServiceConfiguration, sourceLanguage: String, targetLanguage: String, pages: String?) throws -> DocumentRecord {
        guard let mono = result.translatedPath, let dual = result.bilingualPath else { throw BridgeError.message("引擎没有返回完整的译文和双语 PDF。") }
        let monoURL = URL(fileURLWithPath: mono).resolvingSymlinksInPath()
        let dualURL = URL(fileURLWithPath: dual).resolvingSymlinksInPath()
        let prefix = staging.resolvingSymlinksInPath().path + "/"
        guard monoURL.path.hasPrefix(prefix), dualURL.path.hasPrefix(prefix), monoURL != dualURL else { throw BridgeError.message("引擎输出路径超出任务目录。") }
        guard let monoPDF = PDFDocument(url: monoURL), !monoPDF.isLocked, monoPDF.pageCount > 0,
              let dualPDF = PDFDocument(url: dualURL), !dualPDF.isLocked, dualPDF.pageCount > 0 else { throw BridgeError.message("输出 PDF 未通过完整性检查。") }
        // V1 uses alternating bilingual pages and preserves all source pages.
        guard monoPDF.pageCount == document.pageCount, dualPDF.pageCount == document.pageCount * 2 else { throw BridgeError.message("输出页数与输入不一致，无法建立可靠的对照映射。结果尚未提交。") }
        guard try Self.sha256(document.originalURL) == document.inputSHA256 else { throw BridgeError.message("原始副本发生变化，已停止提交结果。") }
        let commitFolder = staging.appendingPathComponent("commit", isDirectory: true)
        try fm.createDirectory(at: commitFolder, withIntermediateDirectories: false)
        let translated = commitFolder.appendingPathComponent("translated.pdf")
        let bilingual = commitFolder.appendingPathComponent("bilingual.pdf")
        try fm.copyItem(at: monoURL, to: translated); try fm.copyItem(at: dualURL, to: bilingual)
        let quality = monoURL.deletingLastPathComponent().appendingPathComponent("translation-quality.json").resolvingSymlinksInPath()
        if fm.fileExists(atPath: quality.path) {
            guard quality.path.hasPrefix(prefix) else { throw BridgeError.message("质量校验记录超出任务目录。") }
            try fm.copyItem(at: quality, to: commitFolder.appendingPathComponent("translation-quality.json"))
        }
        let manifest = OutputManifest(documentID: document.id, attemptID: attempt, createdAt: Date(), inputSHA256: document.inputSHA256,
            translatedSHA256: try Self.sha256(translated), bilingualSHA256: try Self.sha256(bilingual),
            pageCount: monoPDF.pageCount, bilingualPageCount: dualPDF.pageCount, service: service,
            sourceLanguage: sourceLanguage, targetLanguage: targetLanguage, pages: pages, engineVersion: result.engineVersion)
        try JSONEncoder().encode(manifest).write(to: commitFolder.appendingPathComponent("manifest.json"), options: .atomic)
        let results = document.originalURL.deletingLastPathComponent().appendingPathComponent("results", isDirectory: true)
        try fm.createDirectory(at: results, withIntermediateDirectories: true)
        let destination = results.appendingPathComponent(attempt.uuidString, isDirectory: true)
        try fm.moveItem(at: commitFolder, to: destination)
        var updated = document
        updated.translatedPath = destination.appendingPathComponent("translated.pdf").path
        updated.bilingualPath = destination.appendingPathComponent("bilingual.pdf").path
        updated.sourceLanguage = sourceLanguage; updated.targetLanguage = targetLanguage
        updated.status = .completed; updated.lastError = nil; updated.attemptID = attempt
        return updated
    }
    public static func sha256(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
        var hash = SHA256()
        while let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty { hash.update(data: chunk) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
    public static func export(source: URL, destination: URL, protectedInput: URL, managedRoot: URL? = nil) throws {
        if let managedRoot {
            let managed = managedRoot.resolvingSymlinksInPath().path
            let target = destination.resolvingSymlinksInPath().path
            guard target != managed, !target.hasPrefix(managed + "/") else { throw BridgeError.message("请导出到文稿、桌面等其他目录，不能覆盖 PageBridge 管理的工作文件。") }
        }
        guard destination.resolvingSymlinksInPath() != protectedInput.resolvingSymlinksInPath(), destination.resolvingSymlinksInPath() != source.resolvingSymlinksInPath() else { throw BridgeError.message("请另选保存位置，不能覆盖工作文件。") }
        guard let pdf = PDFDocument(url: source), pdf.pageCount > 0 else { throw BridgeError.message("要导出的 PDF 不可用。") }
        let temporary = destination.deletingLastPathComponent().appendingPathComponent(".pagebridge-\(UUID().uuidString).pdf")
        defer { try? FileManager.default.removeItem(at: temporary) }
        try FileManager.default.copyItem(at: source, to: temporary)
        if FileManager.default.fileExists(atPath: destination.path) {
            _ = try FileManager.default.replaceItemAt(destination, withItemAt: temporary)
        } else { try FileManager.default.moveItem(at: temporary, to: destination) }
    }
}

public struct OutputManifest: Codable {
    public var documentID: UUID
    public var attemptID: UUID
    public var createdAt: Date
    public var inputSHA256: String
    public var translatedSHA256: String
    public var bilingualSHA256: String
    public var pageCount: Int
    public var bilingualPageCount: Int
    public var service: ServiceConfiguration
    public var sourceLanguage: String
    public var targetLanguage: String
    public var pages: String?
    public var engineVersion: String?
}
