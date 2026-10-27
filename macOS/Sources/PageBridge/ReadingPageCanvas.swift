import AppKit
import PDFKit
import SwiftUI
import PageBridgeCore

struct ReadingCanvasPlacement: Equatable, Identifiable {
    let block: StructuredReadingBlock
    let rect: CGRect
    let preferredFontSize: CGFloat
    var id: String { block.id }
    var overlaysTranslation: Bool {
        ["text", "heading", "list"].contains(block.kind) && block.status == "translated"
            && block.translatedText?.isEmpty == false
    }
}

struct ReadingPageCanvas: NSViewRepresentable {
    let image: NSImage
    let pageSize: CGSize
    let placements: [ReadingCanvasPlacement]
    var decorations: [StructuredReadingDecoration] = []
    var backgroundImage: NSImage?
    var backgroundBlocks: Set<String>?

    func makeNSView(context: Context) -> ReadingPageCanvasView { ReadingPageCanvasView() }
    func updateNSView(_ view: ReadingPageCanvasView, context: Context) {
        view.configure(image: image, pageSize: pageSize, placements: placements, decorations: decorations,
                       backgroundImage: backgroundImage, backgroundBlocks: backgroundBlocks)
    }
}

/// Page geometry stays in PDF points. Only the entire page is scaled to the viewport.
final class ReadingPageCanvasView: NSView {
    override var isFlipped: Bool { true }
    private var image: NSImage?
    private var backgroundImage: NSImage?
    private var backgroundBlocks: Set<String>?
    private var pageSize: CGSize = .zero
    private var placements: [ReadingCanvasPlacement] = []
    private var layouts: [String: StructuredReadingTextLayout] = [:]
    private var decorations: [StructuredReadingDecoration] = []
    private var decorationMask: CGPath?
    private var movedEquationRects: [CGRect] = []

    func configure(image: NSImage, pageSize: CGSize, placements: [ReadingCanvasPlacement],
                   decorations: [StructuredReadingDecoration] = [], backgroundImage: NSImage? = nil,
                   backgroundBlocks: Set<String>? = nil) {
        guard self.image !== image || self.pageSize != pageSize || self.placements != placements
                || self.decorations != decorations || self.backgroundImage !== backgroundImage
                || self.backgroundBlocks != backgroundBlocks else { return }
        self.image = image; self.pageSize = pageSize; self.placements = placements
        self.backgroundImage = backgroundImage; self.backgroundBlocks = backgroundBlocks
        self.decorations = decorations
        decorationMask = backgroundBlocks != nil || decorations.isEmpty ? nil : StructuredReadingDecorations.strokeMask(decorations)
        movedEquationRects = placements.filter(\.overlaysTranslation).flatMap { placement in
            (placement.block.inlineMath ?? []).compactMap {
                StructuredReadingPageGeometry.rect($0.bbox, pageSize: pageSize)?.insetBy(dx: -0.75, dy: -0.75)
            }
        }
        layouts = Dictionary(uniqueKeysWithValues: placements.filter {
            $0.overlaysTranslation && (backgroundBlocks == nil || (backgroundImage != nil && backgroundBlocks!.contains($0.id)))
        }.map { placement in
            let block = placement.block
            let crops = StructuredReadingMathImages.crops(for: block, image: image, pageSize: pageSize)
            return (placement.id, StructuredReadingTextLayout.fit(text: block.translatedText ?? "", in: placement.rect.size,
                preferredFontSize: placement.preferredFontSize, heading: placement.block.kind == "heading",
                backgroundColor: block.backgroundColor, foregroundColor: block.foregroundColor,
                template: crops.count == block.inlineMath?.count ? block.translatedTemplate : nil,
                inlineImages: crops, minimumFontSize: block.sourceType == "table_cell" ? 6.5 : 8))
        })
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let image, pageSize.width > 0, pageSize.height > 0,
              let context = NSGraphicsContext.current?.cgContext else { return }
        context.saveGState()
        defer { context.restoreGState() }
        context.scaleBy(x: bounds.width / pageSize.width, y: bounds.height / pageSize.height)
        let pageRect = CGRect(origin: .zero, size: pageSize)
        NSBezierPath(rect: pageRect).addClip()
        func drawSource() {
            image.draw(in: pageRect, from: .zero, operation: .sourceOver, fraction: 1, respectFlipped: true,
                       hints: [.interpolation: NSImageInterpolation.high])
        }
        if backgroundBlocks != nil {
            // The prepared page already retains photos, vector shapes and tables.
            // Only original text belonging to listed blocks was removed upstream.
            (backgroundImage ?? image).draw(in: pageRect, from: .zero, operation: .sourceOver, fraction: 1,
                respectFlipped: true, hints: [.interpolation: NSImageInterpolation.high])
        } else {
            drawSource()
            for placement in placements where layouts[placement.id] != nil {
                let rgb = placement.block.backgroundColor ?? [255, 255, 255]
                NSColor(calibratedRed: rgb[0] / 255, green: rgb[1] / 255, blue: rgb[2] / 255, alpha: 1).setFill()
                placement.rect.fill()
            }
            // Legacy and scanned pages retain source regions and fine lines.
            // Restore them before drawing text so a nearby table cannot erase it.
            for placement in placements where placement.block.isSourceRegion {
                NSGraphicsContext.saveGraphicsState()
                NSBezierPath(rect: placement.rect).addClip()
                drawSource()
                NSGraphicsContext.restoreGraphicsState()
            }
            if let decorationMask {
                context.saveGState()
                StructuredReadingDecorations.clip(decorationMask, excluding: movedEquationRects, page: pageRect, context: context)
                drawSource()
                context.restoreGState()
            }
        }
        for placement in placements {
            layouts[placement.id]?.draw(in: placement.rect)
        }
        for placement in placements {
            if layouts[placement.id]?.overflows == true {
                drawIndicator(in: placement.rect, color: .systemBlue, symbol: "+")
            } else if placement.block.status == "source_fallback" {
                drawIndicator(in: placement.rect, color: .systemOrange, symbol: "!")
            }
        }
    }

    private func drawIndicator(in rect: CGRect, color: NSColor, symbol: String) {
        NSGraphicsContext.saveGraphicsState()
        defer { NSGraphicsContext.restoreGraphicsState() }
        NSBezierPath(rect: rect).addClip()
        color.withAlphaComponent(0.65).setStroke()
        let outline = NSBezierPath(rect: rect.insetBy(dx: 0.4, dy: 0.4))
        outline.lineWidth = 0.8; outline.stroke()
        let size = min(10, rect.height, rect.width)
        let badge = CGRect(x: rect.maxX - size, y: rect.maxY - size, width: size, height: size)
        color.setFill(); NSBezierPath(roundedRect: badge, xRadius: 1, yRadius: 1).fill()
        let style = NSMutableParagraphStyle(); style.alignment = .center
        NSAttributedString(string: symbol, attributes: [.font: NSFont.boldSystemFont(ofSize: max(5, size - 2)),
            .foregroundColor: NSColor.white, .paragraphStyle: style]).draw(in: badge)
    }
}

struct ReadingSourcePage {
    let image: NSImage
    let size: CGSize
    var backgroundImage: NSImage?
}

@MainActor final class ReadingSourcePages: ObservableObject {
    private var pdf: PDFDocument?
    private var path: String?
    private let images = NSCache<NSString, NSImage>()
    private let backgrounds = NSCache<NSString, NSImage>()
    private var fonts: [String: CGFloat] = [:]

    init() { images.countLimit = 3; backgrounds.countLimit = 3 }

    func open(_ url: URL) {
        guard path != url.path else { return }
        objectWillChange.send()
        path = url.path; pdf = PDFDocument(url: url)
        images.removeAllObjects(); backgrounds.removeAllObjects(); fonts.removeAll()
    }

    func page(_ number: Int, metadata: StructuredReadingPage?) -> ReadingSourcePage? {
        guard let page = pdf?.page(at: number - 1) else { return nil }
        let bounds = page.bounds(for: .cropBox)
        let rotated = abs(page.rotation % 180) == 90
        let size = metadata.map { CGSize(width: $0.width, height: $0.height) }
            ?? CGSize(width: rotated ? bounds.height : bounds.width, height: rotated ? bounds.width : bounds.height)
        let key = "\(number)-\(size.width)-\(size.height)" as NSString
        var background: NSImage?
        if let encoded = metadata?.backgroundImage {
            let backgroundKey = "\(number)-\(encoded.hashValue)" as NSString
            background = backgrounds.object(forKey: backgroundKey)
            if background == nil, let decoded = StructuredReadingBackground.image(encoded) {
                backgrounds.setObject(decoded, forKey: backgroundKey); background = decoded
            }
        }
        if let image = images.object(forKey: key) { return ReadingSourcePage(image: image, size: size, backgroundImage: background) }
        let scale = min(3, 3000 / max(size.width, size.height))
        let image = page.thumbnail(of: CGSize(width: size.width * scale, height: size.height * scale), for: .cropBox)
        images.setObject(image, forKey: key)
        return ReadingSourcePage(image: image, size: size, backgroundImage: background)
    }

    func fontSize(for block: StructuredReadingBlock, pageNumber: Int) -> CGFloat? {
        guard ["text", "heading", "list"].contains(block.kind),
              let box = block.bbox, box.count == 4, let page = pdf?.page(at: pageNumber - 1),
              page.rotation % 360 == 0 else { return nil }
        let key = "\(pageNumber)-\(box)"
        if let cached = fonts[key] { return cached }
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
            if count > middle { fonts[key] = size; return size }
        }
        return nil
    }
}
