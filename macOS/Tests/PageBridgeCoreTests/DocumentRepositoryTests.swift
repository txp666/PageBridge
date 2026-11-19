import XCTest
import PDFKit
import CoreGraphics
import Darwin
@testable import PageBridgeCore

final class DocumentRepositoryTests: XCTestCase {
    private var root: URL!
    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("pagebridge-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }
    private func pdf(_ name: String, pages: Int) throws -> URL {
        let url = root.appendingPathComponent(name)
        var bounds = CGRect(x: 0, y: 0, width: 595, height: 842)
        let context = try XCTUnwrap(CGContext(url as CFURL, mediaBox: &bounds, nil))
        for _ in 0..<pages {
            context.beginPDFPage(nil); context.setFillColor(CGColor(gray: 0.5, alpha: 1))
            context.fill(CGRect(x: 30, y: 30, width: 50, height: 50)); context.endPDFPage()
        }
        context.closePDF(); return url
    }
    func testExportDoesNotOverwriteWorkingInputAndCopiesExactBytes() throws {
        let source = try pdf("source.pdf", pages: 1)
        let output = try pdf("translated.pdf", pages: 2), library = root.appendingPathComponent("library")
        XCTAssertThrowsError(try DocumentRepository.export(source: output, destination: source, protectedInput: source, managedRoot: library))
        let export = root.appendingPathComponent("export.pdf")
        try DocumentRepository.export(source: output, destination: export, protectedInput: source, managedRoot: library)
        XCTAssertEqual(try DocumentRepository.sha256(export), try DocumentRepository.sha256(output))
        try DocumentRepository.export(source: source, destination: export, protectedInput: source, managedRoot: library)
        XCTAssertEqual(PDFDocument(url: export)?.pageCount, 1)
    }
    func testExportCannotReplaceAnotherManagedResult() throws {
        let source = try pdf("translated.pdf", pages: 1)
        let bilingual = try pdf("bilingual.pdf", pages: 2)
        let input = try pdf("input.pdf", pages: 1)
        let before = try DocumentRepository.sha256(bilingual)
        XCTAssertThrowsError(try DocumentRepository.export(source: source, destination: bilingual, protectedInput: input, managedRoot: root))
        XCTAssertEqual(try DocumentRepository.sha256(bilingual), before)
    }

    func testCacheCleanupProtectsCurrentAndPendingReadingDrafts() throws {
        let library = root.appendingPathComponent("library")
        let repository = try DocumentRepository(root: library)
        var document = try repository.importPDF(pdf("source.pdf", pages: 1))
        let folder = document.originalURL.deletingLastPathComponent()
        func fixture(in parent: URL, _ name: String) throws -> URL {
            try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
            let file = parent.appendingPathComponent(name)
            try Data("test".utf8).write(to: file)
            return file
        }
        let results = folder.appendingPathComponent("results")
        let savedAttempt = UUID()
        let current = try fixture(in: results.appendingPathComponent(savedAttempt.uuidString), "reading.json")
        let savedImage = try fixture(in: current.deletingLastPathComponent(), "background-1.png")
        let redundant = try fixture(in: repository.attemptDirectory(document: document, attempt: savedAttempt)
            .appendingPathComponent("result-saved"), "reading.json")
        _ = try fixture(in: redundant.deletingLastPathComponent(), "background-1.png")
        let history = try fixture(in: results.appendingPathComponent(UUID().uuidString), "reading.json")
        let abandoned = try fixture(in: repository.attemptDirectory(document: document, attempt: UUID()), "temporary.json")
        let recoverable = try fixture(in: repository.attemptDirectory(document: document, attempt: UUID())
            .appendingPathComponent("result-saved"), "reading.json")
        let runtime = root.appendingPathComponent("shared/mineru/4.0.12")
        let model = try fixture(in: runtime.appendingPathComponent("home/models"), "layout.onnx")
        let download = try fixture(in: runtime.appendingPathComponent("uv-cache"), "package.whl")
        document.readingPath = current.path
        try repository.saveDocuments([document])

        let storage = CacheStorage(root: library, minerURuntime: runtime)
        let usage = try storage.usage()
        XCTAssertEqual(usage[.currentReadings]?.locations.count, 1)
        XCTAssertEqual(usage[.readingHistory]?.locations.count, 1)
        XCTAssertEqual(usage[.pendingReadings]?.locations.count, 1)
        XCTAssertGreaterThan(usage[.minerU]?.bytes ?? 0, 0)
        XCTAssertGreaterThan(usage[.minerUDownloads]?.bytes ?? 0, 0)
        XCTAssertThrowsError(try storage.clear(.currentReadings))

        document.status = .running
        try repository.saveDocuments([document])
        XCTAssertThrowsError(try storage.clear(.taskFiles))
        document.status = .interrupted
        try repository.saveDocuments([document])
        XCTAssertEqual(try storage.clear(.taskFiles).removed, 2)
        do {
            let lock = open(runtime.appendingPathComponent("install.lock").path, O_WRONLY | O_CREAT, S_IRUSR | S_IWUSR)
            XCTAssertGreaterThanOrEqual(lock, 0)
            defer { _ = close(lock) }
            XCTAssertEqual(flock(lock, LOCK_EX | LOCK_NB), 0)
            XCTAssertEqual(try storage.clear(.minerUDownloads).failed, 1)
            XCTAssertTrue(FileManager.default.fileExists(atPath: download.path))
        }
        XCTAssertEqual(try storage.clear(.minerUDownloads).removed, 1)
        for file in [current, history, recoverable, model, savedImage] {
            XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))
        }
        for file in [abandoned, download, redundant] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: file.path))
        }
    }
}
