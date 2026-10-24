import Foundation
import CryptoKit
import PDFKit

public final class AnnotationStore {
    public let root: URL
    private struct Envelope: Codable {
        var version = 1
        var binding: AnnotationBinding
        var annotations: [ReadingAnnotation]
    }
    public init(root: URL? = nil) throws { self.root = try DocumentRepository(root: root).root }

    public func fileURL(for binding: AnnotationBinding) throws -> URL {
        guard binding.sourceSHA256.count == 64, binding.sourceSHA256.allSatisfy({ "0123456789abcdef".contains($0) }) else {
            throw BridgeError.message("批注文件的 PDF 校验值无效。")
        }
        return root.appendingPathComponent("annotations", isDirectory: true)
            .appendingPathComponent(binding.documentID.uuidString, isDirectory: true)
            .appendingPathComponent(binding.variant.rawValue, isDirectory: true)
            .appendingPathComponent(binding.sourceSHA256 + ".json")
    }
    public func load(for binding: AnnotationBinding) throws -> [ReadingAnnotation] {
        let file = try fileURL(for: binding)
        guard FileManager.default.fileExists(atPath: file.path) else { return [] }
        let envelope = try JSONDecoder().decode(Envelope.self, from: Data(contentsOf: file))
        guard envelope.version == 1, envelope.binding == binding else { throw BridgeError.message("批注与当前 PDF 版本不匹配，未载入旧批注。") }
        return envelope.annotations
    }
    public func save(_ annotations: [ReadingAnnotation], for binding: AnnotationBinding, sourceURL: URL) throws {
        let document = try verifiedDocument(sourceURL, binding: binding)
        try PDFAnnotationRenderer.validate(annotations, pageCount: document.pageCount)
        let file = try fileURL(for: binding)
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(Envelope(binding: binding, annotations: annotations)).write(to: file, options: .atomic)
    }
    public func verifiedDocument(_ source: URL, binding: AnnotationBinding) throws -> PDFDocument {
        let data = try Data(contentsOf: source)
        let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard hash == binding.sourceSHA256 else { throw BridgeError.message("PDF 内容已变化，请重新打开文档后添加批注。旧批注仍保留在原版本中。") }
        guard let document = PDFDocument(data: data), !document.isLocked, document.pageCount > 0 else { throw BridgeError.message("无法读取要批注的 PDF。") }
        return document
    }
    public func exportAnnotatedPDF(source: URL, binding: AnnotationBinding, destination: URL, protectedURLs: [URL] = []) throws {
        try validateDestination(destination, protectedURLs: protectedURLs + [source])
        let document = try verifiedDocument(source, binding: binding)
        try PDFAnnotationRenderer.apply(load(for: binding), to: document)
        guard let data = document.dataRepresentation() else { throw BridgeError.message("无法生成带批注的 PDF。") }
        try data.write(to: destination, options: .atomic)
    }
    public func exportNotes(title: String, source: URL, binding: AnnotationBinding, destination: URL, protectedURLs: [URL] = []) throws {
        try validateDestination(destination, protectedURLs: protectedURLs + [source])
        _ = try verifiedDocument(source, binding: binding)
        let text = markdown(title: title, binding: binding, annotations: try load(for: binding))
        try Data(text.utf8).write(to: destination, options: .atomic)
    }
    public func markdown(title: String, binding: AnnotationBinding, annotations: [ReadingAnnotation]) -> String {
        var lines = ["# " + title.replacingOccurrences(of: "\n", with: " "), "", "来源：\(binding.variant.title)", "", "PDF SHA-256：`\(binding.sourceSHA256)`", ""]
        for item in annotations.sorted(by: { $0.page == $1.page ? $0.createdAt < $1.createdAt : $0.page < $1.page }) {
            lines += ["## 第 \(item.page) 页 · \(item.kind.title)", ""]
            if !item.selectedText.isEmpty { lines += item.selectedText.components(separatedBy: .newlines).map { "> " + $0 }; lines.append("") }
            if !item.note.isEmpty { lines += [item.note, ""] }
        }
        return lines.joined(separator: "\n")
    }
    private func validateDestination(_ destination: URL, protectedURLs: [URL]) throws {
        let target = destination.resolvingSymlinksInPath().standardizedFileURL
        let managed = root.resolvingSymlinksInPath().standardizedFileURL.path
        guard target.isFileURL, target.path != managed, !target.path.hasPrefix(managed + "/"),
              !protectedURLs.contains(where: { $0.resolvingSymlinksInPath().standardizedFileURL == target }) else {
            throw BridgeError.message("请另存到文稿或桌面；不能覆盖原文、译文或 PageBridge 工作文件。")
        }
    }
}
