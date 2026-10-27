import Combine
import Foundation

public struct ReadingScrollAnchor: Equatable {
    public var page: Int
    /// Fractions may extend into an inter-page gap so continuous scrolling does not pause.
    public var fraction: CGFloat
    public var horizontalFraction: CGFloat
    public init(page: Int, fraction: CGFloat, horizontalFraction: CGFloat = 0.5) {
        self.page = page; self.fraction = fraction; self.horizontalFraction = horizontalFraction
    }

    public static func horizontalFraction(at offset: CGFloat, contentWidth: CGFloat, viewportWidth: CGFloat,
                                          pageMargins: CGFloat = 0) -> CGFloat {
        if contentWidth - pageMargins <= viewportWidth { return 0.5 }
        let range = max(0, contentWidth - viewportWidth)
        return range > 0.5 ? min(1, max(0, offset / range)) : 0.5
    }

    public func horizontalOffset(contentWidth: CGFloat, viewportWidth: CGFloat) -> CGFloat {
        max(0, contentWidth - viewportWidth) * min(1, max(0, horizontalFraction))
    }
}

@MainActor public protocol ReadingScrollPeer: AnyObject {
    var synchronizesScrolling: Bool { get }
    func applyAnchor(_ anchor: ReadingScrollAnchor)
    func visibleAnchor() -> ReadingScrollAnchor?
    func pageNumber(for anchor: ReadingScrollAnchor) -> Int
    func reportPage(_ number: Int)
}

/// Coordinates native scroll views without publishing per-pixel SwiftUI state.
@MainActor public final class ReadingScrollSynchronizer: ObservableObject {
    private final class Peer {
        weak var value: (any ReadingScrollPeer)?
        init(_ value: any ReadingScrollPeer) { self.value = value }
    }
    private var peers: [Peer] = []
    private weak var leader: (any ReadingScrollPeer)?
    public private(set) var isSynchronizing = false
    public init() {}

    public func register(_ peer: any ReadingScrollPeer) {
        peers.removeAll { $0.value == nil || $0.value === peer }
        peers.append(Peer(peer))
    }
    public func unregister(_ peer: any ReadingScrollPeer) {
        peers.removeAll { $0.value == nil || $0.value === peer }
        if leader === peer { leader = nil }
    }
    public func reset() { leader = nil }

    public func scrolled(_ anchor: ReadingScrollAnchor, from source: any ReadingScrollPeer) {
        guard !isSynchronizing else { return }
        leader = source
        guard source.synchronizesScrolling else { return }
        isSynchronizing = true
        defer { isSynchronizing = false }
        for peer in peers.compactMap(\.value) where peer !== source && peer.synchronizesScrolling {
            peer.applyAnchor(anchor)
        }
    }
    public func align() {
        let source = leader.flatMap { $0.synchronizesScrolling ? $0 : nil }
            ?? peers.compactMap(\.value).first(where: \.synchronizesScrolling)
        guard let source, let anchor = source.visibleAnchor() else { return }
        scrolled(anchor, from: source)
        source.reportPage(source.pageNumber(for: anchor))
    }
}

/// Continuous page geometry, independent of bitmap loading and SwiftUI layout.
public struct ReadingScrollLayout: Equatable {
    public let pageFrames: [CGRect]
    public let contentSize: CGSize
    public let scale: CGFloat

    public init(pageSizes: [CGSize], viewport: CGSize, zoom: CGFloat) {
        let margin: CGFloat = 16
        scale = min(6, max(0.1, (viewport.width - 34) / max(1, pageSizes.first?.width ?? 612) * zoom))
        let width = max(viewport.width, (pageSizes.map(\.width).max() ?? 0) * scale + margin * 2)
        var y = margin
        var frames: [CGRect] = []
        for size in pageSizes {
            let scaled = CGSize(width: max(1, size.width) * scale, height: max(1, size.height) * scale)
            frames.append(CGRect(x: (width - scaled.width) / 2, y: y, width: scaled.width, height: scaled.height))
            y += scaled.height + 28 * scale
        }
        pageFrames = frames
        contentSize = CGSize(width: width, height: max(viewport.height, y - (frames.isEmpty ? 0 : 28 * scale) + margin))
    }

    public func anchor(at offset: CGFloat) -> ReadingScrollAnchor? {
        guard !pageFrames.isEmpty else { return nil }
        var low = 0, high = pageFrames.count
        while low < high {
            let middle = (low + high) / 2
            if pageFrames[middle].minY <= offset { low = middle + 1 } else { high = middle }
        }
        var index = max(0, low - 1)
        if index + 1 < pageFrames.count, offset > pageFrames[index].maxY,
           offset - pageFrames[index].maxY > pageFrames[index + 1].minY - offset { index += 1 }
        let rect = pageFrames[index]
        return ReadingScrollAnchor(page: index + 1, fraction: (offset - rect.minY) / rect.height)
    }

    public func offset(for anchor: ReadingScrollAnchor) -> CGFloat {
        guard !pageFrames.isEmpty else { return 0 }
        let rect = pageFrames[max(0, min(pageFrames.count - 1, anchor.page - 1))]
        return rect.minY + anchor.fraction * rect.height
    }

    public func anchor(at origin: CGPoint, viewport: CGSize) -> ReadingScrollAnchor? {
        guard var anchor = anchor(at: origin.y) else { return nil }
        anchor.horizontalFraction = ReadingScrollAnchor.horizontalFraction(at: origin.x,
            contentWidth: contentSize.width, viewportWidth: viewport.width)
        return anchor
    }

    public func pageNumber(for anchor: ReadingScrollAnchor) -> Int {
        max(1, min(pageFrames.count, anchor.page + (anchor.fraction >= 1 ? 1 : 0)))
    }

    public func visiblePages(in viewport: CGRect, prefetch: Int = 1) -> [Int] {
        guard !pageFrames.isEmpty else { return [] }
        let first = max(0, (anchor(at: viewport.minY)?.page ?? 1) - 1 - prefetch)
        let last = min(pageFrames.count - 1, (anchor(at: viewport.maxY)?.page ?? 1) - 1 + prefetch)
        return first <= last ? Array(first...last).map { $0 + 1 } : []
    }
}
