import AppKit
import XCTest
@testable import PageBridgeCore

final class StructuredReadingMathTests: XCTestCase {
    private func image(width: Int = 100, height: Int = 100) throws -> NSImage {
        let bitmap = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        let pixels = try XCTUnwrap(bitmap.bitmapData)
        for y in 0..<height { for x in 0..<width {
            let red = (10..<30).contains(x) && (20..<40).contains(y), offset = y * bitmap.bytesPerRow + x * 4
            pixels[offset] = red ? 255 : 0; pixels[offset + 1] = 0
            pixels[offset + 2] = red ? 0 : 255; pixels[offset + 3] = 255
        } }
        return NSImage(cgImage: try XCTUnwrap(bitmap.cgImage), size: CGSize(width: CGFloat(width), height: CGFloat(height)))
    }

    func testCropUsesOriginalTopLeftPageCoordinates() throws {
        let block = StructuredReadingBlock(id: "b1", kind: "text", sourceText: "x", status: "translated",
            inlineMath: [StructuredReadingInlineMath(token: "[[PBM0]]", latex: "x", bbox: [10, 20, 30, 40])])
        let crops = StructuredReadingMathImages.crops(for: block, image: try image(), pageSize: CGSize(width: 100, height: 100))
        let crop = try XCTUnwrap(crops["[[PBM0]]"])
        XCTAssertEqual(crop.size, CGSize(width: 21.5, height: 21.5))
        let bitmap = NSBitmapImageRep(cgImage: try XCTUnwrap(crop.image.cgImage(forProposedRect: nil, context: nil, hints: nil)))
        let center = try XCTUnwrap(bitmap.colorAt(x: 10, y: 10)?.usingColorSpace(.deviceRGB))
        XCTAssertGreaterThan(center.redComponent, 0.95)
        XCTAssertLessThan(center.blueComponent, 0.05)
    }

    func testInlineFormulaIsOneMeasuredAttachmentAndKeepsSurroundingText() throws {
        let image = StructuredReadingInlineImage(image: try image(width: 40, height: 20), size: CGSize(width: 20, height: 10))
        let layout = StructuredReadingTextLayout.fit(text: "方差 $x^2$ 的估计", in: CGSize(width: 300, height: 60),
            preferredFontSize: 12, heading: false, template: "方差 [[PBM0]] 的估计", inlineImages: ["[[PBM0]]": image])
        XCTAssertFalse(layout.overflows)
        XCTAssertEqual(layout.attributedText.string, "方差 \u{fffc} 的估计")
        var attachments: [NSTextAttachment] = []
        layout.attributedText.enumerateAttribute(.attachment, in: NSRange(location: 0, length: layout.attributedText.length)) { value, _, _ in
            if let value = value as? NSTextAttachment { attachments.append(value) }
        }
        XCTAssertEqual(attachments.count, 1)
        XCTAssertEqual(attachments[0].bounds.width, 20, accuracy: 0.01)
        XCTAssertEqual(attachments[0].bounds.height, 10, accuracy: 0.01)
        XCTAssertNotNil(attachments[0].image)
    }

    func testFormulaAndTextShareFontScalingInFullParagraph() throws {
        let formula = StructuredReadingInlineImage(image: try image(), size: CGSize(width: 20, height: 12))
        let layout = StructuredReadingTextLayout.fit(text: "测试 $x$", in: CGSize(width: 300, height: 100),
            preferredFontSize: 18, heading: false, template: "测试 [[PBM0]]", inlineImages: ["[[PBM0]]": formula], referenceFontSize: 12)
        let attachment = try XCTUnwrap(layout.attributedText.attribute(.attachment, at: 3, effectiveRange: nil) as? NSTextAttachment)
        XCTAssertEqual(attachment.bounds.width, 30, accuracy: 0.01)
        XCTAssertEqual(attachment.bounds.height, 18, accuracy: 0.01)
    }

    func testMissingImageFallsBackToReadableTextWithoutExposingTemplate() {
        let layout = StructuredReadingTextLayout.fit(text: "原式 $x^2$", in: CGSize(width: 100, height: 50),
            preferredFontSize: 12, heading: false, template: "原式 [[PBM0]]")
        XCTAssertEqual(layout.attributedText.string, "原式 $x^2$")
    }

    func testStrokeMaskRestoresBorderWithoutCoveringBoxInterior() {
        let path = StructuredReadingDecoration(commands: [
            .init(op: "M", points: [10, 10]), .init(op: "L", points: [90, 10]),
            .init(op: "L", points: [90, 80]), .init(op: "L", points: [10, 80]), .init(op: "Z", points: [])
        ], width: 0.5, dash: [], dashPhase: 0, lineCap: 0, lineJoin: 0, color: [40, 150, 180], opacity: 0.5)
        let mask = StructuredReadingDecorations.strokeMask([path])
        XCTAssertTrue(mask.contains(CGPoint(x: 50, y: 80)))
        XCTAssertFalse(mask.contains(CGPoint(x: 50, y: 40)))
        XCTAssertFalse(mask.contains(CGPoint(x: 5, y: 80)))
    }

    func testDashedStrokeDoesNotRestorePixelsInGaps() {
        let path = StructuredReadingDecoration(commands: [.init(op: "M", points: [0, 20]), .init(op: "L", points: [100, 20])],
            width: 1, dash: [4, 4], dashPhase: 0, lineCap: 0, lineJoin: 0, color: [0, 0, 0], opacity: 1)
        let mask = StructuredReadingDecorations.strokeMask([path])
        XCTAssertTrue(mask.contains(CGPoint(x: 2, y: 20)))
        XCTAssertFalse(mask.contains(CGPoint(x: 6, y: 20)))
    }

    func testSourceDecorationClipExcludesOldInlineFractionPosition() throws {
        let bitmap = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 100, pixelsHigh: 100,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        let graphics = try XCTUnwrap(NSGraphicsContext(bitmapImageRep: bitmap))
        let context = graphics.cgContext
        context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1)); context.fill(CGRect(x: 0, y: 0, width: 100, height: 100))
        let line = CGMutablePath(); line.addRect(CGRect(x: 0, y: 49, width: 100, height: 2))
        StructuredReadingDecorations.clip(line, excluding: [CGRect(x: 40, y: 40, width: 20, height: 20)],
            page: CGRect(x: 0, y: 0, width: 100, height: 100), context: context)
        context.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 1)); context.fill(CGRect(x: 0, y: 0, width: 100, height: 100))
        XCTAssertLessThan(try XCTUnwrap(bitmap.colorAt(x: 20, y: 50)?.usingColorSpace(.deviceRGB)).redComponent, 0.05)
        XCTAssertGreaterThan(try XCTUnwrap(bitmap.colorAt(x: 50, y: 50)?.usingColorSpace(.deviceRGB)).redComponent, 0.95)
    }
}
