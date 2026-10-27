import AppKit

/// The attachment is a crop of the original equation, not reconstructed LaTeX.
public struct StructuredReadingInlineImage {
    public let image: NSImage
    public let size: CGSize
    public init(image: NSImage, size: CGSize) { self.image = image; self.size = size }
}

public enum StructuredReadingMathImages {
    public static func crops(for block: StructuredReadingBlock, image: NSImage, pageSize: CGSize) -> [String: StructuredReadingInlineImage] {
        guard pageSize.width > 0, pageSize.height > 0,
              let bitmap = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return [:] }
        let sx = CGFloat(bitmap.width) / pageSize.width, sy = CGFloat(bitmap.height) / pageSize.height
        var result: [String: StructuredReadingInlineImage] = [:]
        for formula in block.inlineMath ?? [] {
            guard let original = StructuredReadingPageGeometry.rect(formula.bbox, pageSize: pageSize) else { continue }
            // OCR boxes may touch accent tips. This is a fixed rendering margin,
            // not a guessed formula boundary, and never changes stored coordinates.
            let rect = original.insetBy(dx: -0.75, dy: -0.75).intersection(CGRect(origin: .zero, size: pageSize))
            guard
                  let crop = bitmap.cropping(to: CGRect(x: rect.minX * sx, y: rect.minY * sy,
                      width: rect.width * sx, height: rect.height * sy).integral) else { continue }
            result[formula.token] = StructuredReadingInlineImage(image: NSImage(cgImage: crop, size: rect.size), size: rect.size)
        }
        return result
    }
}

public enum StructuredReadingPageGeometry {
    public static func rect(_ bbox: [Double]?, pageSize: CGSize) -> CGRect? {
        guard let box = bbox, box.count == 4, box.allSatisfy(\.isFinite) else { return nil }
        let rect = CGRect(x: box[0], y: box[1], width: box[2] - box[0], height: box[3] - box[1])
            .intersection(CGRect(origin: .zero, size: pageSize))
        return rect.isNull || rect.isEmpty ? nil : rect
    }
}

/// Measures and draws with the same TextKit layout in original PDF points.
/// The caller clips drawing to the source rectangle; overflow never grows it.
public final class StructuredReadingTextLayout {
    public let fontSize: CGFloat
    public let measuredSize: CGSize
    public let overflows: Bool
    private let storage: NSTextStorage
    private let manager: NSLayoutManager
    private let container: NSTextContainer
    public var attributedText: NSAttributedString { NSAttributedString(attributedString: storage) }

    private init(text: String, width: CGFloat, fontSize: CGFloat, heading: Bool, foregroundColor: NSColor,
                 template: String?, images: [String: StructuredReadingInlineImage], referenceFontSize: CGFloat) {
        self.fontSize = fontSize
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byWordWrapping
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: fontSize, weight: heading ? .semibold : .regular),
            .foregroundColor: foregroundColor,
            .paragraphStyle: paragraph
        ]
        storage = NSTextStorage(attributedString: Self.richText(text: text, template: template,
            images: images, fontSize: fontSize, referenceFontSize: referenceFontSize, attributes: attributes))
        manager = NSLayoutManager()
        container = NSTextContainer(size: CGSize(width: max(1, width), height: .greatestFiniteMagnitude))
        container.lineFragmentPadding = 0
        storage.addLayoutManager(manager)
        manager.addTextContainer(container)
        manager.ensureLayout(for: container)
        measuredSize = manager.usedRect(for: container).size
        overflows = false
    }

    private init(copying layout: StructuredReadingTextLayout, overflows: Bool) {
        fontSize = layout.fontSize; measuredSize = layout.measuredSize
        storage = layout.storage; manager = layout.manager; container = layout.container
        self.overflows = overflows
    }

    public static func fit(text: String, in bounds: CGSize, preferredFontSize: CGFloat, heading: Bool,
                           backgroundColor: [Double]? = nil, foregroundColor: [Double]? = nil, template: String? = nil,
                           inlineImages: [String: StructuredReadingInlineImage] = [:],
                           referenceFontSize: CGFloat? = nil, minimumFontSize: CGFloat = 8) -> StructuredReadingTextLayout {
        let minimum = max(1, minimumFontSize)
        let maximum = max(minimum, preferredFontSize)
        let rgb = backgroundColor?.count == 3 ? backgroundColor! : [255, 255, 255]
        let brightness = rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114
        let foreground: NSColor
        if let color = foregroundColor, color.count == 3 {
            foreground = NSColor(srgbRed: color[0] / 255, green: color[1] / 255, blue: color[2] / 255, alpha: 1)
        } else {
            foreground = brightness < 128 ? .white : .black
        }
        func layout(_ size: CGFloat) -> StructuredReadingTextLayout {
            StructuredReadingTextLayout(text: text, width: bounds.width, fontSize: size, heading: heading,
                                        foregroundColor: foreground, template: template, images: inlineImages,
                                        referenceFontSize: referenceFontSize ?? preferredFontSize)
        }
        func fits(_ value: StructuredReadingTextLayout) -> Bool {
            value.measuredSize.height <= bounds.height && value.measuredSize.width <= bounds.width
        }
        let full = layout(maximum)
        if fits(full) { return full }
        let small = layout(minimum)
        guard fits(small) else { return StructuredReadingTextLayout(copying: small, overflows: true) }
        var low = minimum, high = maximum, best = small
        for _ in 0..<8 {
            let mid = (low + high) / 2, candidate = layout(mid)
            if fits(candidate) { low = mid; best = candidate } else { high = mid }
        }
        return best
    }

    private static func richText(text: String, template: String?, images: [String: StructuredReadingInlineImage],
                                 fontSize: CGFloat, referenceFontSize: CGFloat,
                                 attributes: [NSAttributedString.Key: Any]) -> NSAttributedString {
        guard let template, !images.isEmpty else { return NSAttributedString(string: text, attributes: attributes) }
        let source = template as NSString
        var matches: [(NSRange, StructuredReadingInlineImage)] = []
        for (token, image) in images where !token.isEmpty {
            var search = NSRange(location: 0, length: source.length)
            while search.length > 0 {
                let found = source.range(of: token, options: [], range: search)
                guard found.location != NSNotFound else { break }
                matches.append((found, image))
                search = NSRange(location: NSMaxRange(found), length: source.length - NSMaxRange(found))
            }
        }
        // Old artifacts or unavailable source images remain readable as plain text.
        guard !matches.isEmpty else { return NSAttributedString(string: text, attributes: attributes) }
        let output = NSMutableAttributedString()
        let scale = fontSize / max(1, referenceFontSize)
        var offset = 0
        for (range, value) in matches.sorted(by: { $0.0.location < $1.0.location }) where range.location >= offset {
            output.append(NSAttributedString(string: source.substring(with: NSRange(location: offset, length: range.location - offset)), attributes: attributes))
            let attachment = NSTextAttachment()
            attachment.image = value.image
            let size = CGSize(width: value.size.width * scale, height: value.size.height * scale)
            let font = NSFont.systemFont(ofSize: fontSize)
            attachment.bounds = CGRect(x: 0, y: (font.capHeight - size.height) / 2, width: size.width, height: size.height)
            let piece = NSMutableAttributedString(attachment: attachment)
            piece.addAttributes(attributes, range: NSRange(location: 0, length: piece.length))
            output.append(piece)
            offset = NSMaxRange(range)
        }
        output.append(NSAttributedString(string: source.substring(from: offset), attributes: attributes))
        return output
    }

    /// Call from a flipped AppKit view so PDF top-left coordinates stay unchanged.
    public func draw(in rect: CGRect) {
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath(rect: rect).addClip()
        manager.drawGlyphs(forGlyphRange: manager.glyphRange(for: container), at: rect.origin)
        NSGraphicsContext.restoreGraphicsState()
    }
}
