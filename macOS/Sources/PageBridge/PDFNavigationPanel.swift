import AppKit
import PDFKit
import SwiftUI
import PageBridgeCore

/// Each PDF instance is confined to this actor. The UI receives PNG data, never a shared PDFPage.
actor PDFPreviewCache {
    static let shared = PDFPreviewCache()
    private var documents: [String: PDFDocument] = [:]
    private var documentOrder: [String] = []
    private var previews: [String: Data] = [:]
    private var previewOrder: [String] = []
    private var cachedBytes = 0

    private func document(_ url: URL) -> PDFDocument? {
        let key = url.path
        if let existing = documents[key] { return existing }
        guard let value = PDFDocument(url: url) else { return nil }
        documents[key] = value; documentOrder.append(key)
        while documentOrder.count > 4 { documents.removeValue(forKey: documentOrder.removeFirst()) }
        return value
    }

    func thumbnail(url: URL, page: Int, width: Int) -> Data? {
        guard !Task.isCancelled else { return nil }
        let key = "\(url.path)|\(page)|\(width)"
        if let cached = previews[key] { return cached }
        guard let pdf = document(url), page > 0, page <= pdf.pageCount,
              let source = pdf.page(at: page - 1) else { return nil }
        let data: Data? = autoreleasepool {
            let bounds = source.bounds(for: .cropBox)
            guard bounds.width > 0, bounds.height > 0 else { return nil }
            let image = source.thumbnail(of: NSSize(width: width, height: Int(Double(width) * 1.5)), for: .cropBox)
            guard let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff) else { return nil }
            return bitmap.representation(using: .png, properties: [:])
        }
        if let data, data.count < 4 * 1_024 * 1_024 {
            previews[key] = data; previewOrder.append(key); cachedBytes += data.count
            while cachedBytes > 24 * 1_024 * 1_024 || previewOrder.count > 180 {
                if let old = previews.removeValue(forKey: previewOrder.removeFirst()) { cachedBytes -= old.count }
            }
        }
        return data
    }

    func outline(url: URL) -> [PaperOutlineEntry] {
        guard let pdf = document(url), let root = pdf.outlineRoot else { return [] }
        var entries: [PaperOutlineEntry] = []
        func visit(_ node: PDFOutline, depth: Int) {
            guard depth < 14, entries.count < 2_000 else { return }
            for index in 0..<min(node.numberOfChildren, 2_000) {
                guard entries.count < 2_000, let child = node.child(at: index) else { break }
                let destination = child.destination ?? (child.action as? PDFActionGoTo)?.destination
                if let page = destination?.page {
                    let number = pdf.index(for: page)
                    if number >= 0, number < pdf.pageCount {
                        entries.append(PaperOutlineEntry(id: entries.count, title: String((child.label ?? "未命名章节").prefix(240)), page: number + 1, depth: depth))
                    }
                }
                visit(child, depth: depth + 1)
            }
        }
        visit(root, depth: 0)
        return entries
    }
}

struct PaperOutlineEntry: Identifiable, Sendable {
    var id: Int
    var title: String
    var page: Int
    var depth: Int
}

struct PDFThumbnailImage: View {
    let url: URL
    var page = 1
    let width: CGFloat
    @State private var preview: NSImage?
    @State private var unavailable = false

    var body: some View {
        Group {
            if let preview {
                Image(nsImage: preview).resizable().scaledToFit()
            } else {
                ZStack {
                    RoundedRectangle(cornerRadius: 3).fill(Color(nsColor: .textBackgroundColor))
                    Image(systemName: unavailable ? "doc.questionmark" : "doc.text")
                        .font(.system(size: min(width * 0.35, 26))).foregroundStyle(.tertiary)
                }
                .aspectRatio(0.71, contentMode: .fit)
            }
        }
        .frame(width: width)
        .clipShape(RoundedRectangle(cornerRadius: 3))
        .overlay(RoundedRectangle(cornerRadius: 3).stroke(.gray.opacity(0.18), lineWidth: 0.5))
        .task(id: "\(url.path)|\(page)|\(Int(width))") {
            preview = nil; unavailable = false
            let png = await PDFPreviewCache.shared.thumbnail(url: url, page: page, width: max(80, Int(width * 2)))
            guard !Task.isCancelled else { return }
            preview = png.flatMap(NSImage.init(data:)); unavailable = preview == nil
        }
        .accessibilityLabel("第 \(page) 页预览")
    }
}

struct PDFNavigationPanel: View {
    let document: DocumentRecord
    let url: URL
    let page: Int
    let bookmarks: [Int]
    let onNavigate: (Int) -> Void
    @State private var tab = 0
    @State private var outline: [PaperOutlineEntry] = []

    var body: some View {
        VStack(spacing: 0) {
            Picker("页面导航", selection: $tab) {
                Text("页面").tag(0)
                Text("目录").tag(1)
                Text("书签").tag(2)
            }.pickerStyle(.segmented).labelsHidden().controlSize(.small).padding(9)
            Divider()
            if tab == 0 { thumbnails }
            else if tab == 1 { outlineList }
            else { bookmarkList }
        }
        .frame(width: 158)
        .background(Color(nsColor: .controlBackgroundColor))
        .task(id: url.path) { outline = await PDFPreviewCache.shared.outline(url: url) }
    }

    private var thumbnails: some View {
        ScrollViewReader { scroll in
            ScrollView {
                LazyVStack(spacing: 15) {
                    ForEach(1...max(1, document.pageCount), id: \.self) { number in
                        Button { onNavigate(number) } label: {
                            VStack(spacing: 6) {
                                PDFThumbnailImage(url: url, page: number, width: 112)
                                    .overlay(RoundedRectangle(cornerRadius: 4).stroke(number == page ? Color.accentColor : .clear, lineWidth: 3))
                                    .shadow(color: .black.opacity(0.09), radius: 3, y: 2)
                                HStack(spacing: 4) {
                                    if bookmarks.contains(number) { Image(systemName: "bookmark.fill").foregroundStyle(.orange) }
                                    Text("\(number)").monospacedDigit()
                                }.font(.caption).foregroundStyle(number == page ? Color.accentColor : .secondary)
                            }.padding(4)
                        }
                        .buttonStyle(.plain).id(number)
                        .accessibilityLabel("跳转到第 \(number) 页")
                        .accessibilityAddTraits(number == page ? .isSelected : [])
                    }
                }.padding(.vertical, 14).frame(maxWidth: .infinity)
            }
            .onAppear { scroll.scrollTo(page, anchor: .center) }
            .onChange(of: page) { _, value in scroll.scrollTo(value, anchor: .center) }
        }
    }

    private var outlineList: some View {
        Group {
            if outline.isEmpty { empty("这份 PDF 没有内置目录", symbol: "list.bullet.indent") }
            else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(outline) { entry in
                            Button { onNavigate(entry.page) } label: {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(entry.title).font(.caption).lineLimit(3)
                                    Text("第 \(entry.page) 页").font(.system(size: 10)).foregroundStyle(.secondary)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.leading, CGFloat(min(entry.depth, 3)) * 8 + 9)
                                .padding(.trailing, 8).padding(.vertical, 8)
                                .background(entry.page == page ? Color.accentColor.opacity(0.1) : .clear)
                            }.buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }

    private var bookmarkList: some View {
        Group {
            if bookmarks.isEmpty { empty("点击阅读工具栏的书签，标记重要页面。", symbol: "bookmark") }
            else {
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(bookmarks, id: \.self) { number in
                            Button { onNavigate(number) } label: {
                                HStack {
                                    Image(systemName: "bookmark.fill").foregroundStyle(.orange)
                                    Text("第 \(number) 页").font(.caption)
                                    Spacer()
                                }.padding(11).background(number == page ? Color.accentColor.opacity(0.1) : .clear)
                            }.buttonStyle(.plain)
                        }
                    }.padding(.top, 6)
                }
            }
        }
    }

    private func empty(_ text: String, symbol: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: symbol).font(.title2).foregroundStyle(.tertiary)
            Text(text).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }.padding(15).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
