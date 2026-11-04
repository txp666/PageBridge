import Foundation
import PDFKit

public struct LibrarySearchHit: Identifiable, Sendable, Equatable {
    public let id = UUID()
    public var documentID: UUID
    public var documentName: String
    public var page: Int
    public var snippet: String
    public var matchRange: Range<Int>
    public var inTranslation: Bool
}

/// Searches the original text and reading-draft translations of every document.
/// Page text is extracted once per PDF and cached beside it, keyed by the PDF's hash.
public enum LibrarySearch {
    public static let maximumHits = 500

    private struct Cache: Codable { var inputSHA256: String; var pages: [String] }

    public static func search(_ query: String, documents: [DocumentRecord]) throws -> [LibrarySearchHit] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard needle.count >= 2 else { return [] }
        var hits: [LibrarySearchHit] = []
        for document in documents {
            try Task.checkCancellation()
            for (index, text) in pageTexts(document).enumerated() {
                if let hit = hit(in: text, needle: needle, document: document, page: index + 1, translation: false) { hits.append(hit) }
            }
            for (page, text) in translations(document) {
                if let hit = hit(in: text, needle: needle, document: document, page: page, translation: true) { hits.append(hit) }
            }
            if hits.count >= maximumHits { break }
        }
        return Array(hits.prefix(maximumHits))
    }

    static func hit(in text: String, needle: String, document: DocumentRecord, page: Int, translation: Bool) -> LibrarySearchHit? {
        guard let range = text.range(of: needle, options: [.caseInsensitive, .diacriticInsensitive]) else { return nil }
        let start = text.index(range.lowerBound, offsetBy: -40, limitedBy: text.startIndex) ?? text.startIndex
        let end = text.index(range.upperBound, offsetBy: 80, limitedBy: text.endIndex) ?? text.endIndex
        let flatten = { (part: Substring) in part.replacingOccurrences(of: "\n", with: " ") }
        let before = flatten(text[start..<range.lowerBound]), match = flatten(text[range]), after = flatten(text[range.upperBound..<end])
        let prefix = start == text.startIndex ? "" : "…"
        let snippet = prefix + before + match + after + (end == text.endIndex ? "" : "…")
        let lower = prefix.count + before.count
        return LibrarySearchHit(documentID: document.id, documentName: document.name, page: page, snippet: snippet,
                                matchRange: lower..<(lower + match.count), inTranslation: translation)
    }

    static func pageTexts(_ document: DocumentRecord) -> [String] {
        let cacheURL = document.originalURL.deletingLastPathComponent().appendingPathComponent("search-text.json")
        if let data = try? Data(contentsOf: cacheURL), let cache = try? JSONDecoder().decode(Cache.self, from: data),
           cache.inputSHA256 == document.inputSHA256 {
            return cache.pages
        }
        guard let pdf = PDFDocument(url: document.originalURL), !pdf.isLocked else { return [] }
        let pages = (0..<pdf.pageCount).map { pdf.page(at: $0)?.string ?? "" }
        if let data = try? JSONEncoder().encode(Cache(inputSHA256: document.inputSHA256, pages: pages)) {
            try? data.write(to: cacheURL, options: .atomic)
        }
        return pages
    }

    static func translations(_ document: DocumentRecord) -> [(Int, String)] {
        guard let url = document.readingURL, let data = try? Data(contentsOf: url),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let pages = root["pages"] as? [[String: Any]] else { return [] }
        return pages.compactMap { page in
            guard let number = page["pageNumber"] as? Int, let blocks = page["blocks"] as? [[String: Any]] else { return nil }
            let text = blocks.compactMap { $0["translatedText"] as? String }.joined(separator: "\n")
            return text.isEmpty ? nil : (number, text)
        }
    }
}
