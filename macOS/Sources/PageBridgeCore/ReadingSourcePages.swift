import AppKit
import PDFKit

public struct ReadingPageRequest: Hashable, Sendable {
    let url: URL
    let page: Int
    let readingPath: String?

    public init(url: URL, page: Int, readingPath: String?) {
        self.url = url
        self.page = page
        self.readingPath = readingPath
    }
}

/// Immutable after preparation. The worker owns the PDFKit document and creates
/// the images; the canvas only uses the finished images and font measurements.
public final class ReadingSourcePage: @unchecked Sendable {
    public let request: ReadingPageRequest
    public let image: NSImage
    public let size: CGSize
    public let backgroundImage: NSImage?
    public let fontSizes: [String: CGFloat]

    init(request: ReadingPageRequest, image: NSImage, size: CGSize, background: NSImage?, fontSizes: [String: CGFloat]) {
        self.request = request
        self.image = image
        self.size = size
        self.backgroundImage = background
        self.fontSizes = fontSizes
    }
}

public actor ReadingSourcePages {
    private var pdf: PDFDocument?
    private var url: URL?
    private let pages = NSCache<NSNumber, ReadingSourcePage>()

    public init() { pages.totalCostLimit = 96 * 1024 * 1024 }

    public func page(_ request: ReadingPageRequest, metadata: StructuredReadingPage?) -> ReadingSourcePage? {
        guard !Task.isCancelled else { return nil }
        if url != request.url {
            url = request.url
            pdf = PDFDocument(url: request.url)
            pages.removeAllObjects()
        }
        let key = NSNumber(value: request.page)
        if let cached = pages.object(forKey: key), cached.request == request { return cached }
        guard let page = pdf?.page(at: request.page - 1) else { return nil }
        let bounds = page.bounds(for: .cropBox), rotated = abs(page.rotation % 180) == 90
        let size = metadata.map { CGSize(width: $0.width, height: $0.height) }
            ?? CGSize(width: rotated ? bounds.height : bounds.width, height: rotated ? bounds.width : bounds.height)
        let background = metadata?.backgroundImage.flatMap(StructuredReadingBackground.image)
        guard !Task.isCancelled else { return nil }
        let blocks = metadata?.blocks ?? []
        let image: NSImage
        if let background, !blocks.contains(where: { $0.inlineMath?.isEmpty == false }) {
            image = background
        } else {
            let scale = min(3, 3000 / max(1, size.width, size.height))
            image = page.thumbnail(of: CGSize(width: size.width * scale, height: size.height * scale), for: .cropBox)
        }
        var sizes: [String: CGFloat] = [:]
        for block in blocks {
            guard !Task.isCancelled else { return nil }
            sizes[block.id] = fontSize(for: block, on: page)
        }
        let result = ReadingSourcePage(request: request, image: image, size: size, background: background, fontSizes: sizes)
        func bytes(_ image: NSImage) -> Int {
            image.representations.reduce(0) { $0 + max(0, $1.pixelsWide) * max(0, $1.pixelsHigh) * 4 }
        }
        let cost = bytes(image) + (background !== image ? background.map(bytes) ?? 0 : 0)
        pages.setObject(result, forKey: key, cost: cost)
        return result
    }

    private func fontSize(for block: StructuredReadingBlock, on page: PDFPage) -> CGFloat? {
        guard ["text", "heading", "list"].contains(block.kind),
              let box = block.bbox, box.count == 4, page.rotation % 360 == 0 else { return nil }
        let crop = page.bounds(for: .cropBox)
        let rect = CGRect(x: crop.minX + box[0], y: crop.maxY - box[3], width: box[2] - box[0], height: box[3] - box[1])
            .intersection(crop)
        guard let text = page.selection(for: rect)?.attributedString else { return nil }
        var sizes: [(CGFloat, Int)] = []
        text.enumerateAttribute(.font, in: NSRange(location: 0, length: text.length)) { value, range, _ in
            if let font = value as? NSFont, font.pointSize.isFinite, font.pointSize > 0 {
                sizes.append((font.pointSize, range.length))
            }
        }
        guard !sizes.isEmpty else { return nil }
        sizes.sort { $0.0 < $1.0 }
        let middle = sizes.reduce(0) { $0 + $1.1 } / 2
        var count = 0
        for (size, length) in sizes {
            count += length
            if count > middle { return size }
        }
        return nil
    }
}
