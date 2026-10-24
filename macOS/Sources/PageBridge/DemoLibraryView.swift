import AppKit
import SwiftUI

private struct DemoCatalog: Decodable {
    var papers: [DemoPaper]
}

private struct DemoPaper: Decodable, Identifiable {
    var id: String
    var title: String
    var originalTitle: String
    var description: String
    var file: String
    var pages: Int
    var sourceURL: String
}

struct DemoLibraryView: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var papers: [DemoPaper] = []
    @State private var loadError: String?

    private var folder: URL? { Bundle.main.resourceURL?.appendingPathComponent("demos", isDirectory: true) }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("示例 PDF").font(.title2.bold())
                    Text("从短文开始，也可以试读带公式和图表的 PDF。")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            ScrollView {
                VStack(spacing: 12) {
                    ForEach(papers) { paper in
                        HStack(alignment: .top, spacing: 14) {
                            Image(systemName: "doc.text").font(.title).foregroundStyle(.blue).frame(width: 32)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(paper.title).font(.headline)
                                Text(paper.originalTitle).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                                Text("\(paper.pages) 页 · \(paper.description)").font(.caption)
                                if let url = URL(string: paper.sourceURL) {
                                    Link("原文与许可", destination: url).font(.caption)
                                }
                            }
                            Spacer(minLength: 12)
                            Button("打开示例") { open(paper) }
                                .accessibilityLabel("打开示例：\(paper.title)")
                        }
                        .padding(15)
                        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                    }
                }
            }
            if let loadError { Text(loadError).foregroundStyle(.red).font(.callout) }
            Text("打开后添加到你的文档库。选择翻译服务并开始翻译时，才会调用模型。")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(24).frame(width: 680, height: 620)
        .onAppear(perform: load)
    }

    private func load() {
        do {
            guard let folder else { throw CocoaError(.fileNoSuchFile) }
            let data = try Data(contentsOf: folder.appendingPathComponent("catalog.json"))
            papers = try JSONDecoder().decode(DemoCatalog.self, from: data).papers
        } catch { loadError = "示例文件未安装，请使用完整的 PageBridge 应用。" }
    }

    private func open(_ paper: DemoPaper) {
        guard let folder else { return }
        let url = folder.appendingPathComponent(paper.file).standardizedFileURL
        guard url.path.hasPrefix(folder.standardizedFileURL.path + "/"),
              url.pathExtension.lowercased() == "pdf",
              FileManager.default.fileExists(atPath: url.path) else {
            loadError = "找不到这份示例 PDF。"
            return
        }
        model.importURLs([url])
        dismiss()
    }
}
