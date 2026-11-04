import PageBridgeCore
import SwiftUI

/// Adds one term while reading and re-translates the paragraphs that contain it.
struct AddTermSheet: View {
    @ObservedObject var model: AppModel
    let request: AppModel.AddTermRequest
    @Environment(\.dismiss) private var dismiss
    @State private var source = ""
    @State private var target = ""
    @State private var glossaryID = ""
    @State private var retranslate = true
    @State private var error: String?

    private static let newGlossary = "new"

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("加入术语表").font(.title2.weight(.semibold))
            if !request.context.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("所在段落（可选中复制要固定的词）").font(.caption).foregroundStyle(.secondary)
                    ScrollView { Text(request.context).font(.callout).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(8) }
                        .frame(maxHeight: 110).background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 6))
                }
            }
            Form {
                TextField("原文", text: $source, prompt: Text("例如 servo drive"))
                TextField("译文", text: $target, prompt: Text("留空表示保留原文不译"))
                Picker("术语表", selection: $glossaryID) {
                    ForEach(model.writableGlossaries) { Text($0.name).tag($0.id) }
                    Divider()
                    Text("新建：\(documentGlossaryName)").tag(Self.newGlossary)
                }
                Toggle("立即重译阅读稿中包含这个术语的段落", isOn: $retranslate)
                    .disabled(model.selectedDocument?.readingPath == nil || model.isWorkerBusy)
            }
            .formStyle(.columns)
            Text("这个术语表会关联到当前文档，之后翻译和单段重试都会带上。英文按整词匹配（含常见词形变化），大写缩写区分大小写。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let error {
                Label(error, systemImage: "exclamationmark.circle.fill").font(.caption).foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(retranslate && model.selectedDocument?.readingPath != nil && !model.isWorkerBusy ? "加入并重译" : "加入", action: add)
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 480)
        .onAppear {
            source = request.source
            let linked = model.selectedDocument?.glossaryIDs ?? []
            glossaryID = model.writableGlossaries.first(where: { linked.contains($0.id) })?.id ?? Self.newGlossary
        }
    }

    private var documentGlossaryName: String {
        ((model.selectedDocument?.name ?? "文档") as NSString).deletingPathExtension + " 术语"
    }

    private func add() {
        do {
            let busy = model.isWorkerBusy || model.selectedDocument?.readingPath == nil
            try model.addTerm(source: source, target: target, glossaryID: glossaryID == Self.newGlossary ? nil : glossaryID,
                              retranslate: retranslate && !busy)
            dismiss()
        } catch { self.error = error.localizedDescription }
    }
}

/// Reviews terms the engine proposed for the current document before they are saved.
struct TermSuggestionsSheet: View {
    @ObservedObject var model: AppModel
    /// Where accepted terms go; nil makes a new glossary for the document.
    let initialGlossaryID: String?
    var onAdded: (String) -> Void = { _ in }
    @Environment(\.dismiss) private var dismiss
    @State private var items: [Item] = []
    @State private var loading = true
    @State private var message: String?
    @State private var glossaryID = ""
    @State private var task: Task<Void, Never>?

    struct Item: Identifiable { var suggestion: TermSuggestion; var target: String; var selected = true; var id: String { suggestion.id } }
    private static let newGlossary = "new"

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("从文档提取术语").font(.title2.weight(.semibold))
            Text("候选词在本机统计，再请所选模型挑出领域术语并给出常用译法。请逐条核对：取消勾选不需要的，修改不准确的译法；译文留空表示保留原文。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if loading {
                VStack(spacing: 10) { ProgressView(); Text("正在提取术语，长文档可能需要一两分钟…").foregroundStyle(.secondary) }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if items.isEmpty {
                Text(message ?? "没有找到新的候选术语。").foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                HStack {
                    Button("全选") { for index in items.indices { items[index].selected = true } }.controlSize(.small)
                    Button("全不选") { for index in items.indices { items[index].selected = false } }.controlSize(.small)
                    Spacer()
                    Text("已选 \(items.filter(\.selected).count) / \(items.count)").font(.caption).foregroundStyle(.secondary)
                }
                List($items) { $item in
                    HStack(spacing: 10) {
                        Toggle("", isOn: $item.selected).labelsHidden()
                        Text(item.suggestion.source).frame(width: 210, alignment: .leading).lineLimit(1)
                        TextField("保留原文", text: $item.target).textFieldStyle(.roundedBorder)
                        Text("\(item.suggestion.count) 次").font(.caption).foregroundStyle(.secondary).frame(width: 52, alignment: .trailing)
                    }
                }
                .listStyle(.inset(alternatesRowBackgrounds: true))
            }
            HStack {
                Picker("加入", selection: $glossaryID) {
                    ForEach(model.writableGlossaries) { Text($0.name).tag($0.id) }
                    Divider()
                    Text("新建：\(documentGlossaryName)").tag(Self.newGlossary)
                }
                .frame(width: 320)
                Spacer()
                Button("取消") { task?.cancel(); dismiss() }.keyboardShortcut(.cancelAction)
                Button("加入所选术语", action: add).buttonStyle(.borderedProminent)
                    .disabled(loading || !items.contains(where: \.selected))
            }
            if let message, !items.isEmpty {
                Label(message, systemImage: "exclamationmark.circle.fill").font(.caption).foregroundStyle(.red)
            }
        }
        .padding(22).frame(width: 680, height: 560)
        .onAppear {
            glossaryID = model.writableGlossaries.first(where: { $0.id == initialGlossaryID })?.id ?? Self.newGlossary
            let linked = model.glossaries.combinedText(model.selectedDocument?.glossaryIDs ?? [])
            let existing = [linked, model.glossaries.glossary(initialGlossaryID)?.text ?? ""].joined(separator: "\n")
            task = Task {
                do {
                    let terms = try await model.suggestTerms(excluding: existing)
                    items = terms.map { Item(suggestion: $0, target: $0.target ?? "") }
                } catch { message = error.localizedDescription }
                loading = false
            }
        }
    }

    private var documentGlossaryName: String {
        ((model.selectedDocument?.name ?? "文档") as NSString).deletingPathExtension + " 术语"
    }

    private func add() {
        let chosen = items.filter(\.selected)
        do {
            var target: String? = glossaryID == Self.newGlossary ? nil : glossaryID
            let document = model.selectedDocument
            let languages = (model.structuredReading?.sourceLanguage ?? document?.sourceLanguage, model.structuredReading?.targetLanguage ?? document?.targetLanguage)
            try model.changeGlossaries { library in
                if target == nil { target = try library.create(name: documentGlossaryName, sourceLanguage: languages.0, targetLanguage: languages.1).id }
                for item in chosen { try library.addEntry(target!, source: item.suggestion.source, target: item.target) }
            }
            if let target { model.linkGlossaryToSelectedDocument(target); onAdded(target) }
            dismiss()
        } catch { message = error.localizedDescription }
    }
}
