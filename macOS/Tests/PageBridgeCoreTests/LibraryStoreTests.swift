import XCTest
@testable import PageBridgeCore

final class LibraryStoreTests: XCTestCase {
    private var root: URL!
    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("pagebridge-library-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }
    private func record(_ name: String = "Research.pdf", importedAt: Date = Date()) -> DocumentRecord {
        var result = DocumentRecord(name: name, pageCount: 3, inputPath: root.appendingPathComponent("input.pdf").path, inputSHA256: "unchanged")
        result.importedAt = importedAt; return result
    }

    func testOldDocumentsRemainUnclassifiedWithoutRewritingLegacyIndex() throws {
        let repository = try DocumentRepository(root: root)
        let original = [record("Older.pdf"), record("Newer.pdf")]
        try repository.saveDocuments(original)
        let index = root.appendingPathComponent("documents.json")
        let bytes = try Data(contentsOf: index)
        let store = try LibraryStore(root: root), state = try store.load()
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.fileURL.path))
        XCTAssertEqual(Set(state.filteredDocuments(original, filter: .uncategorized).map(\.id)), Set(original.map(\.id)))
        XCTAssertEqual(state.filteredDocuments(original, filter: .favorites), [])
        try store.createFolder(name: "Papers")
        try store.moveDocuments(ids: [original[0].id], to: try XCTUnwrap(store.load().folders.first?.id))
        XCTAssertEqual(try Data(contentsOf: index), bytes)
        XCTAssertEqual(try repository.loadDocuments(), original)
    }

    func testFolderRenameMoveDeletePersistAndReturnAllMembersToUnclassified() throws {
        let store = try LibraryStore(root: root), folder = UUID(), a = record(), b = record("Other.pdf")
        try store.createFolder(id: folder, name: "  Articles  ")
        try store.moveDocuments(ids: [a.id, b.id], to: folder)
        try store.setFavorite(id: a.id, value: true)
        try store.trashDocuments([b])
        try store.renameFolder(id: folder, name: "References")
        let reopened = try LibraryStore(root: root)
        XCTAssertEqual(try reopened.load().folders.first?.name, "References")
        XCTAssertEqual(try reopened.load().metadata(for: a.id).folderID, folder)
        try reopened.deleteFolder(id: folder)
        let final = try store.load()
        XCTAssertTrue(final.folders.isEmpty)
        XCTAssertNil(final.metadata(for: a.id).folderID)
        XCTAssertNil(final.metadata(for: b.id).folderID)
        XCTAssertTrue(final.metadata(for: a.id).isFavorite)
        XCTAssertNotNil(final.metadata(for: b.id).trashedAt)
        try store.restoreDocuments(ids: [b.id])
        XCTAssertEqual(Set(try store.load().filteredDocuments([a, b], filter: .uncategorized).map(\.id)), [a.id, b.id])
    }

    func testTrashRestoreRetainsFolderFavoriteReadingHistoryAndEveryUserFile() throws {
        let store = try LibraryStore(root: root), folder = UUID(), document = record()
        let files = [document.originalURL, root.appendingPathComponent("translated.pdf"), root.appendingPathComponent("bilingual.pdf")]
        for (index, url) in files.enumerated() { try Data("user-file-\(index)".utf8).write(to: url) }
        let before = try files.map { try Data(contentsOf: $0) }
        let date = Date(timeIntervalSince1970: 1_000)
        try store.createFolder(id: folder, name: "Work")
        try store.moveDocuments(ids: [document.id], to: folder)
        try store.setFavorite(id: document.id, value: true)
        try store.markOpened(id: document.id, at: date)
        let trashed = try store.trashDocuments([document], at: date.addingTimeInterval(1))
        XCTAssertTrue(trashed.filteredDocuments([document], filter: .all).isEmpty)
        XCTAssertTrue(trashed.filteredDocuments([document], filter: .favorites).isEmpty)
        XCTAssertEqual(trashed.filteredDocuments([document], filter: .trash), [document])
        let restored = try LibraryStore(root: root).restoreDocuments(ids: [document.id])
        XCTAssertNil(restored.metadata(for: document.id).trashedAt)
        XCTAssertEqual(restored.metadata(for: document.id).folderID, folder)
        XCTAssertTrue(restored.metadata(for: document.id).isFavorite)
        XCTAssertEqual(restored.metadata(for: document.id).lastOpenedAt, date)
        XCTAssertEqual(try files.map { try Data(contentsOf: $0) }, before)
    }

    func testActiveTranslationRejectsWholeTrashMutation() throws {
        let store = try LibraryStore(root: root)
        let idle = record("Idle.pdf")
        var busy = record("Busy.pdf")
        try store.setFavorite(id: idle.id, value: true)
        let original = try Data(contentsOf: store.fileURL)
        for status in [DocumentStatus.running, .cancelling] {
            busy.status = status
            XCTAssertThrowsError(try store.trashDocuments([idle, busy]))
            XCTAssertEqual(try Data(contentsOf: store.fileURL), original)
            XCTAssertNil(try store.load().metadata(for: idle.id).trashedAt)
        }
        busy.status = .completed
        XCTAssertEqual(try store.trashDocuments([busy]).filteredDocuments([busy], filter: .trash), [busy])
    }

    func testCorruptOrNewerMetadataIsNeverOverwrittenByMutation() throws {
        let store = try LibraryStore(root: root)
        for bytes in [Data("{broken".utf8), Data("{\"version\":2,\"future\":true}".utf8)] {
            try bytes.write(to: store.fileURL)
            XCTAssertThrowsError(try store.load())
            XCTAssertThrowsError(try store.createFolder(name: "Must not replace"))
            XCTAssertThrowsError(try store.setFavorite(id: UUID(), value: true))
            XCTAssertEqual(try Data(contentsOf: store.fileURL), bytes)
        }
    }

    func testMalformedFolderReferencesAndDuplicateIdentitiesAreRejected() throws {
        let store = try LibraryStore(root: root), id = UUID(), folder = LibraryFolder(name: "Folder")
        let invalid = [
            LibraryState(folders: [folder, folder]),
            LibraryState(documents: [id.uuidString: LibraryDocumentMetadata(folderID: UUID())]),
            LibraryState(documents: ["not-a-uuid": LibraryDocumentMetadata()])
        ]
        for value in invalid {
            let bytes = try JSONEncoder().encode(value); try bytes.write(to: store.fileURL)
            XCTAssertThrowsError(try store.load())
            XCTAssertThrowsError(try store.restoreDocuments(ids: [id]))
            XCTAssertEqual(try Data(contentsOf: store.fileURL), bytes)
        }
    }

    func testInvalidNamesDuplicateNamesAndMissingMovesDoNotChangeSavedState() throws {
        let store = try LibraryStore(root: root), id = UUID()
        try store.createFolder(id: id, name: "Science")
        let before = try Data(contentsOf: store.fileURL)
        for name in [" ", "bad\nname", String(repeating: "x", count: 101), "SCIENCE"] {
            XCTAssertThrowsError(try store.createFolder(name: name))
            XCTAssertEqual(try Data(contentsOf: store.fileURL), before)
        }
        XCTAssertThrowsError(try store.moveDocuments(ids: [UUID()], to: UUID()))
        XCTAssertThrowsError(try store.renameFolder(id: UUID(), name: "Other"))
        XCTAssertEqual(try Data(contentsOf: store.fileURL), before)
    }

    func testFreshReadsAvoidDroppingMetadataWrittenByAnotherStoreInstance() throws {
        let a = try LibraryStore(root: root), b = try LibraryStore(root: root), id = UUID(), folder = UUID()
        _ = try b.load()
        try a.createFolder(id: folder, name: "Research")
        try b.setFavorite(id: id, value: true)
        try a.moveDocuments(ids: [id], to: folder)
        let latest = try b.load()
        XCTAssertEqual(latest.folders.map(\.id), [folder])
        XCTAssertTrue(latest.metadata(for: id).isFavorite)
        XCTAssertEqual(latest.metadata(for: id).folderID, folder)
    }

    func testNameSearchAllFiltersRecentActivityAndStableSorts() throws {
        let now = Date(timeIntervalSince1970: 2_000_000_000)
        let old = record("Alpha 10.pdf", importedAt: now.addingTimeInterval(-50 * 86400))
        let recent = record("alpha 2.pdf", importedAt: now.addingTimeInterval(-86400))
        let trash = record("Discard.pdf", importedAt: now)
        let folder = UUID()
        let state = LibraryState(folders: [LibraryFolder(id: folder, name: "Physics")], documents: [
            old.id.uuidString: LibraryDocumentMetadata(folderID: folder, isFavorite: true, lastOpenedAt: now),
            trash.id.uuidString: LibraryDocumentMetadata(isFavorite: true, trashedAt: now)
        ])
        let records = [trash, old, recent]
        XCTAssertEqual(state.filteredDocuments(records, filter: .all, query: " ALPHA ", sort: .nameAscending, now: now).map(\.id), [recent.id, old.id])
        XCTAssertEqual(state.filteredDocuments(records, filter: .all, sort: .nameDescending, now: now).map(\.id), [old.id, recent.id])
        XCTAssertEqual(state.filteredDocuments(records, filter: .all, sort: .newest, now: now).map(\.id), [recent.id, old.id])
        XCTAssertEqual(state.filteredDocuments(records, filter: .all, sort: .oldest, now: now).map(\.id), [old.id, recent.id])
        XCTAssertEqual(state.filteredDocuments(records, filter: .recent, now: now).map(\.id), [old.id, recent.id])
        XCTAssertEqual(state.filteredDocuments(records, filter: .favorites, now: now), [old])
        XCTAssertEqual(state.filteredDocuments(records, filter: .folder(folder), now: now), [old])
        XCTAssertEqual(state.filteredDocuments(records, filter: .uncategorized, now: now), [recent])
        XCTAssertEqual(state.filteredDocuments(records, filter: .trash, now: now), [trash])
        XCTAssertTrue(state.filteredDocuments([old], filter: .recent, now: now.addingTimeInterval(31 * 86400)).isEmpty)
    }

    func testSparseMetadataDefaultsAndUnknownDocumentMetadataArePreserved() throws {
        let store = try LibraryStore(root: root), imported = UUID(), unknown = UUID()
        let data = Data("{\"version\":1,\"folders\":[],\"documents\":{\"\(unknown.uuidString)\":{}}}".utf8)
        try data.write(to: store.fileURL)
        XCTAssertFalse(try store.load().metadata(for: unknown).isFavorite)
        try store.setFavorite(id: imported, value: true)
        let reloaded = try store.load()
        XCTAssertNotNil(reloaded.documents[unknown.uuidString])
        XCTAssertTrue(reloaded.metadata(for: imported).isFavorite)
    }
}
