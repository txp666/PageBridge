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
        let override = ProcessInfo.processInfo.environment["PAGEBRIDGE_DATA_ROOT"].flatMap { $0.hasPrefix("/") ? URL(fileURLWithPath: $0, isDirectory: true) : nil }
        self.root = try root ?? override ?? fm.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("PageBridge", isDirectory: true)
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
    public static func sha256(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
        var hash = SHA256()
        while let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty { hash.update(data: chunk) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
    /// Every export, including the worker's reading exports, stays outside managed files.
    public static func validateExportDestination(_ destination: URL, protectedInput: URL, managedRoot: URL) throws {
        let managed = managedRoot.resolvingSymlinksInPath().path
        let target = destination.resolvingSymlinksInPath().path
        guard target != managed, !target.hasPrefix(managed + "/") else { throw BridgeError.message("请导出到文稿、桌面等其他目录，不能覆盖 PageBridge 管理的工作文件。") }
        guard destination.resolvingSymlinksInPath() != protectedInput.resolvingSymlinksInPath() else { throw BridgeError.message("请另选保存位置，不能覆盖工作文件。") }
    }
    public static func export(source: URL, destination: URL, protectedInput: URL, managedRoot: URL) throws {
        try validateExportDestination(destination, protectedInput: protectedInput, managedRoot: managedRoot)
        guard destination.resolvingSymlinksInPath() != source.resolvingSymlinksInPath() else { throw BridgeError.message("请另选保存位置，不能覆盖工作文件。") }
        guard let pdf = PDFDocument(url: source), pdf.pageCount > 0 else { throw BridgeError.message("要导出的 PDF 不可用。") }
        let temporary = destination.deletingLastPathComponent().appendingPathComponent(".pagebridge-\(UUID().uuidString).pdf")
        defer { try? FileManager.default.removeItem(at: temporary) }
        try FileManager.default.copyItem(at: source, to: temporary)
        if FileManager.default.fileExists(atPath: destination.path) {
            _ = try FileManager.default.replaceItemAt(destination, withItemAt: temporary)
        } else { try FileManager.default.moveItem(at: temporary, to: destination) }
    }
}
