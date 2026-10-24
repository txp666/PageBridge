import Foundation

public struct LibraryFolder: Identifiable, Codable, Equatable, Sendable {
    public var id: UUID
    public var name: String
    public var createdAt: Date

    public init(id: UUID = UUID(), name: String, createdAt: Date = Date()) {
        self.id = id; self.name = name; self.createdAt = createdAt
    }
}

public struct LibraryDocumentMetadata: Codable, Equatable, Sendable {
    public var folderID: UUID?
    public var isFavorite: Bool
    public var lastOpenedAt: Date?
    public var trashedAt: Date?

    public init(folderID: UUID? = nil, isFavorite: Bool = false, lastOpenedAt: Date? = nil, trashedAt: Date? = nil) {
        self.folderID = folderID; self.isFavorite = isFavorite
        self.lastOpenedAt = lastOpenedAt; self.trashedAt = trashedAt
    }

    private enum CodingKeys: String, CodingKey { case folderID, isFavorite, lastOpenedAt, trashedAt }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        folderID = try values.decodeIfPresent(UUID.self, forKey: .folderID)
        isFavorite = try values.decodeIfPresent(Bool.self, forKey: .isFavorite) ?? false
        lastOpenedAt = try values.decodeIfPresent(Date.self, forKey: .lastOpenedAt)
        trashedAt = try values.decodeIfPresent(Date.self, forKey: .trashedAt)
    }
}

public enum LibraryFilter: Hashable, Sendable {
    case all, uncategorized, favorites, recent, trash, folder(UUID)

    public var title: String {
        switch self {
        case .all: "全部文档"
        case .uncategorized: "未分类"
        case .favorites: "收藏"
        case .recent: "最近"
        case .trash: "回收站"
        case .folder: "文件夹"
        }
    }
}

public enum LibrarySortOrder: String, CaseIterable, Identifiable, Sendable {
    case recentActivity, newest, oldest, nameAscending, nameDescending
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .recentActivity: "最近打开"
        case .newest: "最新导入"
        case .oldest: "最早导入"
        case .nameAscending: "名称 A → Z"
        case .nameDescending: "名称 Z → A"
        }
    }
}

public struct LibraryState: Codable, Equatable, Sendable {
    public var version: Int
    public var folders: [LibraryFolder]
    public var documents: [String: LibraryDocumentMetadata]

    public init(version: Int = 1, folders: [LibraryFolder] = [], documents: [String: LibraryDocumentMetadata] = [:]) {
        self.version = version; self.folders = folders; self.documents = documents
    }

    public func metadata(for id: UUID) -> LibraryDocumentMetadata {
        documents[id.uuidString] ?? LibraryDocumentMetadata()
    }

    /// Old document records need no migration: absent metadata means unclassified and visible.
    public func filteredDocuments(_ records: [DocumentRecord], filter: LibraryFilter, query: String = "",
                                  sort: LibrarySortOrder = .recentActivity, now: Date = Date()) -> [DocumentRecord] {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let cutoff = now.addingTimeInterval(-30 * 24 * 60 * 60)
        let selected = records.filter { record in
            let meta = metadata(for: record.id)
            let matches: Bool
            switch filter {
            case .trash: matches = meta.trashedAt != nil
            case .all: matches = meta.trashedAt == nil
            case .uncategorized: matches = meta.trashedAt == nil && meta.folderID == nil
            case .favorites: matches = meta.trashedAt == nil && meta.isFavorite
            case .recent: matches = meta.trashedAt == nil && (meta.lastOpenedAt ?? record.importedAt) >= cutoff
            case .folder(let id): matches = meta.trashedAt == nil && meta.folderID == id
            }
            return matches && (query.isEmpty || record.name.localizedStandardContains(query))
        }
        return selected.sorted { lhs, rhs in
            let comparison: ComparisonResult
            switch sort {
            case .nameAscending, .nameDescending:
                comparison = lhs.name.localizedStandardCompare(rhs.name)
            case .newest, .oldest:
                comparison = lhs.importedAt.compare(rhs.importedAt)
            case .recentActivity:
                let a = metadata(for: lhs.id).lastOpenedAt ?? lhs.importedAt
                let b = metadata(for: rhs.id).lastOpenedAt ?? rhs.importedAt
                comparison = a.compare(b)
            }
            if comparison == .orderedSame { return lhs.id.uuidString < rhs.id.uuidString }
            return (sort == .nameAscending || sort == .oldest) ? comparison == .orderedAscending : comparison == .orderedDescending
        }
    }
}

public enum LibraryStoreError: LocalizedError {
    case invalidName, duplicateFolder, missingFolder, invalidData, unsupportedVersion(Int), busyDocument
    public var errorDescription: String? {
        switch self {
        case .invalidName: "文件夹名称不能为空，且不能超过 100 个字符或包含换行。"
        case .duplicateFolder: "已有同名文件夹，请使用其他名称。"
        case .missingFolder: "文件夹已不存在，请重新选择。"
        case .invalidData: "文档库分类文件损坏，尚未改动。请恢复 library.json 后重试。"
        case .unsupportedVersion(let version): "文档库分类版本 \(version) 暂不支持，尚未改动。请使用对应版本的 PageBridge。"
        case .busyDocument: "文档正在翻译或取消任务，暂时不能移入回收站。"
        }
    }
}

/// Metadata only. No operation in this store deletes or renames user PDFs or documents.json.
public final class LibraryStore {
    public let root: URL
    public var fileURL: URL { root.appendingPathComponent("library.json") }
    private let lock = NSLock()

    public init(root: URL? = nil) throws {
        self.root = try root ?? DocumentRepository().root
        try FileManager.default.createDirectory(at: self.root, withIntermediateDirectories: true)
    }

    public func load() throws -> LibraryState {
        lock.lock(); defer { lock.unlock() }
        return try read()
    }

    private func read() throws -> LibraryState {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return LibraryState() }
        let data = try Data(contentsOf: fileURL)
        // Inspect the version before decoding a possibly newer schema. Never reset unreadable metadata.
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let version = object["version"] as? Int else { throw LibraryStoreError.invalidData }
        guard version == 1 else { throw LibraryStoreError.unsupportedVersion(version) }
        let state: LibraryState
        do { state = try JSONDecoder().decode(LibraryState.self, from: data) }
        catch { throw LibraryStoreError.invalidData }
        let ids = Set(state.folders.map(\.id))
        guard ids.count == state.folders.count,
              state.folders.allSatisfy({ validName($0.name) }),
              Set(state.folders.map { $0.name.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current) }).count == state.folders.count,
              state.documents.allSatisfy({ key, value in UUID(uuidString: key)?.uuidString == key && (value.folderID == nil || ids.contains(value.folderID!)) })
        else { throw LibraryStoreError.invalidData }
        return state
    }

    private func update(_ mutation: (inout LibraryState) throws -> Void) throws -> LibraryState {
        lock.lock(); defer { lock.unlock() }
        var state = try read()
        try mutation(&state)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(state).write(to: fileURL, options: .atomic)
        return state
    }

    private func validName(_ name: String) -> Bool {
        !name.isEmpty && name.count <= 100 && name.rangeOfCharacter(from: .controlCharacters) == nil
            && name == name.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func checkedName(_ name: String, state: LibraryState, excluding id: UUID? = nil) throws -> String {
        let value = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard validName(value) else { throw LibraryStoreError.invalidName }
        guard !state.folders.contains(where: { $0.id != id && $0.name.compare(value, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame }) else {
            throw LibraryStoreError.duplicateFolder
        }
        return value
    }

    @discardableResult public func createFolder(id: UUID = UUID(), name: String) throws -> LibraryState {
        try update { state in
            guard !state.folders.contains(where: { $0.id == id }) else { throw LibraryStoreError.duplicateFolder }
            let name = try checkedName(name, state: state)
            state.folders.append(LibraryFolder(id: id, name: name))
        }
    }

    @discardableResult public func renameFolder(id: UUID, name: String) throws -> LibraryState {
        try update { state in
            guard let index = state.folders.firstIndex(where: { $0.id == id }) else { throw LibraryStoreError.missingFolder }
            state.folders[index].name = try checkedName(name, state: state, excluding: id)
        }
    }

    @discardableResult public func deleteFolder(id: UUID) throws -> LibraryState {
        try update { state in
            guard state.folders.contains(where: { $0.id == id }) else { throw LibraryStoreError.missingFolder }
            state.folders.removeAll { $0.id == id }
            for key in Array(state.documents.keys) where state.documents[key]?.folderID == id {
                state.documents[key]?.folderID = nil
            }
        }
    }

    @discardableResult public func moveDocuments(ids: [UUID], to folderID: UUID?) throws -> LibraryState {
        try update { state in
            if let folderID, !state.folders.contains(where: { $0.id == folderID }) { throw LibraryStoreError.missingFolder }
            for id in Set(ids) {
                var meta = state.metadata(for: id); meta.folderID = folderID
                state.documents[id.uuidString] = meta
            }
        }
    }

    @discardableResult public func setFavorite(id: UUID, value: Bool) throws -> LibraryState {
        try update { state in
            var meta = state.metadata(for: id); meta.isFavorite = value
            state.documents[id.uuidString] = meta
        }
    }

    @discardableResult public func markOpened(id: UUID, at date: Date = Date()) throws -> LibraryState {
        try update { state in
            var meta = state.metadata(for: id)
            guard meta.trashedAt == nil else { return }
            meta.lastOpenedAt = date; state.documents[id.uuidString] = meta
        }
    }

    @discardableResult public func trashDocuments(_ records: [DocumentRecord], at date: Date = Date()) throws -> LibraryState {
        guard !records.contains(where: { $0.status == .running || $0.status == .cancelling }) else { throw LibraryStoreError.busyDocument }
        return try update { state in
            for record in records {
                var meta = state.metadata(for: record.id); meta.trashedAt = meta.trashedAt ?? date
                state.documents[record.id.uuidString] = meta
            }
        }
    }

    @discardableResult public func restoreDocuments(ids: [UUID]) throws -> LibraryState {
        try update { state in
            for id in Set(ids) {
                var meta = state.metadata(for: id); meta.trashedAt = nil
                state.documents[id.uuidString] = meta
            }
        }
    }
}
