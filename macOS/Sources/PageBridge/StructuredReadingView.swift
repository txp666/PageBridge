import AppKit
import PDFKit
import SwiftUI
import PageBridgeCore

struct StructuredReadingView: View {
    @ObservedObject var model: AppModel
    let document: DocumentRecord
    @Binding var pageNumber: Int
    let navigation: PDFPageNavigation
    let acceptsNavigation: Bool
    let synchronizesScrolling: Bool
    let synchronizer: PDFScrollSynchronizer
    let onLocate: (Int, [Double]?) -> Void
    @StateObject private var sourcePages = ReadingSourcePages()
    @State private var showsUnlocated = false

    private var readingPage: StructuredReadingPage? { model.structuredReading?.page(pageNumber) }
    private var unlocated: [StructuredReadingBlock] {
        guard let metadata = readingPage else { return [] }
        let size = CGSize(width: metadata.width, height: metadata.height)
        return metadata.blocks.filter { StructuredReadingPageGeometry.rect($0.bbox, pageSize: size) == nil }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("译文 · 原页版式").font(.system(size: 10.5, weight: .medium))
                Spacer()
                if model.readingLoading { ProgressView().controlSize(.mini) }
                if let error = model.readingLoadError {
                    Image(systemName: "exclamationmark.circle").foregroundStyle(.secondary)
                        .help(error).accessibilityLabel(error)
                }
                if let error = readingPage?.error {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 11)).foregroundStyle(.orange)
                        .help(error).accessibilityLabel("第 \(pageNumber) 页：\(error)")
                        .accessibilityIdentifier("structured-reading-page-warning")
                }
                unlocatedControl
                Text("原文第 \(pageNumber) 页").font(.system(size: 10.5)).foregroundStyle(.secondary)
            }.lineLimit(1).padding(.horizontal, 16).frame(height: 22)
            ReadingContinuousScrollView(url: document.originalURL, page: $pageNumber, navigation: navigation,
                acceptsNavigation: acceptsNavigation, zoom: model.zoom,
                synchronizesScrolling: synchronizesScrolling, synchronizer: synchronizer) { number, scale in
                    AnyView(StructuredReadingPageContent(model: model, sourcePages: sourcePages,
                        pageNumber: number, scale: scale, onLocate: onLocate).id("\(document.id)-\(number)"))
                }
        }
        .frame(minWidth: 280, maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { sourcePages.open(document.originalURL) }
        .onChange(of: document.inputPath) { _, _ in sourcePages.open(document.originalURL) }
        .onChange(of: pageNumber) { _, _ in showsUnlocated = false }
    }

    @ViewBuilder private var unlocatedControl: some View {
        if !unlocated.isEmpty {
            Button("未定位内容（\(unlocated.count)）") { showsUnlocated.toggle() }
                .buttonStyle(.link).font(.system(size: 10.5))
                .popover(isPresented: $showsUnlocated) {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 18) {
                            Text("以下内容没有页内坐标，保留原页位置。")
                                .font(.caption).foregroundStyle(.secondary)
                            ForEach(unlocated) { block in
                                StructuredReadingBlockDetails(model: model, block: block, pageNumber: pageNumber,
                                    source: sourcePages.page(pageNumber, metadata: readingPage),
                                    preferredFontSize: sourcePages.fontSize(for: block, pageNumber: pageNumber) ?? 12) {
                                        showsUnlocated = false; onLocate(pageNumber, block.bbox)
                                    }
                                Divider()
                            }
                        }.padding(18)
                    }.lineLimit(nil).frame(width: 490, height: 500)
                }
        }
    }
}

/// The continuous native scroll view mounts only visible pages and their neighbors.
private struct StructuredReadingPageContent: View {
    @ObservedObject var model: AppModel
    @ObservedObject var sourcePages: ReadingSourcePages
    let pageNumber: Int
    let scale: CGFloat
    let onLocate: (Int, [Double]?) -> Void
    @State private var selectedBlockID: String?

    private var metadata: StructuredReadingPage? { model.structuredReading?.page(pageNumber) }

    var body: some View {
        if let source = sourcePages.page(pageNumber, metadata: metadata) {
            pageCanvas(source)
        } else {
            Color.white.overlay { Text("正在读取原页…").font(.caption).foregroundStyle(.secondary) }
        }
    }

    private func pageCanvas(_ source: ReadingSourcePage) -> some View {
        let placements = (metadata?.blocks ?? []).compactMap { block -> ReadingCanvasPlacement? in
            guard let rect = StructuredReadingPageGeometry.rect(block.bbox, pageSize: source.size) else { return nil }
            return ReadingCanvasPlacement(block: block, rect: rect,
                preferredFontSize: sourcePages.fontSize(for: block, pageNumber: pageNumber) ?? (block.kind == "heading" ? 18 : 12))
        }
        return ZStack(alignment: .topLeading) {
            ReadingPageCanvas(image: source.image, pageSize: source.size, placements: placements,
                              decorations: metadata?.decorations ?? [], backgroundImage: source.backgroundImage,
                              backgroundBlocks: metadata?.backgroundBlocks.map(Set.init))
                .allowsHitTesting(false)
            ForEach(placements) { placement in
                let rect = placement.rect
                Button { selectedBlockID = placement.id } label: {
                    Rectangle().fill(Color.clear).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .frame(width: rect.width * scale, height: rect.height * scale)
                .position(x: rect.midX * scale, y: rect.midY * scale)
                .zIndex(placement.overlaysTranslation ? 2 : (placement.block.isSourceRegion ? 1 : 0))
                .help(placement.block.status == "source_fallback" ? "查看原文或重试此段" : "点击查看完整内容")
                .accessibilityLabel("\(readingKindTitle(placement.block.kind))，点击查看完整内容")
                .popover(isPresented: Binding(get: { selectedBlockID == placement.id },
                                             set: { if !$0 { selectedBlockID = nil } })) {
                    ScrollView {
                        StructuredReadingBlockDetails(model: model, block: placement.block, pageNumber: pageNumber,
                            source: source, preferredFontSize: placement.preferredFontSize) {
                                selectedBlockID = nil; onLocate(pageNumber, placement.block.bbox)
                            }.padding(18)
                    }.frame(width: 490, height: 420)
                }
            }
        }
        .frame(width: source.size.width * scale, height: source.size.height * scale)
        .background(Color.white)
        .shadow(color: .black.opacity(0.14), radius: 3, y: 1)
    }
}

private struct StructuredReadingBlockDetails: View {
    @ObservedObject var model: AppModel
    let block: StructuredReadingBlock
    let pageNumber: Int
    let source: ReadingSourcePage?
    let preferredFontSize: CGFloat
    let onLocate: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(readingKindTitle(block.kind)).font(.headline)
                Spacer()
                Button("定位原文", action: onLocate).buttonStyle(.link)
                if block.status == "source_fallback" {
                    Button("重试此段") { model.retryReadingBlock(block) }.disabled(model.isTranslating)
                }
            }
            if let error = block.error {
                Text(error).font(.caption).foregroundStyle(.orange).textSelection(.enabled)
            }
            if block.status == "translated", let translated = block.translatedText,
               !translated.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
               block.kind != "code" && block.kind != "formula" {
                Text(block.isSourceRegion ? "文字翻译（辅助阅读）" : "完整译文")
                    .font(.caption).foregroundStyle(.secondary)
                ReadingMathText(block: block, image: source?.image, pageSize: source?.size ?? .zero,
                                preferredFontSize: preferredFontSize)
                Divider()
            }
            Text(block.kind == "formula" ? "识别文本" : "原文").font(.caption).foregroundStyle(.secondary)
            Group {
                if block.sourceText.isEmpty {
                    Text("本区域无提取文本，请查看原页。").font(.system(size: 14)).textSelection(.enabled)
                } else if block.kind == "code" {
                    Text(block.sourceText).font(.system(size: 14, design: .monospaced))
                        .lineSpacing(4).textSelection(.enabled)
                } else {
                    ReadingMathText(block: block, image: source?.image, pageSize: source?.size ?? .zero,
                                    preferredFontSize: preferredFontSize, displayFontSize: 14, useSourceText: true)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private func readingKindTitle(_ kind: String) -> String {
    switch kind {
    case "table": "表格"
    case "formula": "公式"
    case "figure": "图片"
    case "code": "代码"
    case "heading": "标题"
    default: "段落"
    }
}
