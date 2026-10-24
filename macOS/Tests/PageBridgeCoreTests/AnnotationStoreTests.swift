import XCTest
import AppKit
import PDFKit
import CoreText
@testable import PageBridgeCore

final class AnnotationStoreTests: XCTestCase {
    private var root: URL!
    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("pagebridge-annotations-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }
    private func pdf(_ name: String, text: String = "First selected line", rotation: Int = 0) throws -> URL {
        let url = root.appendingPathComponent(name)
        var box = CGRect(x: 0, y: 0, width: 320, height: 320)
        let context = try XCTUnwrap(CGContext(url as CFURL, mediaBox: &box, nil))
        context.beginPDFPage(nil)
        for (line, y) in [(text, 250.0), ("Second selected line", 215.0)] {
            let string = NSAttributedString(string: line, attributes: [.font: NSFont.systemFont(ofSize: 14), .foregroundColor: NSColor.black])
            context.textPosition = CGPoint(x: 45, y: y)
            CTLineDraw(CTLineCreateWithAttributedString(string), context)
        }
        context.endPDFPage(); context.closePDF()
        let document = try XCTUnwrap(PDFDocument(url: url)), page = try XCTUnwrap(document.page(at: 0))
        page.rotation = rotation
        page.setBounds(CGRect(x: 20, y: 10, width: 280, height: 290), for: .cropBox)
        XCTAssertTrue(document.write(to: url))
        return url
    }
    private func binding(_ url: URL, variant: PDFDocumentVariant = .original, id: UUID = UUID()) throws -> AnnotationBinding {
        AnnotationBinding(documentID: id, variant: variant, sourceSHA256: try DocumentRepository.sha256(url))
    }
    func testSidecarRoundTripEditDeleteAndOriginalBytesUnchanged() throws {
        let source = try pdf("source.pdf"), key = try binding(source)
        let original = try Data(contentsOf: source), store = try AnnotationStore(root: root.appendingPathComponent("library"))
        var note = ReadingAnnotation(kind: .note, page: 1, selectedText: "selected", note: "first note")
        try store.save([note], for: key, sourceURL: source)
        let reopened = try AnnotationStore(root: store.root)
        XCTAssertEqual(try reopened.load(for: key), [note])
        note.note = "edited\nsecond line"; note.modifiedAt = Date()
        try reopened.save([note], for: key, sourceURL: source)
        XCTAssertEqual(try store.load(for: key).first?.note, note.note)
        try store.save([], for: key, sourceURL: source)
        XCTAssertEqual(try reopened.load(for: key), [])
        XCTAssertEqual(try Data(contentsOf: source), original)
    }
    func testOriginalTranslatedAndNewRevisionAreIsolatedEvenWithSameBytes() throws {
        let source = try pdf("source.pdf"), updated = try pdf("new-translation.pdf", text: "A different translation")
        let id = UUID(), original = try binding(source, id: id), translated = try binding(source, variant: .translated, id: id)
        let newTranslation = try binding(updated, variant: .translated, id: id)
        let store = try AnnotationStore(root: root.appendingPathComponent("library"))
        let note = ReadingAnnotation(kind: .note, page: 1, note: "original only")
        try store.save([note], for: original, sourceURL: source)
        XCTAssertEqual(try store.load(for: translated), [])
        try store.save([ReadingAnnotation(kind: .bookmark, page: 1)], for: translated, sourceURL: source)
        XCTAssertEqual(try store.load(for: newTranslation), [])
        XCTAssertEqual(try store.load(for: original), [note])
        XCTAssertEqual(try store.load(for: translated).count, 1)
        XCTAssertThrowsError(try store.save([note], for: translated, sourceURL: updated))
        XCTAssertThrowsError(try store.exportAnnotatedPDF(source: updated, binding: translated, destination: root.appendingPathComponent("bad.pdf")))
    }
    func testMultiLinePageSpaceSelectionSurvivesAllPageRotationsAndPDFExport() throws {
        for rotation in [0, 90, 180, 270] {
            let source = try pdf("rotate\(rotation).pdf", rotation: rotation), key = try binding(source)
            let document = try XCTUnwrap(PDFDocument(url: source)), page = try XCTUnwrap(document.page(at: 0))
            let selection = try XCTUnwrap(page.selection(for: NSRange(location: 0, length: page.numberOfCharacters)))
            let regions = PDFAnnotationRenderer.regions(for: selection, in: document)
            XCTAssertEqual(regions.count, 2, "Expected separate line annotations rather than the paragraph bounding box")
            XCTAssertTrue(regions.allSatisfy { $0.page == 1 && $0.rect.x > 40 && $0.rect.x < 50 && $0.rect.height < 25 })
            XCTAssertGreaterThan(abs(regions[0].rect.y - regions[1].rect.y), 30)
            let store = try AnnotationStore(root: root.appendingPathComponent("library-\(rotation)"))
            let highlight = ReadingAnnotation(kind: .highlight, page: 1, regions: regions, selectedText: selection.string ?? "")
            let underline = ReadingAnnotation(kind: .underline, page: 1, regions: [regions[0]])
            let note = ReadingAnnotation(kind: .note, page: 1, note: "A standard PDF note")
            let before = try Data(contentsOf: source)
            try store.save([highlight, underline, note, ReadingAnnotation(kind: .bookmark, page: 1)], for: key, sourceURL: source)
            let destination = root.appendingPathComponent("annotated\(rotation).pdf")
            try store.exportAnnotatedPDF(source: source, binding: key, destination: destination)
            let exported = try XCTUnwrap(PDFDocument(url: destination)), result = try XCTUnwrap(exported.page(at: 0))
            XCTAssertEqual(result.rotation, rotation)
            XCTAssertEqual(result.annotations.filter { $0.type == "Highlight" }.count, 2)
            XCTAssertEqual(result.annotations.filter { $0.type == "Underline" }.count, 1)
            XCTAssertEqual(result.annotations.filter { $0.type == "Text" }.first?.contents, note.note)
            for (annotation, region) in zip(result.annotations.filter { $0.type == "Highlight" }, regions) {
                XCTAssertEqual(annotation.bounds.minX, region.rect.x, accuracy: 0.01)
                XCTAssertEqual(annotation.bounds.minY, region.rect.y, accuracy: 0.01)
                XCTAssertEqual(annotation.bounds.width, region.rect.width, accuracy: 0.01)
                let points = try XCTUnwrap(annotation.quadrilateralPoints).map(\.pointValue)
                XCTAssertEqual(points.count, 4)
                XCTAssertEqual(points[0].x, 0, accuracy: 0.01)
                XCTAssertEqual(points[0].y, annotation.bounds.height, accuracy: 0.01)
                XCTAssertEqual(points[3].x, annotation.bounds.width, accuracy: 0.01)
                XCTAssertEqual(points[3].y, 0, accuracy: 0.01)
            }
            XCTAssertEqual(try Data(contentsOf: source), before)
        }
    }
    func testSourceAnnotationsSurviveOverlayRefreshAndReimport() throws {
        let source = try pdf("source.pdf")
        let document = try XCTUnwrap(PDFDocument(url: source)), page = try XCTUnwrap(document.page(at: 0))
        let original = PDFAnnotation(bounds: CGRect(x: 10, y: 10, width: 20, height: 20), forType: .text, withProperties: nil)
        original.contents = "existing annotation"; original.setValue("PageBridge.annotation.preexisting", forAnnotationKey: PDFAnnotationKey(rawValue: "NM"))
        page.addAnnotation(original)
        let items = [ReadingAnnotation(kind: .highlight, page: 1, regions: [AnnotationRegion(page: 1, rect: CGRect(x: 45, y: 250, width: 100, height: 15))])]
        try PDFAnnotationRenderer.apply(items, to: document)
        try PDFAnnotationRenderer.apply(items, to: document)
        XCTAssertEqual(page.annotations.filter { $0.type != "Popup" }.count, 2)
        XCTAssertEqual(page.annotations.filter { $0.type == "Highlight" }.count, 1)
        try PDFAnnotationRenderer.apply([], to: document)
        XCTAssertEqual(page.annotations.filter { $0.type != "Popup" }.count, 1)
        XCTAssertTrue(page.annotations[0] === original)
        let notes = [ReadingAnnotation(kind: .note, page: 1, note: "overlay note")]
        try PDFAnnotationRenderer.apply(notes, to: document)
        try PDFAnnotationRenderer.apply(notes, to: document)
        XCTAssertEqual(page.annotations.filter { $0.type == "Text" }.count, 2)
        try PDFAnnotationRenderer.apply([], to: document)
        XCTAssertEqual(page.annotations.filter { $0.type == "Text" }.count, 1)
        XCTAssertLessThanOrEqual(page.annotations.filter { $0.type == "Popup" }.count, 1)
        try PDFAnnotationRenderer.apply(items, to: document)
        let data = try XCTUnwrap(document.dataRepresentation()), reimported = try XCTUnwrap(PDFDocument(data: data))
        try PDFAnnotationRenderer.apply([], to: reimported)
        XCTAssertEqual(reimported.page(at: 0)?.annotations.filter { $0.type != "Popup" }.count, 2, "Imported PDF's previous annotations belong to the source")
    }
    func testExportsCannotOverwriteManagedOrProtectedFilesAndMarkdownContainsNotes() throws {
        let source = try pdf("source.pdf"), other = try pdf("translated.pdf")
        let key = try binding(source), store = try AnnotationStore(root: root.appendingPathComponent("library"))
        let note = ReadingAnnotation(kind: .note, page: 1, selectedText: "a quote\nnext line", note: "my note")
        try store.save([note], for: key, sourceURL: source)
        let before = try Data(contentsOf: source), otherBefore = try Data(contentsOf: other)
        XCTAssertThrowsError(try store.exportAnnotatedPDF(source: source, binding: key, destination: source))
        XCTAssertThrowsError(try store.exportAnnotatedPDF(source: source, binding: key, destination: other, protectedURLs: [other]))
        XCTAssertThrowsError(try store.exportNotes(title: "Notes", source: source, binding: key, destination: other, protectedURLs: [other]))
        XCTAssertThrowsError(try store.exportAnnotatedPDF(source: source, binding: key, destination: store.root.appendingPathComponent("output.pdf")))
        let symlink = root.appendingPathComponent("input-alias.pdf")
        try FileManager.default.createSymbolicLink(at: symlink, withDestinationURL: source)
        XCTAssertThrowsError(try store.exportAnnotatedPDF(source: source, binding: key, destination: symlink))
        let markdown = root.appendingPathComponent("notes.md")
        try store.exportNotes(title: "Paper", source: source, binding: key, destination: markdown)
        let text = try String(contentsOf: markdown, encoding: .utf8)
        XCTAssertTrue(text.contains("> a quote\n> next line")); XCTAssertTrue(text.contains("my note")); XCTAssertTrue(text.contains(key.sourceSHA256))
        XCTAssertEqual(try Data(contentsOf: source), before); XCTAssertEqual(try Data(contentsOf: other), otherBefore)
    }
    func testLiveOverlayIdentityDoesNotTrustNamesAndExpiresOnRefresh() throws {
        let source = try pdf("source.pdf")
        let document = try XCTUnwrap(PDFDocument(url: source)), page = try XCTUnwrap(document.page(at: 0))
        let selection = try XCTUnwrap(page.selection(for: NSRange(location: 0, length: page.numberOfCharacters)))
        let highlight = ReadingAnnotation(kind: .highlight, page: 1, regions: PDFAnnotationRenderer.regions(for: selection, in: document), selectedText: "both lines")
        try PDFAnnotationRenderer.apply([highlight], to: document)
        let firstGeneration = page.annotations.filter { $0.type == "Highlight" }
        XCTAssertEqual(firstGeneration.count, 2)
        for annotation in firstGeneration { XCTAssertEqual(PDFAnnotationRenderer.overlayID(for: annotation, in: document), highlight.id) }

        let clone = PDFAnnotation(bounds: firstGeneration[0].bounds, forType: .highlight, withProperties: nil)
        clone.setValue(firstGeneration[0].value(forAnnotationKey: PDFAnnotationKey(rawValue: "NM"))!, forAnnotationKey: PDFAnnotationKey(rawValue: "NM"))
        page.addAnnotation(clone)
        XCTAssertNil(PDFAnnotationRenderer.overlayID(for: clone, in: document), "A matching serialized name is not live ownership")
        let link = PDFAnnotation(bounds: CGRect(x: 40, y: 40, width: 100, height: 20), forType: .link, withProperties: nil)
        link.action = PDFActionURL(url: URL(string: "https://example.org/paper")!)
        page.addAnnotation(link)
        XCTAssertNil(PDFAnnotationRenderer.overlayID(for: link, in: document))
        let anotherDocument = try XCTUnwrap(PDFDocument(url: source))
        XCTAssertNil(PDFAnnotationRenderer.overlayID(for: firstGeneration[0], in: anotherDocument))

        try PDFAnnotationRenderer.apply([highlight], to: document)
        for annotation in firstGeneration { XCTAssertNil(PDFAnnotationRenderer.overlayID(for: annotation, in: document)) }
        XCTAssertTrue(page.annotations.contains { $0 === clone }, "Refreshing must preserve unrelated annotations even if names match")
        XCTAssertTrue(page.annotations.contains { $0 === link })
        let current = page.annotations.filter { PDFAnnotationRenderer.overlayID(for: $0, in: document) == highlight.id }
        XCTAssertEqual(current.count, 2)
        try PDFAnnotationRenderer.apply([], to: document)
        for annotation in current { XCTAssertNil(PDFAnnotationRenderer.overlayID(for: annotation, in: document)) }
    }

    func testImportedExportDoesNotBecomeAnEditableLiveOverlay() throws {
        let source = try pdf("source.pdf")
        let document = try XCTUnwrap(PDFDocument(url: source)), page = try XCTUnwrap(document.page(at: 0))
        let note = ReadingAnnotation(kind: .note, page: 1, note: "源文件批注")
        try PDFAnnotationRenderer.apply([note], to: document)
        let current = try XCTUnwrap(page.annotations.first { $0.type == "Text" })
        XCTAssertEqual(PDFAnnotationRenderer.overlayID(for: current, in: document), note.id)
        let reimported = try XCTUnwrap(PDFDocument(data: XCTUnwrap(document.dataRepresentation())))
        let importedPage = try XCTUnwrap(reimported.page(at: 0))
        let imported = try XCTUnwrap(importedPage.annotations.first { $0.type == "Text" })
        XCTAssertNil(PDFAnnotationRenderer.overlayID(for: imported, in: reimported))
        try PDFAnnotationRenderer.apply([ReadingAnnotation(kind: .note, page: 1, note: "本次新增")], to: reimported)
        XCTAssertNil(PDFAnnotationRenderer.overlayID(for: imported, in: reimported))
        try PDFAnnotationRenderer.apply([], to: reimported)
        XCTAssertTrue(importedPage.annotations.contains { $0 === imported })
        XCTAssertEqual(imported.contents, note.note)
    }

    func testAssociatedPopupUsesItsLiveParentIdentityOnly() throws {
        let source = try pdf("source.pdf")
        let document = try XCTUnwrap(PDFDocument(url: source)), page = try XCTUnwrap(document.page(at: 0))
        let note = ReadingAnnotation(kind: .note, page: 1, note: "便笺内容")
        try PDFAnnotationRenderer.apply([note], to: document)
        let annotation = try XCTUnwrap(page.annotations.first { $0.type == "Text" })
        let popup = annotation.popup ?? PDFAnnotation(bounds: CGRect(x: 40, y: 40, width: 120, height: 80), forType: .popup, withProperties: nil)
        annotation.popup = popup
        if !page.annotations.contains(where: { $0 === popup }) { page.addAnnotation(popup) }
        XCTAssertEqual(PDFAnnotationRenderer.overlayID(for: popup, in: document), note.id)
        try PDFAnnotationRenderer.apply([], to: document)
        XCTAssertNil(PDFAnnotationRenderer.overlayID(for: popup, in: document))
        XCTAssertFalse(page.annotations.contains { $0 === popup })
    }

    func testDisplayMarkupSuppressesNativeBubblesButExportPreservesContents() throws {
        let source = try pdf("source.pdf"), key = try binding(source)
        let document = try XCTUnwrap(PDFDocument(url: source)), page = try XCTUnwrap(document.page(at: 0))
        let region = AnnotationRegion(page: 1, rect: CGRect(x: 45, y: 250, width: 100, height: 15))
        let items = [
            ReadingAnnotation(kind: .highlight, page: 1, regions: [region], selectedText: "摘录原文"),
            ReadingAnnotation(kind: .underline, page: 1, regions: [region], selectedText: "underlined", note: "带注释的划线"),
            ReadingAnnotation(kind: .note, page: 1, note: "中文便笺")
        ]
        try PDFAnnotationRenderer.apply(items, to: document, forDisplay: true)
        for annotation in page.annotations where annotation.type == "Highlight" || annotation.type == "Underline" {
            XCTAssertNil(annotation.contents)
            XCTAssertNotNil(PDFAnnotationRenderer.overlayID(for: annotation, in: document))
        }
        XCTAssertEqual(page.annotations.first { $0.type == "Text" }?.contents, "中文便笺")
        let store = try AnnotationStore(root: root.appendingPathComponent("library"))
        try store.save(items, for: key, sourceURL: source)
        let destination = root.appendingPathComponent("annotated.pdf")
        try store.exportAnnotatedPDF(source: source, binding: key, destination: destination)
        let exported = try XCTUnwrap(PDFDocument(url: destination)), exportedPage = try XCTUnwrap(exported.page(at: 0))
        XCTAssertEqual(exportedPage.annotations.first { $0.type == "Highlight" }?.contents, "摘录原文")
        XCTAssertEqual(exportedPage.annotations.first { $0.type == "Underline" }?.contents, "带注释的划线")
        XCTAssertEqual(exportedPage.annotations.first { $0.type == "Text" }?.contents, "中文便笺")
    }

    func testInvalidGeometryAndWrongEnvelopeFailClosed() throws {
        let source = try pdf("source.pdf"), key = try binding(source), store = try AnnotationStore(root: root.appendingPathComponent("library"))
        XCTAssertThrowsError(try store.save([ReadingAnnotation(kind: .note, page: 2)], for: key, sourceURL: source))
        XCTAssertThrowsError(try store.save([ReadingAnnotation(kind: .highlight, page: 1)], for: key, sourceURL: source))
        XCTAssertThrowsError(try store.save([ReadingAnnotation(kind: .highlight, page: 1, regions: [AnnotationRegion(page: 1, rect: CGRect(x: 0, y: 0, width: 1, height: 0))])], for: key, sourceURL: source))
        try store.save([], for: key, sourceURL: source)
        let file = try store.fileURL(for: key)
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: Any])
        var b = try XCTUnwrap(json["binding"] as? [String: Any]); b["variant"] = "translated"; json["binding"] = b
        try JSONSerialization.data(withJSONObject: json).write(to: file)
        XCTAssertThrowsError(try store.load(for: key))
    }
}
