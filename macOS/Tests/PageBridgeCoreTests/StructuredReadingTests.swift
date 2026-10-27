import XCTest
import CoreGraphics
@testable import PageBridgeCore

final class StructuredReadingTests: XCTestCase {
    private var directory: URL!
    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("pagebridge-reading-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: directory) }

    private func fixture() throws -> (DocumentRepository, DocumentRecord) {
        let source = directory.appendingPathComponent("source.pdf")
        var bounds = CGRect(x: 0, y: 0, width: 600, height: 800)
        let context = try XCTUnwrap(CGContext(source as CFURL, mediaBox: &bounds, nil))
        for _ in 0..<2 { context.beginPDFPage(nil); context.endPDFPage() }
        context.closePDF()
        let repository = try DocumentRepository(root: directory.appendingPathComponent("library"))
        return (repository, try repository.importPDF(source))
    }
    private func artifact(_ record: DocumentRecord, partial: Bool = true) -> StructuredReadingDocument {
        let block = StructuredReadingBlock(id: "p2-b1", kind: "text", sourceText: "The original paragraph.",
            translatedText: partial ? nil : "原文段落。", status: partial ? "source_fallback" : "translated", bbox: [10, 20, 100, 80])
        return StructuredReadingDocument(schemaVersion: 1, artifactType: "structured-reading", sourceSHA256: record.inputSHA256,
            pageCount: 2, sourceLanguage: "en", targetLanguage: "zh", status: partial ? "partial" : "completed",
            selectedPages: [2], pages: [StructuredReadingPage(pageNumber: 2, width: 600, height: 800, blocks: [block])])
    }
    private func staged(_ reading: StructuredReadingDocument, record: DocumentRecord, repository: DocumentRepository, attempt: UUID) throws -> (URL, WorkerEvent) {
        let staging = try repository.attemptDirectory(document: record, attempt: attempt)
        let file = staging.appendingPathComponent("reading.json")
        try JSONEncoder().encode(reading).write(to: file)
        var event = WorkerEvent(protocolVersion: 1, jobId: record.id.uuidString, attemptId: attempt.uuidString, sequence: 5, type: "result")
        event.artifactType = "structured-reading"; event.readingPath = file.path
        event.readingSHA256 = try DocumentRepository.sha256(file)
        return (staging, event)
    }

    func testPartialReadingCommitsAndSurvivesRestartWithoutReplacingPDFResults() throws {
        let (repository, imported) = try fixture()
        var record = imported; record.translatedPath = "/previous/translated.pdf"; record.bilingualPath = "/previous/bilingual.pdf"
        let attempt = UUID(), reading = artifact(record)
        let (staging, event) = try staged(reading, record: record, repository: repository, attempt: attempt)
        let updated = try repository.commitReading(result: event, document: record, attempt: attempt, staging: staging)
        XCTAssertEqual(updated.status, .partial)
        XCTAssertEqual(updated.translatedPath, record.translatedPath)
        XCTAssertEqual(updated.bilingualPath, record.bilingualPath)
        XCTAssertEqual(updated.preferredOutputMode, "reading")
        try repository.saveDocuments([updated])
        try FileManager.default.removeItem(at: staging)
        let restarted = try DocumentRepository(root: repository.root).loadDocuments().first!
        let reloaded = try StructuredReadingDocument.load(try XCTUnwrap(restarted.readingURL), for: restarted)
        XCTAssertEqual(reloaded.page(2)?.blocks.first?.displayText, "The original paragraph.")
        XCTAssertNil(reloaded.page(1))
        XCTAssertEqual(try DocumentRepository.sha256(record.originalURL), record.inputSHA256)
    }

    func testReadingRejectsWrongSourceAndChangedOriginal() throws {
        let (_, record) = try fixture()
        var reading = artifact(record); reading.sourceSHA256 = String(repeating: "0", count: 64)
        XCTAssertThrowsError(try reading.validate(for: record))
        reading = artifact(record)
        let file = directory.appendingPathComponent("reading.json")
        try JSONEncoder().encode(reading).write(to: file)
        try Data("changed".utf8).write(to: record.originalURL)
        XCTAssertThrowsError(try StructuredReadingDocument.load(file, for: record))
    }

    func testCommitRejectsEscapeDigestAndWrongAttempt() throws {
        let (repository, record) = try fixture()
        let attempt = UUID(), reading = artifact(record, partial: false)
        let (staging, event) = try staged(reading, record: record, repository: repository, attempt: attempt)
        var invalid = event; invalid.readingSHA256 = String(repeating: "0", count: 64)
        XCTAssertThrowsError(try repository.commitReading(result: invalid, document: record, attempt: attempt, staging: staging))
        invalid = event; invalid.attemptId = UUID().uuidString
        XCTAssertThrowsError(try repository.commitReading(result: invalid, document: record, attempt: attempt, staging: staging))
        let outside = directory.appendingPathComponent("outside.json")
        try JSONEncoder().encode(reading).write(to: outside)
        invalid = event; invalid.readingPath = outside.path
        XCTAssertThrowsError(try repository.commitReading(result: invalid, document: record, attempt: attempt, staging: staging))
    }

    func testCommitRejectsSymlinkOutputWithoutChangingPreviousRecord() throws {
        let (repository, record) = try fixture()
        let attempt = UUID(), reading = artifact(record)
        let (staging, event) = try staged(reading, record: record, repository: repository, attempt: attempt)
        let path = try XCTUnwrap(event.readingPath), outside = directory.appendingPathComponent("outside.json")
        try FileManager.default.moveItem(at: URL(fileURLWithPath: path), to: outside)
        try FileManager.default.createSymbolicLink(atPath: path, withDestinationPath: outside.path)
        XCTAssertThrowsError(try repository.commitReading(result: event, document: record, attempt: attempt, staging: staging))
        XCTAssertNil(record.readingPath)
    }

    func testPageFailureRemainsReadableAndPageOrBlockIdentityCannotDrift() throws {
        let (_, record) = try fixture()
        var reading = artifact(record)
        reading.pages[0].blocks = []; reading.pages[0].status = "source_fallback"
        XCTAssertNoThrow(try reading.validate(for: record))
        XCTAssertEqual(reading.failedPageCount, 1)
        reading.selectedPages = [1]
        XCTAssertThrowsError(try reading.validate(for: record))
        reading = artifact(record); reading.pages[0].blocks.append(reading.pages[0].blocks[0])
        XCTAssertThrowsError(try reading.validate(for: record))
    }

    func testLegacyDocumentsDecodeWithoutReadingFields() throws {
        let (_, record) = try fixture()
        let data = try JSONEncoder().encode(record)
        let decoded = try JSONDecoder().decode(DocumentRecord.self, from: data)
        XCTAssertNil(decoded.readingPath)
        XCTAssertNil(decoded.preferredOutputMode)
        XCTAssertFalse(decoded.hasTranslation)
    }

    func testCodeArtifactKeepsOriginalTokensAndSourceRegionAfterDecoding() throws {
        let (_, record) = try fixture()
        let source = "{\n  \"enabled\": false,\n  \"value\": null\n}"
        var reading = artifact(record, partial: false)
        reading.pages[0].blocks = [StructuredReadingBlock(id: "p2-code", kind: "code", sourceText: source,
            status: "preserved", bbox: [10, 20, 100, 80])]
        let decoded = try JSONDecoder().decode(StructuredReadingDocument.self, from: JSONEncoder().encode(reading))
        XCTAssertNoThrow(try decoded.validate(for: record))
        let code = try XCTUnwrap(decoded.page(2)?.blocks.first)
        XCTAssertTrue(code.isSourceRegion)
        XCTAssertEqual(code.displayText, source)
        XCTAssertNil(code.translatedText)
    }

    func testOptionalBackgroundColorAcceptsRGBAndKeepsLegacyArtifactsCompatible() throws {
        let (_, record) = try fixture()
        var reading = artifact(record, partial: false)
        let legacy = try JSONDecoder().decode(StructuredReadingDocument.self, from: JSONEncoder().encode(reading))
        XCTAssertNil(legacy.pages[0].blocks[0].backgroundColor)
        reading.pages[0].blocks[0].backgroundColor = [232, 241, 255]
        let decoded = try JSONDecoder().decode(StructuredReadingDocument.self, from: JSONEncoder().encode(reading))
        XCTAssertNoThrow(try decoded.validate(for: record))
        XCTAssertEqual(decoded.pages[0].blocks[0].backgroundColor, [232, 241, 255])
        reading.pages[0].blocks[0].backgroundColor = [255, 256, 255]
        XCTAssertThrowsError(try reading.validate(for: record))
        reading.pages[0].blocks[0].backgroundColor = [255, 255]
        XCTAssertThrowsError(try reading.validate(for: record))
    }

    func testCanonicalLibraryAliasCanCommitReading() throws {
        let (repository, record) = try fixture()
        let alias = directory.appendingPathComponent("library-alias")
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: repository.root)
        let aliasedRepository = try DocumentRepository(root: alias)
        var aliasedRecord = record
        aliasedRecord.inputPath = record.inputPath.replacingOccurrences(of: repository.root.path, with: alias.path)
        let attempt = UUID()
        let (staging, event) = try staged(artifact(record), record: aliasedRecord, repository: aliasedRepository, attempt: attempt)
        let result = try aliasedRepository.commitReading(result: event, document: aliasedRecord, attempt: attempt, staging: staging)
        XCTAssertNotNil(result.readingPath)
        XCTAssertEqual(result.status, .partial)
    }

    func testRetryRequestCarriesOnlyTargetBlockAndArtifactReference() throws {
        let request = WorkerRequest(action: "translate", inputPath: "/input.pdf", outputDirectory: "/attempt",
            outputMode: "reading", readingPath: "/previous/reading.json", retryBlockIds: ["p2-b1"])
        let decoded = try JSONDecoder().decode(WorkerRequest.self, from: JSONEncoder().encode(request))
        XCTAssertEqual(decoded.outputMode, "reading")
        XCTAssertEqual(decoded.readingPath, "/previous/reading.json")
        XCTAssertEqual(decoded.retryBlockIds, ["p2-b1"])
    }
}
