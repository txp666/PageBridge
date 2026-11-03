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
}
