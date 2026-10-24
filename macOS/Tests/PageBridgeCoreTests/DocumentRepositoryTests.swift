import XCTest
import PDFKit
import CoreGraphics
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
    func testImportOwnsCopyAndPersistsRecordWithoutChangingSource() throws {
        let source = try pdf("original.pdf", pages: 2)
        let hash = try DocumentRepository.sha256(source)
        let repository = try DocumentRepository(root: root.appendingPathComponent("library"))
        let record = try repository.importPDF(source)
        XCTAssertNotEqual(record.originalURL, source)
        XCTAssertEqual(record.pageCount, 2)
        XCTAssertEqual(record.inputSHA256, hash)
        try repository.saveDocuments([record])
        XCTAssertEqual(try repository.loadDocuments(), [record])
        XCTAssertEqual(try DocumentRepository.sha256(source), hash)
    }
    func testCommitRequiresMatchingPagesAndContainedOutputs() throws {
        let repository = try DocumentRepository(root: root.appendingPathComponent("library"))
        let source = try pdf("source.pdf", pages: 1)
        let record = try repository.importPDF(source)
        let attempt = UUID(), staging = try repository.attemptDirectory(document: record, attempt: UUID())
        let outside = try pdf("outside.pdf", pages: 1)
        var event = WorkerEvent(protocolVersion: 1, jobId: record.id.uuidString, attemptId: attempt.uuidString, sequence: 1, type: "result", translatedPath: outside.path, bilingualPath: outside.path)
        XCTAssertThrowsError(try repository.commit(result: event, document: record, attempt: attempt, staging: staging, service: .defaultOllama, sourceLanguage: "en", targetLanguage: "zh", pages: nil))
        let mono = staging.appendingPathComponent("mono.pdf"), dual = staging.appendingPathComponent("dual.pdf")
        try FileManager.default.copyItem(at: outside, to: mono)
        try FileManager.default.copyItem(at: outside, to: dual)
        event.translatedPath = mono.path; event.bilingualPath = dual.path
        XCTAssertThrowsError(try repository.commit(result: event, document: record, attempt: attempt, staging: staging, service: .defaultOllama, sourceLanguage: "en", targetLanguage: "zh", pages: nil))
        try FileManager.default.removeItem(at: dual)
        let validDual = try pdf("valid-dual.pdf", pages: 2)
        try FileManager.default.copyItem(at: validDual, to: dual)
        let result = try repository.commit(result: event, document: record, attempt: attempt, staging: staging, service: .defaultOllama, sourceLanguage: "en", targetLanguage: "zh", pages: nil)
        XCTAssertEqual(result.status, .completed)
        XCTAssertEqual(PDFDocument(url: try XCTUnwrap(result.translatedURL))?.pageCount, 1)
        XCTAssertEqual(PDFDocument(url: try XCTUnwrap(result.bilingualURL))?.pageCount, 2)
        XCTAssertFalse(result.translatedPath!.hasPrefix(staging.path))
        XCTAssertEqual(try DocumentRepository.sha256(source), record.inputSHA256)
    }
    func testExportDoesNotOverwriteWorkingInputAndCopiesExactBytes() throws {
        let source = try pdf("source.pdf", pages: 1)
        let output = try pdf("translated.pdf", pages: 2)
        XCTAssertThrowsError(try DocumentRepository.export(source: output, destination: source, protectedInput: source))
        let export = root.appendingPathComponent("export.pdf")
        try DocumentRepository.export(source: output, destination: export, protectedInput: source)
        XCTAssertEqual(try DocumentRepository.sha256(export), try DocumentRepository.sha256(output))
        try DocumentRepository.export(source: source, destination: export, protectedInput: source)
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
}
