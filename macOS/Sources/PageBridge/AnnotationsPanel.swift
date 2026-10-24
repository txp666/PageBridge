import SwiftUI
import PageBridgeCore

struct AnnotationsPanel: View {
    @ObservedObject var workspace: ReaderWorkspace
    var onNavigate: (Int) -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("批注与笔记").font(.headline)
                Spacer()
                Button { workspace.addNote() } label: { Image(systemName: "square.and.pencil") }
                    .help("在选区或当前页添加笔记").disabled(!workspace.hasDocument)
            }.padding()
            Picker("批注来源", selection: $workspace.selectedVariant) {
                ForEach(PDFDocumentVariant.allCases) { variant in Text(variant.title).tag(variant) }
            }.pickerStyle(.segmented).padding(.horizontal).padding(.bottom, 10)
            HStack {
                Button { workspace.highlightSelection() } label: { Image(systemName: "highlighter") }.help("高亮选中文字").disabled(!workspace.canAnnotateSelection)
                Button { workspace.underlineSelection() } label: { Image(systemName: "underline") }.help("为选中文字添加下划线").disabled(!workspace.canAnnotateSelection)
                Spacer()
                Button { workspace.toggleBookmark() } label: {
                    Image(systemName: workspace.bookmarkPageNumbers.contains(workspace.currentPage) ? "bookmark.fill" : "bookmark")
                }.help("切换当前页书签").disabled(!workspace.hasDocument)
            }.disabled(!workspace.canAnnotateSelection && !workspace.hasDocument).padding(.horizontal).padding(.bottom, 10)
            Divider()
            if workspace.currentAnnotations.isEmpty {
                ContentUnavailableView("还没有批注", systemImage: "text.bubble", description: Text("在 PDF 中选择文字以高亮或划线，也可以为当前页写笔记、添加书签。"))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(workspace.currentAnnotations) { item in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Button { workspace.navigate(to: item); onNavigate(item.page) } label: {
                                Label("第 \(item.page) 页 · \(item.kind.title)", systemImage: icon(item.kind))
                            }.buttonStyle(.plain).foregroundStyle(.tint)
                            Spacer()
                            Button { workspace.edit(item) } label: { Image(systemName: "pencil") }.buttonStyle(.borderless).help("编辑笔记")
                            Button(role: .destructive) { workspace.delete(item) } label: { Image(systemName: "trash") }.buttonStyle(.borderless).help("删除批注")
                        }
                        if !item.selectedText.isEmpty { Text(item.selectedText).font(.callout).foregroundStyle(.secondary).lineLimit(4).textSelection(.enabled) }
                        if !item.note.isEmpty { Text(item.note).font(.callout).textSelection(.enabled) }
                    }.padding(.vertical, 5)
                }.listStyle(.plain)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            HStack {
                Button("导出批注 PDF") { workspace.exportAnnotatedPDF() }
                Button("导出笔记") { workspace.exportNotes() }
            }.font(.callout).padding().disabled(!workspace.hasDocument)
        }
        .frame(minWidth: 260, maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
    private func icon(_ kind: ReadingAnnotationKind) -> String {
        switch kind { case .highlight: "highlighter"; case .underline: "underline"; case .note: "note.text"; case .bookmark: "bookmark.fill" }
    }
}
