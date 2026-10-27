import AppKit
import XCTest
@testable import PageBridgeCore

final class StructuredReadingTextLayoutTests: XCTestCase {
    func testTableLabelFitsOriginalFinancialRowWithoutLoweringBodyMinimum() throws {
        let bounds = CGSize(width: 120, height: 8.86)
        let table = StructuredReadingTextLayout.fit(text: "现金及现金等价物", in: bounds,
            preferredFontSize: 8, heading: false, minimumFontSize: 6.5)
        XCTAssertFalse(table.overflows)
        XCTAssertGreaterThanOrEqual(table.fontSize, 6.5)
        XCTAssertLessThan(table.fontSize, 8)
        XCTAssertLessThanOrEqual(table.measuredSize.height, bounds.height)
        let body = StructuredReadingTextLayout.fit(text: "现金及现金等价物", in: bounds,
            preferredFontSize: 8, heading: false)
        XCTAssertTrue(body.overflows)
        XCTAssertEqual(body.fontSize, 8)
        let block = try JSONDecoder().decode(StructuredReadingBlock.self, from: Data("""
        {"id":"cell","kind":"text","sourceType":"table_cell","sourceText":"Cash",
         "translatedText":"现金","status":"translated"}
        """.utf8))
        XCTAssertEqual(block.sourceType, "table_cell")
    }

    func testChineseTextFitsWithoutExceedingOriginalRectangle() {
        let bounds = CGSize(width: 160, height: 42)
        let layout = StructuredReadingTextLayout.fit(text: "注意力机制可以同时处理多个位置，并保持原页的双栏布局。",
            in: bounds, preferredFontSize: 18, heading: false)
        XCTAssertFalse(layout.overflows)
        XCTAssertGreaterThanOrEqual(layout.fontSize, 8)
        XCTAssertLessThan(layout.fontSize, 18)
        XCTAssertLessThanOrEqual(layout.measuredSize.width, bounds.width)
        XCTAssertLessThanOrEqual(layout.measuredSize.height, bounds.height)
    }

    func testOverflowRetainsReadableMinimumInsteadOfExpandingTheFrame() {
        let bounds = CGSize(width: 50, height: 12)
        let layout = StructuredReadingTextLayout.fit(text: String(repeating: "完整译文通过点击展开。", count: 30),
            in: bounds, preferredFontSize: 12, heading: false)
        XCTAssertEqual(layout.fontSize, 8)
        XCTAssertTrue(layout.overflows)
        XCTAssertGreaterThan(layout.measuredSize.height, bounds.height)
    }

    func testSourceRectanglesAreClippedToTheirPageWithoutInventingMissingPositions() throws {
        let page = CGSize(width: 600, height: 800)
        XCTAssertEqual(StructuredReadingPageGeometry.rect([-5, 780, 90, 830], pageSize: page),
                       CGRect(x: 0, y: 780, width: 90, height: 20))
        XCTAssertNil(StructuredReadingPageGeometry.rect(nil, pageSize: page))
        XCTAssertNil(StructuredReadingPageGeometry.rect([700, 0, 800, 20], pageSize: page))
    }

    func testDrawingLongTextCannotPaintOutsideTheFixedSourceRectangle() throws {
        let bitmap = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 120, pixelsHigh: 80,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        let context = try XCTUnwrap(NSGraphicsContext(bitmapImageRep: bitmap))
        let previous = NSGraphicsContext.current
        NSGraphicsContext.current = context
        defer { NSGraphicsContext.current = previous }
        NSColor.white.setFill(); CGRect(x: 0, y: 0, width: 120, height: 80).fill()
        let rect = CGRect(x: 20, y: 30, width: 40, height: 20)
        let layout = StructuredReadingTextLayout.fit(text: String(repeating: "原页文字覆盖", count: 20),
            in: rect.size, preferredFontSize: 12, heading: false)
        layout.draw(in: rect)
        var inkInside = 0, inkOutside = 0
        for y in 0..<80 {
            for x in 0..<120 {
                let color = try XCTUnwrap(bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
                if color.redComponent < 0.95 {
                    if rect.contains(CGPoint(x: CGFloat(x) + 0.5, y: CGFloat(y) + 0.5)) { inkInside += 1 }
                    else { inkOutside += 1 }
                }
            }
        }
        XCTAssertGreaterThan(inkInside, 0)
        XCTAssertEqual(inkOutside, 0)
    }

    func testDarkSourceBackgroundDrawsWhiteTextWithinItsRectangle() throws {
        let bitmap = try XCTUnwrap(NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 120, pixelsHigh: 80,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0))
        let context = try XCTUnwrap(NSGraphicsContext(bitmapImageRep: bitmap))
        let previous = NSGraphicsContext.current
        NSGraphicsContext.current = context
        defer { NSGraphicsContext.current = previous }
        NSColor(calibratedRed: 12 / 255, green: 18 / 255, blue: 35 / 255, alpha: 1).setFill()
        CGRect(x: 0, y: 0, width: 120, height: 80).fill()
        let rect = CGRect(x: 20, y: 30, width: 80, height: 20)
        let layout = StructuredReadingTextLayout.fit(text: "深色背景译文", in: rect.size,
            preferredFontSize: 12, heading: false, backgroundColor: [12, 18, 35])
        layout.draw(in: rect)
        var whiteInside = 0, whiteOutside = 0
        for y in 0..<80 {
            for x in 0..<120 {
                let color = try XCTUnwrap(bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
                if min(color.redComponent, color.greenComponent, color.blueComponent) > 0.9 {
                    if rect.contains(CGPoint(x: CGFloat(x) + 0.5, y: CGFloat(y) + 0.5)) { whiteInside += 1 }
                    else { whiteOutside += 1 }
                }
            }
        }
        XCTAssertGreaterThan(whiteInside, 0)
        XCTAssertEqual(whiteOutside, 0)
    }
}
