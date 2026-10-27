import XCTest
@testable import PageBridgeCore

final class ReadingScrollSynchronizationTests: XCTestCase {
    private let sizes = [CGSize(width: 612, height: 792), CGSize(width: 792, height: 612), CGSize(width: 420, height: 900)]

    func testMixedSizePagesRoundTripPageAndFraction() throws {
        let layout = ReadingScrollLayout(pageSizes: sizes, viewport: CGSize(width: 500, height: 600), zoom: 1)
        for page in 1...3 {
            for fraction in [0.0, 0.17, 0.5, 0.99] {
                let anchor = ReadingScrollAnchor(page: page, fraction: fraction)
                let actual = try XCTUnwrap(layout.anchor(at: layout.offset(for: anchor)))
                XCTAssertEqual(actual.page, page)
                XCTAssertEqual(actual.fraction, fraction, accuracy: 0.000001)
            }
        }
    }

    func testPageGapRemainsContinuousWithoutClampingAtBoundaries() throws {
        let layout = ReadingScrollLayout(pageSizes: sizes, viewport: CGSize(width: 500, height: 600), zoom: 1)
        let first = layout.pageFrames[0], second = layout.pageFrames[1]
        for offset in stride(from: first.maxY, through: second.minY, by: 0.25) {
            let anchor = try XCTUnwrap(layout.anchor(at: offset))
            XCTAssertEqual(layout.offset(for: anchor), offset, accuracy: 0.000001)
        }
        XCTAssertEqual(layout.pageNumber(for: ReadingScrollAnchor(page: 1, fraction: 1.001)), 2)
    }

    func testZoomAndViewportResizePreserveCanonicalPosition() throws {
        let old = ReadingScrollLayout(pageSizes: sizes, viewport: CGSize(width: 500, height: 600), zoom: 1)
        let new = ReadingScrollLayout(pageSizes: sizes, viewport: CGSize(width: 800, height: 450), zoom: 2)
        let anchor = try XCTUnwrap(old.anchor(at: old.offset(for: ReadingScrollAnchor(page: 2, fraction: 0.47))))
        let resized = try XCTUnwrap(new.anchor(at: new.offset(for: anchor)))
        XCTAssertEqual(resized.page, 2)
        XCTAssertEqual(resized.fraction, 0.47, accuracy: 0.000001)
        XCTAssertGreaterThan(new.pageFrames[1].width, old.pageFrames[1].width)
    }

    func testHorizontalAnchorCentersFitPagesAndSurvivesZoomAndDifferentWidths() throws {
        let fit = ReadingScrollLayout(pageSizes: [sizes[0]], viewport: CGSize(width: 500, height: 600), zoom: 1)
        let initial = try XCTUnwrap(fit.anchor(at: CGPoint(x: 0, y: 40), viewport: CGSize(width: 500, height: 600)))
        XCTAssertEqual(initial.horizontalFraction, 0.5)
        XCTAssertEqual(ReadingScrollAnchor.horizontalFraction(at: 0, contentWidth: 636,
            viewportWidth: 633.4, pageMargins: 24), 0.5)
        let wide = ReadingScrollLayout(pageSizes: [sizes[0]], viewport: CGSize(width: 700, height: 450), zoom: 2)
        for fraction in [0.0, 0.23, 0.5, 1.0] {
            let target = ReadingScrollAnchor(page: 1, fraction: 0.2, horizontalFraction: fraction)
            let offset = target.horizontalOffset(contentWidth: wide.contentSize.width, viewportWidth: 700)
            let actual = try XCTUnwrap(wide.anchor(at: CGPoint(x: offset, y: wide.offset(for: target)),
                viewport: CGSize(width: 700, height: 450)))
            XCTAssertEqual(actual.horizontalFraction, fraction, accuracy: 0.000001)
            XCTAssertEqual(actual.fraction, target.fraction, accuracy: 0.000001)
        }
    }

    func testVirtualizationMountsVisiblePagesAndNeighborsOnly() {
        let layout = ReadingScrollLayout(pageSizes: Array(repeating: CGSize(width: 612, height: 792), count: 100),
            viewport: CGSize(width: 500, height: 600), zoom: 1)
        let viewport = CGRect(x: 0, y: layout.offset(for: ReadingScrollAnchor(page: 40, fraction: 0.1)), width: 500, height: 600)
        let pages = layout.visiblePages(in: viewport)
        XCTAssertTrue(pages.contains(40))
        XCTAssertTrue(pages.contains(41))
        XCTAssertLessThanOrEqual(pages.count, 4)
        XCTAssertFalse(pages.contains(1))
        XCTAssertFalse(pages.contains(100))
    }

    func testBottomAndEmptyDocumentsRemainBounded() {
        let layout = ReadingScrollLayout(pageSizes: sizes, viewport: CGSize(width: 500, height: 600), zoom: 1)
        XCTAssertEqual(layout.pageNumber(for: ReadingScrollAnchor(page: 3, fraction: 1.5)), 3)
        XCTAssertEqual(layout.visiblePages(in: CGRect(x: 0, y: layout.contentSize.height - 600, width: 500, height: 600)).last, 3)
        let empty = ReadingScrollLayout(pageSizes: [], viewport: CGSize(width: 500, height: 600), zoom: 1)
        XCTAssertNil(empty.anchor(at: 0))
        XCTAssertEqual(empty.visiblePages(in: .zero), [])
    }

    @MainActor func testBothDirectionsUseTheLatestSourceWithoutFeedback() {
        let sync = ReadingScrollSynchronizer()
        let left = Peer(), right = Peer()
        sync.register(left); sync.register(right)
        right.onApply = { anchor in sync.scrolled(anchor, from: right) }
        let first = ReadingScrollAnchor(page: 2, fraction: 0.42)
        left.anchor = first; sync.scrolled(first, from: left)
        XCTAssertEqual(right.applied, [first])
        XCTAssertTrue(left.applied.isEmpty)
        right.onApply = nil
        let second = ReadingScrollAnchor(page: 3, fraction: 0.18)
        right.anchor = second; sync.scrolled(second, from: right)
        XCTAssertEqual(left.applied, [second])
        XCTAssertFalse(sync.isSynchronizing)
    }

    @MainActor func testDisabledPeersStayIndependentAndReenableUsesLastScrolledSide() {
        let sync = ReadingScrollSynchronizer()
        let left = Peer(), right = Peer()
        sync.register(left); sync.register(right)
        left.synchronizesScrolling = false; right.synchronizesScrolling = false
        let target = ReadingScrollAnchor(page: 7, fraction: 0.62)
        right.anchor = target; sync.scrolled(target, from: right)
        XCTAssertTrue(left.applied.isEmpty)
        left.synchronizesScrolling = true; right.synchronizesScrolling = true
        sync.align()
        XCTAssertEqual(left.applied, [target])
        XCTAssertEqual(right.reported, [7])
    }

    @MainActor func testDetachedLeaderCannotDriveNewDocument() {
        let sync = ReadingScrollSynchronizer()
        let old = Peer(), current = Peer()
        sync.register(old); sync.register(current)
        old.anchor = ReadingScrollAnchor(page: 9, fraction: 0.5)
        sync.scrolled(old.anchor!, from: old)
        sync.unregister(old)
        current.anchor = ReadingScrollAnchor(page: 1, fraction: 0)
        sync.align()
        XCTAssertEqual(current.reported, [1])
    }
}

@MainActor private final class Peer: ReadingScrollPeer {
    var synchronizesScrolling = true
    var anchor: ReadingScrollAnchor? = ReadingScrollAnchor(page: 1, fraction: 0)
    var applied: [ReadingScrollAnchor] = []
    var reported: [Int] = []
    var onApply: ((ReadingScrollAnchor) -> Void)?
    func applyAnchor(_ anchor: ReadingScrollAnchor) { applied.append(anchor); self.anchor = anchor; onApply?(anchor) }
    func visibleAnchor() -> ReadingScrollAnchor? { anchor }
    func pageNumber(for anchor: ReadingScrollAnchor) -> Int { anchor.page }
    func reportPage(_ number: Int) { reported.append(number) }
}
