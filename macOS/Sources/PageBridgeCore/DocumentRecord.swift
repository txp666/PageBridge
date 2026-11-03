import Foundation

public enum DocumentStatus: String, Codable, Sendable {
    case ready, running, cancelling, completed, partial, failed, cancelled, interrupted
    public var title: String {
        switch self {
        case .ready: "待翻译"
        case .running: "翻译中"
        case .cancelling: "正在取消"
        case .completed: "已完成"
        case .partial: "部分完成"
        case .failed: "失败"
        case .cancelled: "已取消"
        case .interrupted: "已中断"
        }
    }
}

public struct DocumentRecord: Identifiable, Codable, Sendable, Equatable {
    public var id: UUID
    public var name: String
    public var importedAt: Date
    public var pageCount: Int
    public var status: DocumentStatus
    public var inputPath: String
    public var readingPath: String?
    public var sourceLanguage: String
    public var targetLanguage: String
    public var lastError: String?
    public var attemptID: UUID?
    public var inputSHA256: String

    public init(id: UUID = UUID(), name: String, pageCount: Int, inputPath: String, inputSHA256: String) {
        self.id = id; self.name = name; self.pageCount = pageCount
        self.inputPath = inputPath; self.inputSHA256 = inputSHA256
        self.importedAt = Date(); self.status = .ready
        self.sourceLanguage = "en"; self.targetLanguage = "zh"
    }
    public var readingURL: URL? { readingPath.map(URL.init(fileURLWithPath:)) }
    public var hasTranslation: Bool { readingPath != nil }
    /// A 0.5.x 重排 PDF result is no longer shown, so such a document reads as untranslated.
    public var statusTitle: String {
        (status == .completed || status == .partial) && readingPath == nil ? DocumentStatus.ready.title : status.title
    }
    public var originalURL: URL { URL(fileURLWithPath: inputPath) }
}

public enum ReadingMode: String, CaseIterable, Identifiable, Sendable {
    case original, dual, translated
    public var id: String { rawValue }
    public var title: String { switch self { case .original: "原文"; case .dual: "对照"; case .translated: "译文" } }
}

public enum ExportKind: String, CaseIterable, Identifiable, Sendable {
    case original, translated, bilingual
    public var id: String { rawValue }
    public var title: String { switch self { case .original: "原文 PDF"; case .translated: "译文 PDF"; case .bilingual: "双语 PDF" } }
}

public struct SearchHit: Identifiable, Sendable {
    public var id: Int { page }
    public var page: Int
    public var text: String
    public init(page: Int, text: String) { self.page = page; self.text = text }
}
