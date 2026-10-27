import Foundation

/// The reading artifact stores translated text alongside its immutable source
/// identity. It is deliberately independent of a regenerated PDF's page count.
public struct StructuredReadingDocument: Codable, Sendable, Equatable {
    public var schemaVersion: Int
    public var artifactType: String
    public var sourceSHA256: String
    public var pageCount: Int
    public var sourceLanguage: String
    public var targetLanguage: String
    public var status: String
    public var selectedPages: [Int]
    public var pages: [StructuredReadingPage]

    public var failedBlockCount: Int { pages.flatMap(\.blocks).filter { $0.status == "source_fallback" }.count }
    public var failedPageCount: Int { pages.filter { $0.status == "source_fallback" }.count }
    public var isPartial: Bool { status == "partial" || failedBlockCount > 0 || failedPageCount > 0 }
    public func page(_ number: Int) -> StructuredReadingPage? { pages.first { $0.pageNumber == number } }

    public static func load(_ url: URL, for document: DocumentRecord, verifySource: Bool = true) throws -> Self {
        let info = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard info.isRegularFile == true, info.isSymbolicLink != true,
              let size = info.fileSize, size > 0, size <= 64 * 1024 * 1024 else {
            throw BridgeError.message("对照阅读文件不可用或过大。")
        }
        let reading = try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
        try reading.validate(for: document)
        if verifySource, try DocumentRepository.sha256(document.originalURL) != document.inputSHA256 {
            throw BridgeError.message("原 PDF 已发生变化，无法关联这份阅读稿。")
        }
        return reading
    }

    /// Runtime integrity only: no language, formula or content-quality gates.
    public func validate(for document: DocumentRecord) throws {
        guard schemaVersion == 1, artifactType == "structured-reading",
              sourceSHA256 == document.inputSHA256, pageCount == document.pageCount,
              ["completed", "partial"].contains(status), !selectedPages.isEmpty,
              Set(selectedPages).count == selectedPages.count,
              selectedPages.allSatisfy({ (1...pageCount).contains($0) }),
              Set(pages.map(\.pageNumber)) == Set(selectedPages), pages.count == selectedPages.count else {
            throw BridgeError.message("阅读稿与原 PDF 或所选页码不匹配。")
        }
        var identifiers = Set<String>()
        for page in pages {
            guard page.width.isFinite, page.height.isFinite, page.width > 0, page.height > 0 else {
                throw BridgeError.message("阅读稿页面尺寸无效。")
            }
            guard page.decorationsCoordinateSpace == nil || page.decorationsCoordinateSpace == "page" else {
                throw BridgeError.message("阅读稿的原页坐标无效。")
            }
            if let background = page.backgroundImage {
                let allowed = Set(page.blocks.filter {
                    ["text", "heading", "list"].contains($0.kind) && $0.status == "translated"
                }.map(\.id))
                guard StructuredReadingBackground.pngData(background) != nil,
                      let blocks = page.backgroundBlocks,
                      Set(blocks).count == blocks.count, Set(blocks).isSubset(of: allowed) else {
                    throw BridgeError.message("阅读稿的原页背景无效。")
                }
            } else if page.backgroundBlocks != nil {
                throw BridgeError.message("阅读稿缺少原页背景。")
            }
            for path in page.decorations ?? [] {
                guard path.kind == nil || path.kind == "stroke" || path.kind == "fill_boundary" else {
                    throw BridgeError.message("阅读稿的原页边缘类型无效。")
                }
                guard path.width.isFinite, path.width >= 0,
                      path.dash.allSatisfy({ $0.isFinite && $0 >= 0 }), path.dashPhase.isFinite,
                      (0...2).contains(path.lineCap), (0...2).contains(path.lineJoin),
                      path.color.count == 3, path.color.allSatisfy({ $0.isFinite && (0...255).contains($0) }),
                      path.opacity.isFinite, (0...1).contains(path.opacity),
                      path.commands.allSatisfy({ command in
                          ["M": 2, "L": 2, "C": 6, "Z": 0][command.op] == command.points.count
                              && command.points.allSatisfy(\.isFinite)
                      }) else { throw BridgeError.message("阅读稿的原页描边数据无效。") }
            }
            for block in page.blocks {
                guard !block.id.isEmpty, identifiers.insert(block.id).inserted,
                      ["translated", "source_fallback", "preserved"].contains(block.status) else {
                    throw BridgeError.message("阅读稿的段落标识或状态无效。")
                }
                if block.status == "translated", block.translatedText?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false {
                    throw BridgeError.message("阅读稿中的已译段落没有译文。")
                }
                if let box = block.bbox {
                    guard box.count == 4, box.allSatisfy(\.isFinite), box[2] > box[0], box[3] > box[1] else {
                        throw BridgeError.message("阅读稿的原文区域坐标无效。")
                    }
                }
                for color in [block.backgroundColor, block.foregroundColor].compactMap({ $0 }) {
                    guard color.count == 3, color.allSatisfy({ $0.isFinite && (0...255).contains($0) }) else {
                        throw BridgeError.message("阅读稿的文字背景颜色无效。")
                    }
                }
                var tokens = Set<String>()
                for formula in block.inlineMath ?? [] {
                    guard !formula.token.isEmpty, tokens.insert(formula.token).inserted,
                          formula.bbox.count == 4, formula.bbox.allSatisfy(\.isFinite),
                          0 <= formula.bbox[0], formula.bbox[0] < formula.bbox[2], formula.bbox[2] <= page.width + 1,
                          0 <= formula.bbox[1], formula.bbox[1] < formula.bbox[3], formula.bbox[3] <= page.height + 1
                    else { throw BridgeError.message("阅读稿的行内公式区域无效。") }
                }
            }
        }
    }
}

public struct StructuredReadingPage: Codable, Sendable, Equatable, Identifiable {
    public var pageNumber: Int
    public var width: Double
    public var height: Double
    public var blocks: [StructuredReadingBlock]
    public var status: String?
    public var error: String?
    public var decorations: [StructuredReadingDecoration]?
    public var decorationsCoordinateSpace: String?
    public var backgroundImage: String?
    public var backgroundBlocks: [String]?
    public var id: Int { pageNumber }
}

public struct StructuredReadingBlock: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var kind: String
    public var sourceType: String?
    public var sourceText: String
    public var translatedText: String?
    public var status: String
    public var bbox: [Double]?
    public var error: String?
    public var backgroundColor: [Double]?
    public var foregroundColor: [Double]?
    public var sourceTemplate: String?
    public var translatedTemplate: String?
    public var inlineMath: [StructuredReadingInlineMath]?
    public var isSourceRegion: Bool { ["table", "formula", "figure", "code"].contains(kind) }
    public var displayText: String { status == "translated" ? (translatedText ?? sourceText) : sourceText }
}

public struct StructuredReadingInlineMath: Codable, Sendable, Equatable {
    public var token: String
    public var latex: String
    public var bbox: [Double]
}

public struct StructuredReadingDecoration: Codable, Sendable, Equatable {
    public var commands: [StructuredReadingPathCommand]
    public var width: Double
    public var dash: [Double]
    public var dashPhase: Double
    public var lineCap: Int
    public var lineJoin: Int
    public var color: [Double]
    public var opacity: Double
    public var kind: String?
}

public struct StructuredReadingPathCommand: Codable, Sendable, Equatable {
    public var op: String
    public var points: [Double]
}

extension DocumentRepository {
    public func commitReading(result: WorkerEvent, document: DocumentRecord, attempt: UUID, staging: URL) throws -> DocumentRecord {
        let fm = FileManager.default
        let documentFolder = document.originalURL.deletingLastPathComponent().standardizedFileURL
        let folder = documentFolder.resolvingSymlinksInPath()
        let managed = root.resolvingSymlinksInPath().standardizedFileURL.appendingPathComponent("documents", isDirectory: true)
        let expected = documentFolder.appendingPathComponent("attempts/\(attempt.uuidString)").standardizedFileURL
        let canonicalStaging = folder.appendingPathComponent("attempts/\(attempt.uuidString)").standardizedFileURL
        let staged = staging.standardizedFileURL
        guard result.artifactType == "structured-reading", result.jobId == document.id.uuidString,
              result.attemptId == attempt.uuidString, let path = result.readingPath,
              folder.path.hasPrefix(managed.path + "/"), staged.path == expected.path,
              staged.resolvingSymlinksInPath().path == canonicalStaging.path else {
            throw BridgeError.message("阅读稿的任务身份或提交目录不匹配。")
        }
        let source = URL(fileURLWithPath: path).standardizedFileURL
        guard source.resolvingSymlinksInPath().path.hasPrefix(canonicalStaging.path + "/") else {
            throw BridgeError.message("阅读稿输出路径超出任务目录。")
        }
        let reading = try StructuredReadingDocument.load(source, for: document)
        let digest = try Self.sha256(source)
        guard result.readingSHA256 == digest else { throw BridgeError.message("阅读稿输出校验失败。") }
        let results = folder.appendingPathComponent("results", isDirectory: true)
        guard results.resolvingSymlinksInPath().standardizedFileURL.path == folder.appendingPathComponent("results").path else {
            throw BridgeError.message("阅读稿保存目录不可用。")
        }
        try fm.createDirectory(at: results, withIntermediateDirectories: true)
        let commit = staging.appendingPathComponent("commit-reading", isDirectory: true)
        try fm.createDirectory(at: commit, withIntermediateDirectories: false)
        let copied = commit.appendingPathComponent("reading.json")
        try fm.copyItem(at: source, to: copied)
        guard try Self.sha256(copied) == digest,
              try Self.sha256(document.originalURL) == document.inputSHA256 else {
            throw BridgeError.message("保存过程中原文或阅读稿发生变化，未提交结果。")
        }
        struct Manifest: Encodable {
            let documentID: UUID
            let attemptID: UUID
            let sourceSHA256: String
            let readingSHA256: String
            let selectedPages: [Int]
            let createdAt: Date
        }
        let manifest = Manifest(documentID: document.id, attemptID: attempt, sourceSHA256: document.inputSHA256,
                                readingSHA256: digest, selectedPages: reading.selectedPages, createdAt: Date())
        try JSONEncoder().encode(manifest).write(to: commit.appendingPathComponent("manifest.json"), options: .atomic)
        let destination = results.appendingPathComponent(attempt.uuidString, isDirectory: true)
        try fm.moveItem(at: commit, to: destination)
        var updated = document
        updated.readingPath = destination.appendingPathComponent("reading.json").path
        updated.preferredOutputMode = "reading"
        updated.sourceLanguage = reading.sourceLanguage; updated.targetLanguage = reading.targetLanguage
        updated.status = reading.isPartial ? .partial : .completed
        updated.lastError = nil; updated.attemptID = attempt
        return updated
    }
}
