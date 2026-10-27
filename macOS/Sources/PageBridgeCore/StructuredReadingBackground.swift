import AppKit
import ImageIO

/// The background is embedded PNG data, never a path or a network reference.
public enum StructuredReadingBackground {
    public static func pngData(_ encoded: String) -> Data? {
        guard encoded.utf8.count <= 16 * 1024 * 1024,
              let data = Data(base64Encoded: encoded), data.count >= 24,
              data.starts(with: [137, 80, 78, 71, 13, 10, 26, 10]),
              let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              CGImageSourceGetCount(source) == 1,
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int,
              width > 0, height > 0, width <= 8_000, height <= 8_000,
              width * height <= 16_000_000 else { return nil }
        return data
    }

    public static func image(_ encoded: String) -> NSImage? {
        guard let data = pngData(encoded),
              let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return nil }
        return NSImage(cgImage: image, size: CGSize(width: image.width, height: image.height))
    }
}
