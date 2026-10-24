import Combine
import Foundation
import PageBridgeCore

@MainActor
final class LibraryModel: ObservableObject {
    @Published private(set) var state = LibraryState()
    @Published private(set) var isAvailable = false
    @Published var filter: LibraryFilter = .all
    @Published var searchQuery = ""
    @Published var sortOrder: LibrarySortOrder = .recentActivity
    @Published var errorMessage: String?
    private var store: LibraryStore?
    private let root: URL?

    var folders: [LibraryFolder] { state.folders.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending } }
    var title: String {
        if case .folder(let id) = filter { return folders.first { $0.id == id }?.name ?? "文件夹" }
        return filter.title
    }

    init(root: URL? = nil) { self.root = root; reload() }

    func reload() {
        do {
            let store = try LibraryStore(root: root)
            let loaded = try store.load()
            self.store = store; state = loaded; isAvailable = true; errorMessage = nil
            if case .folder(let id) = filter, !loaded.folders.contains(where: { $0.id == id }) { filter = .uncategorized }
        } catch {
            store = nil; isAvailable = false
            errorMessage = "无法读取文档库分类：\(error.localizedDescription)"
        }
    }

    func visibleDocuments(_ documents: [DocumentRecord]) -> [DocumentRecord] {
        state.filteredDocuments(documents, filter: filter, query: searchQuery, sort: sortOrder)
    }
    func count(_ filter: LibraryFilter, documents: [DocumentRecord]) -> Int {
        state.filteredDocuments(documents, filter: filter).count
    }
    func isTrashed(id: UUID) -> Bool { state.metadata(for: id).trashedAt != nil }
    func isFavorite(id: UUID) -> Bool { state.metadata(for: id).isFavorite }

    @discardableResult private func change(_ operation: (LibraryStore) throws -> LibraryState) -> Bool {
        guard let store, isAvailable else {
            errorMessage = "文档库分类尚未加载，请先重试读取。"; return false
        }
        do { state = try operation(store); return true }
        catch { errorMessage = "无法保存文档库分类：\(error.localizedDescription)"; return false }
    }

    @discardableResult func createFolder(name: String) -> Bool {
        let id = UUID()
        guard change({ try $0.createFolder(id: id, name: name) }) else { return false }
        filter = .folder(id); return true
    }
    @discardableResult func renameFolder(id: UUID, name: String) -> Bool { change { try $0.renameFolder(id: id, name: name) } }
    @discardableResult func deleteFolder(id: UUID) -> Bool {
        guard change({ try $0.deleteFolder(id: id) }) else { return false }
        if filter == .folder(id) { filter = .uncategorized }; return true
    }
    @discardableResult func moveDocument(id: UUID, to folderID: UUID?) -> Bool { change { try $0.moveDocuments(ids: [id], to: folderID) } }
    @discardableResult func toggleFavorite(id: UUID) -> Bool { change { try $0.setFavorite(id: id, value: !state.metadata(for: id).isFavorite) } }
    @discardableResult func trashDocument(_ document: DocumentRecord) -> Bool { change { try $0.trashDocuments([document]) } }
    @discardableResult func restoreDocument(id: UUID) -> Bool { change { try $0.restoreDocuments(ids: [id]) } }
    func markOpened(id: UUID) { _ = change { try $0.markOpened(id: id) } }

    func organizeImported(ids: [UUID]) {
        guard !ids.isEmpty else { return }
        let destination: UUID?
        if case .folder(let id) = filter { destination = id } else { destination = nil }
        guard change({ try $0.moveDocuments(ids: ids, to: destination) }) else { return }
        // An import should remain visible when initiated from favorites/recent/trash.
        if destination == nil && filter != .all && filter != .uncategorized { filter = .uncategorized }
        searchQuery = ""
    }
}
