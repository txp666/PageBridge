import Foundation
import PDFKit
import AppKit

public enum PDFDocumentVariant: String, Codable, CaseIterable, Identifiable, Sendable {
    case original, translated
    public var id: String { rawValue }
    public var title: String { self == .original ? "原文" : "译文" }
}

/// A revision is the PDF's bytes, not its filename or the document's latest attempt.
public struct AnnotationBinding: Codable, Hashable, Sendable {
    public var documentID: UUID
    public var variant: PDFDocumentVariant
    public var sourceSHA256: String
    public init(documentID: UUID, variant: PDFDocumentVariant, sourceSHA256: String) {
        self.documentID = documentID; self.variant = variant; self.sourceSHA256 = sourceSHA256
    }
}

public struct PDFPageRect: Codable, Equatable, Sendable {
    public var x: Double
    public var y: Double
    public var width: Double
    public var height: Double
    public init(_ rect: CGRect) {
        x = rect.origin.x; y = rect.origin.y; width = rect.width; height = rect.height
    }
    public var cgRect: CGRect { CGRect(x: x, y: y, width: width, height: height) }
    public var isValid: Bool { [x, y, width, height].allSatisfy(\.isFinite) && width > 0 && height > 0 }
}

public struct AnnotationRegion: Codable, Equatable, Sendable {
    public var page: Int // 1-based; coordinates are unrotated native PDF page space.
    public var rect: PDFPageRect
    public init(page: Int, rect: CGRect) { self.page = page; self.rect = PDFPageRect(rect) }
}

public enum ReadingAnnotationKind: String, Codable, Sendable {
    case highlight, underline, note, bookmark
    public var title: String {
        switch self { case .highlight: "高亮"; case .underline: "下划线"; case .note: "笔记"; case .bookmark: "书签" }
    }
}

public struct ReadingAnnotation: Identifiable, Codable, Equatable, Sendable {
    public var id: UUID
    public var kind: ReadingAnnotationKind
    public var page: Int
    public var regions: [AnnotationRegion]
    public var selectedText: String
    public var note: String
    public var createdAt: Date
    public var modifiedAt: Date
    public init(id: UUID = UUID(), kind: ReadingAnnotationKind, page: Int, regions: [AnnotationRegion] = [], selectedText: String = "", note: String = "") {
        self.id = id; self.kind = kind; self.page = page; self.regions = regions
        self.selectedText = selectedText; self.note = note; createdAt = Date(); modifiedAt = createdAt
    }
}

/// Uses PDFKit page-space selections. No view pixels, zoom factor or rotated display
/// rectangle is serialized. A rotated PDF page applies its own transform on display/export.
public enum PDFAnnotationRenderer {
    private static let nameKey = PDFAnnotationKey(rawValue: "NM")
    private static let prefix = "PageBridge.annotation."
    // In-memory ownership only: annotations already present in an imported PDF
    // are never removed, even if that PDF was previously exported by PageBridge.
    private static let overlays = NSMapTable<PDFDocument, NSArray>(keyOptions: [.weakMemory, .objectPointerPersonality], valueOptions: .strongMemory)
    private final class Overlay: NSObject {
        weak var annotation: PDFAnnotation?
        let id: UUID
        init(annotation: PDFAnnotation, id: UUID) { self.annotation = annotation; self.id = id }
    }

    /// Resolves only this live document's current overlays. Serialized names alone
    /// are not ownership: a reimported export remains part of the source PDF.
    public static func overlayID(for annotation: PDFAnnotation, in document: PDFDocument) -> UUID? {
        guard let page = annotation.page, page.document === document,
              page.annotations.contains(where: { $0 === annotation }),
              let current = overlays.object(forKey: document) as? [Overlay] else { return nil }
        for overlay in current {
            guard let owned = overlay.annotation, owned.page === page,
                  page.annotations.contains(where: { $0 === owned }) else { continue }
            // PDFKit can hit its automatically generated Popup rather than Text.
            if owned === annotation || owned.popup === annotation { return overlay.id }
        }
        return nil
    }

    public static func regions(for selection: PDFSelection, in document: PDFDocument) -> [AnnotationRegion] {
        selection.selectionsByLine().flatMap { line in
            line.pages.compactMap { page -> AnnotationRegion? in
                let index = document.index(for: page)
                guard index != NSNotFound, index >= 0, index < document.pageCount else { return nil }
                let bounds = line.bounds(for: page)
                guard PDFPageRect(bounds).isValid else { return nil }
                return AnnotationRegion(page: index + 1, rect: bounds)
            }
        }
    }

    /// Only removes this workspace's overlays; source PDF annotations are preserved.
    public static func apply(_ items: [ReadingAnnotation], to document: PDFDocument, forDisplay: Bool = false) throws {
        try validate(items, pageCount: document.pageCount)
        let previous = (overlays.object(forKey: document) as? [Overlay]) ?? []
        for overlay in previous {
            guard let annotation = overlay.annotation, let page = annotation.page,
                  page.document === document else { continue }
            if let popup = annotation.popup { page.removeAnnotation(popup) }
            page.removeAnnotation(annotation)
        }
        var added: [Overlay] = []
        let overlayGeneration = UUID().uuidString
        for item in items where item.kind != .bookmark {
            let regions: [AnnotationRegion]
            if item.kind == .note {
                guard let page = document.page(at: item.page - 1) else { continue }
                let crop = page.bounds(for: .cropBox)
                let selected = item.regions.first?.rect.cgRect
                let x = min(crop.maxX - 24, max(crop.minX, selected?.minX ?? crop.minX + 18))
                let y = min(crop.maxY - 24, max(crop.minY, selected?.maxY ?? crop.maxY - 18))
                regions = [AnnotationRegion(page: item.page, rect: CGRect(x: x, y: y, width: 24, height: 24))]
            } else { regions = item.regions }
            for (index, region) in regions.enumerated() {
                guard let page = document.page(at: region.page - 1) else { continue }
                let type: PDFAnnotationSubtype = item.kind == .note ? .text : (item.kind == .underline ? .underline : .highlight)
                let annotation = PDFAnnotation(bounds: region.rect.cgRect, forType: type, withProperties: nil)
                annotation.color = item.kind == .underline ? .systemRed : .systemYellow
                // The application owns editing. Avoid PDFKit's extra comment
                // bubbles on display-only markup; exported PDFs keep the text.
                annotation.contents = forDisplay && item.kind != .note ? nil : (item.note.isEmpty ? item.selectedText : item.note)
                annotation.userName = "PageBridge"
                annotation.modificationDate = item.modifiedAt
                annotation.shouldPrint = true
                let name = prefix + overlayGeneration + "." + item.id.uuidString + ".\(index)"
                annotation.setValue(name, forAnnotationKey: nameKey)
                if item.kind == .note { annotation.iconType = .note }
                else {
                    // PDFKit requires Z order relative to annotation.bounds.origin.
                    let w = region.rect.width, h = region.rect.height
                    annotation.quadrilateralPoints = [NSValue(point: NSPoint(x: 0, y: h)), NSValue(point: NSPoint(x: w, y: h)), NSValue(point: .zero), NSValue(point: NSPoint(x: w, y: 0))]
                }
                page.addAnnotation(annotation)
                added.append(Overlay(annotation: annotation, id: item.id))
            }
        }
        overlays.setObject(added as NSArray, forKey: document)
    }

    public static func validate(_ items: [ReadingAnnotation], pageCount: Int) throws {
        guard Set(items.map(\.id)).count == items.count,
              items.allSatisfy({ item in
                  (1...max(1, pageCount)).contains(item.page) &&
                  item.regions.allSatisfy { (1...max(1, pageCount)).contains($0.page) && $0.rect.isValid } &&
                  (!(item.kind == .highlight || item.kind == .underline) || !item.regions.isEmpty)
              }), pageCount > 0 else { throw BridgeError.message("批注页码或选区无效。") }
    }
}
