import AppKit
import CryptoKit
import ImageIO

public enum StructuredReadingBackground {
    private static let maxPNGBytes = 16 * 1024 * 1024

    public static func pngData(_ encoded: String) -> Data? {
        guard encoded.utf8.count <= maxPNGBytes, let data = Data(base64Encoded: encoded),
              isValidPNG(data) else { return nil }
        return data
    }

    static func fileURL(for pageNumber: Int, nextTo readingURL: URL) -> URL {
        readingURL.deletingLastPathComponent().appendingPathComponent("background-\(pageNumber).png")
    }

    static func fileData(for pageNumber: Int, nextTo readingURL: URL, digest: String) throws -> Data {
        let file = fileURL(for: pageNumber, nextTo: readingURL)
        let info = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard info?.isRegularFile == true, info?.isSymbolicLink != true,
              let size = info?.fileSize, size > 0, size <= maxPNGBytes,
              let data = try? Data(contentsOf: file), isValidPNG(data),
              SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == digest else {
            throw BridgeError.message("阅读稿第 \(pageNumber) 页背景文件不可用或校验失败。")
        }
        return data
    }

    private static func isValidPNG(_ data: Data) -> Bool {
        guard (24...maxPNGBytes).contains(data.count),
              data.starts(with: [137, 80, 78, 71, 13, 10, 26, 10]),
              let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              CGImageSourceGetCount(source) == 1,
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int,
              width > 0, height > 0, width <= 8_000, height <= 8_000,
              width * height <= 16_000_000 else { return false }
        return true
    }

    public static func image(_ encoded: String) -> NSImage? {
        pngData(encoded).flatMap(makeImage)
    }

    static func image(for page: StructuredReadingPage, readingPath: String?) -> NSImage? {
        if let digest = page.backgroundSHA256, let readingPath {
            let readingURL = URL(fileURLWithPath: readingPath)
            return (try? fileData(for: page.pageNumber, nextTo: readingURL, digest: digest)).flatMap(makeImage)
        }
        return page.backgroundImage.flatMap(image)
    }

    private static func makeImage(_ data: Data) -> NSImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary) else { return nil }
        return NSImage(cgImage: image, size: CGSize(width: image.width, height: image.height))
    }
}
