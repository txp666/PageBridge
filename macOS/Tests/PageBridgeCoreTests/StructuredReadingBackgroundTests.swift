import AppKit
import XCTest
@testable import PageBridgeCore

final class StructuredReadingBackgroundTests: XCTestCase {
    private func png() throws -> Data {
        let bitmap = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 8, pixelsHigh: 6,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        for y in 0..<6 { for x in 0..<8 { bitmap.setColor(NSColor(srgbRed: 0, green: 0, blue: 1, alpha: 1), atX: x, y: y) } }
        return try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
    }

    private func artifact() throws -> (DocumentRecord, StructuredReadingDocument) {
        let document = DocumentRecord(name: "sample.pdf", pageCount: 1, inputPath: "/tmp/sample.pdf",
                                      inputSHA256: String(repeating: "a", count: 64))
        let block = StructuredReadingBlock(id: "p1-b1", kind: "heading", sourceText: "Heading",
            translatedText: "标题", status: "translated", bbox: [10, 10, 100, 30])
        let page = StructuredReadingPage(pageNumber: 1, width: 200, height: 300, blocks: [block],
            decorationsCoordinateSpace: "page", backgroundImage: try png().base64EncodedString(), backgroundBlocks: [block.id])
        return (document, StructuredReadingDocument(schemaVersion: 1, artifactType: "structured-reading",
            sourceSHA256: document.inputSHA256, pageCount: 1, sourceLanguage: "en", targetLanguage: "zh",
            status: "completed", selectedPages: [1], pages: [page]))
    }

    func testEmbeddedBackgroundSurvivesEncodingAndKeepsTranslatedBlockIdentity() throws {
        let (document, reading) = try artifact()
        let decoded = try JSONDecoder().decode(StructuredReadingDocument.self, from: JSONEncoder().encode(reading))
        XCTAssertNoThrow(try decoded.validate(for: document))
        let encoded = try XCTUnwrap(decoded.pages[0].backgroundImage)
        let image = try XCTUnwrap(StructuredReadingBackground.image(encoded))
        XCTAssertEqual(image.size, CGSize(width: 8, height: 6))
        XCTAssertEqual(decoded.pages[0].backgroundBlocks, ["p1-b1"])
    }

    func testBackgroundNeverLoadsPathsURLsOrOversizedImageHeaders() throws {
        XCTAssertNil(StructuredReadingBackground.image("/tmp/background.png"))
        XCTAssertNil(StructuredReadingBackground.image("https://example.com/background.png"))
        XCTAssertNil(StructuredReadingBackground.image(Data([255, 216, 255, 224]).base64EncodedString()))
        var oversized = try png()
        oversized.replaceSubrange(16..<20, with: [0, 1, 0, 0])
        XCTAssertNil(StructuredReadingBackground.pngData(oversized.base64EncodedString()))
    }

    func testBackgroundRequiresKnownTranslatedBlocksAndValidCoordinateSpace() throws {
        let (document, valid) = try artifact()
        var reading = valid
        reading.pages[0].backgroundBlocks = ["p1-missing"]
        XCTAssertThrowsError(try reading.validate(for: document))
        reading = valid; reading.pages[0].backgroundBlocks = ["p1-b1", "p1-b1"]
        XCTAssertThrowsError(try reading.validate(for: document))
        reading = valid; reading.pages[0].backgroundImage = nil
        XCTAssertThrowsError(try reading.validate(for: document))
        reading = valid; reading.pages[0].decorationsCoordinateSpace = "device"
        XCTAssertThrowsError(try reading.validate(for: document))
        reading = valid; reading.pages[0].blocks[0].status = "source_fallback"
        XCTAssertThrowsError(try reading.validate(for: document))
    }

    func testNativeForegroundColorOverridesContrastGuess() throws {
        let layout = StructuredReadingTextLayout.fit(text: "Title", in: CGSize(width: 100, height: 30),
            preferredFontSize: 12, heading: true, backgroundColor: [255, 255, 255], foregroundColor: [255, 0, 0])
        let color = try XCTUnwrap((layout.attributedText.attribute(.foregroundColor, at: 0,
            effectiveRange: nil) as? NSColor)?.usingColorSpace(.sRGB))
        XCTAssertGreaterThan(color.redComponent, 0.95)
        XCTAssertLessThan(color.greenComponent, 0.05)
        XCTAssertLessThan(color.blueComponent, 0.05)
    }
}
