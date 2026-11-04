import PageBridgeCore
import SwiftUI

/// Finds text in the originals and translations of every document in the library.
struct LibrarySearchView: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var hits: [LibrarySearchHit] = []
    @State private var searching = false
    @State private var searched = ""
    @State private var task: Task<Void, Never>?

    private var groups: [(UUID, String, [LibrarySearchHit])] {
        var order: [UUID] = [], grouped: [UUID: [LibrarySearchHit]] = [:]
        for hit in hits {
            if grouped[hit.documentID] == nil { order.append(hit.documentID) }
            grouped[hit.documentID, default: []].append(hit)
        }
        return order.map { ($0, grouped[$0]![0].documentName, grouped[$0]!) }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("在全部文档的原文和译文中搜索，例如报警码、参数名或术语", text: $query)
                    .textFieldStyle(.plain).font(.title3).onSubmit(run)
                if searching { ProgressView().controlSize(.small) }
                Button("搜索", action: run).disabled(query.trimmingCharacters(in: .whitespacesAndNewlines).count < 2)
            }
            .padding(14)
            Divider()
            if hits.isEmpty {
                Text(searched.isEmpty ? "输入至少 2 个字符后按回车。首次搜索会读取每份 PDF 的文字，之后会快很多。"
                                      : "没有找到“\(searched)”。")
                    .foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    ForEach(groups, id: \.0) { group in
                        Section("\(group.1)（\(group.2.count) 页）") {
                            ForEach(group.2) { hit in
                                Button { model.openDocument(hit.documentID, page: hit.page, translation: hit.inTranslation); dismiss() } label: {
                                    VStack(alignment: .leading, spacing: 3) {
                                        HStack(spacing: 6) {
                                            Text("第 \(hit.page) 页").font(.caption.weight(.semibold))
                                            Text(hit.inTranslation ? "译文" : "原文").font(.caption2)
                                                .padding(.horizontal, 5).padding(.vertical, 1)
                                                .background(hit.inTranslation ? Color.blue.opacity(0.14) : Color.secondary.opacity(0.14), in: Capsule())
                                        }
                                        Text(highlighted(hit)).font(.callout).lineLimit(3)
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
            }
            Divider()
            HStack {
                Text(hits.count >= LibrarySearch.maximumHits ? "只显示前 \(LibrarySearch.maximumHits) 处，请换更具体的词。" : hits.isEmpty ? "" : "共 \(hits.count) 处")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("关闭") { task?.cancel(); dismiss() }.keyboardShortcut(.cancelAction)
            }
            .padding(12)
        }
        .frame(width: 720, height: 540)
    }

    private func highlighted(_ hit: LibrarySearchHit) -> AttributedString {
        var text = AttributedString(hit.snippet)
        let characters = text.characters
        if hit.matchRange.upperBound <= characters.count {
            let start = characters.index(characters.startIndex, offsetBy: hit.matchRange.lowerBound)
            let end = characters.index(characters.startIndex, offsetBy: hit.matchRange.upperBound)
            text[start..<end].font = .callout.bold()
            text[start..<end].backgroundColor = .yellow.opacity(0.35)
        }
        return text
    }

    private func run() {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard needle.count >= 2 else { return }
        task?.cancel()
        let documents = model.documents.filter { !model.library.isTrashed(id: $0.id) }
        searching = true
        task = Task {
            let found = await Task.detached(priority: .userInitiated) { (try? LibrarySearch.search(needle, documents: documents)) ?? [] }.value
            guard !Task.isCancelled else { return }
            hits = found; searched = needle; searching = false
        }
    }
}
