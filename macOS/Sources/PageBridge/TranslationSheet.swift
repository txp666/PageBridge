import PageBridgeCore
import SwiftUI

struct TranslationSheet: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var sourceLanguage = "en"
    @State private var targetLanguage = "zh"
    @State private var selectedRange = "all"
    @State private var pages = ""
    @State private var pageError: String?
    @State private var glossaryIDs: [String] = []
    @State private var glossaryManagerPresented = false
    @State private var append = true
    @State private var translateTables = false

    static let languages: [(String, String)] = [
        ("en", "英语"), ("zh", "简体中文"), ("zh-TW", "繁体中文"),
        ("ja", "日语"), ("ko", "韩语"), ("fr", "法语"),
        ("de", "德语"), ("es", "西班牙语"), ("ru", "俄语")
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "character.book.closed.fill")
                    .font(.system(size: 29)).foregroundStyle(.blue)
                    .frame(width: 52, height: 52).background(.blue.opacity(0.09), in: RoundedRectangle(cornerRadius: 12))
                VStack(alignment: .leading, spacing: 5) {
                    Text("翻译 PDF").font(.title2.weight(.semibold))
                    Text(model.selectedDocument?.name ?? "请选择文档")
                        .font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
            }
            Form {
                LabeledContent("源语言") {
                    Picker("源语言", selection: $sourceLanguage) {
                        ForEach(Self.languages, id: \.0) { Text($0.1).tag($0.0) }
                    }.labelsHidden().frame(width: 175)
                }
                LabeledContent("目标语言") {
                    Picker("目标语言", selection: $targetLanguage) {
                        ForEach(Self.languages, id: \.0) { Text($0.1).tag($0.0) }
                    }.labelsHidden().frame(width: 175)
                }
                LabeledContent("翻译范围") {
                    Picker("翻译范围", selection: $selectedRange) {
                        Text("全部页面").tag("all")
                        Text("指定页面").tag("custom")
                    }.labelsHidden().frame(width: 175)
                }
                if selectedRange == "custom" {
                    LabeledContent("页码") {
                        TextField("例如 1-5, 8, 12-16", text: $pages)
                            .textFieldStyle(.roundedBorder).frame(width: 235)
                    }
                    Text("请输入原文页码，用逗号分隔；共 \(model.selectedDocument?.pageCount ?? 0) 页。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if canAppend {
                    Toggle("追加到现有阅读稿（保留已译的 \(draftPages.count) 页）", isOn: $append)
                }
                Toggle("自动翻译表格", isOn: appending ? .constant(draft?.translateTables ?? true) : $translateTables)
                    .disabled(appending)
                LabeledContent("术语表") {
                    HStack(spacing: 8) {
                        Menu {
                            Button("不使用术语表") { glossaryIDs = [] }
                            let own = applicable.filter { !$0.isBuiltIn }, builtIn = applicable.filter(\.isBuiltIn)
                            if !own.isEmpty {
                                Section("我的术语表") { ForEach(own) { toggle($0) } }
                            }
                            if !builtIn.isEmpty {
                                Section("内置") { ForEach(builtIn) { toggle($0) } }
                            }
                        } label: { Text(glossarySummary).lineLimit(1) }
                        .frame(width: 235)
                        Button("管理…") { glossaryManagerPresented = true }.buttonStyle(.link)
                    }
                }
                if hiddenGlossaries > 0 {
                    Text("\(hiddenGlossaries) 个术语表的语言对不适用于当前语言，已隐藏。可选多个，同一术语以“我的术语表”为准。")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("可选多个，同一术语以“我的术语表”为准。").font(.caption).foregroundStyle(.secondary)
                }
            }
            .formStyle(.columns)
            Text("译文按原页位置显示，图表、公式和代码保留原区域；过长的文字可点击展开。扫描页会在本机识别。首次使用会下载本地识别组件，请预留约 4 GB 磁盘空间。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Label("翻译服务", systemImage: "network").font(.subheadline.weight(.medium))
                    Spacer()
                    Button("更改…") {
                        dismiss()
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { model.settingsPresented = true }
                    }.buttonStyle(.link)
                }
                Text(serviceName).font(.body)
                HStack(spacing: 6) {
                    Image(systemName: model.engineReady ? "checkmark.circle.fill" : "exclamationmark.circle")
                        .foregroundStyle(model.engineReady ? .green : .orange)
                    Text(model.engineDescription).lineLimit(2)
                }.font(.caption).foregroundStyle(.secondary)
                Text("PDF 在本机处理。使用在线 API 时，待翻译文本会发送至所选服务；本机服务需在本机运行。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(14).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))

            if let pageError {
                Label(pageError, systemImage: "exclamationmark.circle.fill").font(.caption).foregroundStyle(.red)
            }
            if sourceLanguage == targetLanguage {
                Text("源语言和目标语言需要不同。").font(.caption).foregroundStyle(.orange)
            }
            if appending {
                Text("只翻译尚未翻译的页，并合并进现有阅读稿，沿用阅读稿的语言和表格设置。")
                    .font(.caption).foregroundStyle(.secondary)
            } else if model.selectedDocument?.hasTranslation == true {
                Text("本次翻译成功后，将用这次的页范围替换此文档的阅读稿。原 PDF 会保留。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            HStack {
                Text("保留原页版式，可点击全文与逐段重试").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("开始翻译", action: start)
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(!model.engineReady || model.activeService == nil || model.selectedDocument == nil || model.isWorkerBusy || sourceLanguage == targetLanguage)
            }
        }
        .padding(26).frame(width: 490)
        .sheet(isPresented: $glossaryManagerPresented, onDismiss: dropUnavailableGlossaries) {
            GlossaryManagerView(model: model, initialSelection: glossaryIDs.first)
        }
        .onAppear {
            sourceLanguage = model.selectedDocument?.sourceLanguage ?? "en"
            targetLanguage = model.selectedDocument?.targetLanguage ?? "zh"
            glossaryIDs = model.selectedDocument?.glossaryIDs ?? model.glossaries.lastUsedIDs
            dropUnavailableGlossaries()
        }
        .onChange(of: sourceLanguage) { _, _ in dropUnavailableGlossaries() }
        .onChange(of: targetLanguage) { _, _ in dropUnavailableGlossaries() }
        .onChange(of: appending) { _, value in
            // Appended pages must use the draft's languages.
            if value, let reading = draft { sourceLanguage = reading.sourceLanguage; targetLanguage = reading.targetLanguage }
        }
    }

    private var draft: StructuredReadingDocument? {
        model.selectedDocument?.readingPath == nil ? nil : model.structuredReading
    }
    private var draftPages: [Int] { draft?.selectedPages ?? [] }
    private var canAppend: Bool { selectedRange == "custom" && draft != nil }
    private var appending: Bool { canAppend && append }

    private var applicable: [Glossary] { model.glossaries.all.filter { $0.applies(source: sourceLanguage, target: targetLanguage) } }
    private var hiddenGlossaries: Int { model.glossaries.all.count - applicable.count }
    private var glossarySummary: String {
        let names = glossaryIDs.compactMap { model.glossaries.glossary($0)?.name }
        if names.isEmpty { return "不使用" }
        return names.count == 1 ? names[0] : "\(names[0]) 等 \(names.count) 个"
    }
    private func toggle(_ glossary: Glossary) -> some View {
        Toggle(glossary.name, isOn: Binding(
            get: { glossaryIDs.contains(glossary.id) },
            set: { on in
                glossaryIDs.removeAll { $0 == glossary.id }
                if on { glossaryIDs.append(glossary.id) }
            }))
    }
    private func dropUnavailableGlossaries() {
        let available = Set(applicable.map(\.id))
        glossaryIDs = glossaryIDs.filter { available.contains($0) }
    }

    private var serviceName: String {
        // Service settings owns provider details; use its user-facing name here.
        model.activeService?.name ?? "尚未配置服务，请先在设置中添加 API"
    }

    private func start() {
        pageError = nil
        let range = pages.trimmingCharacters(in: .whitespacesAndNewlines)
        if selectedRange == "custom" {
            guard !range.isEmpty else { pageError = "请输入要翻译的页码。"; return }
            let maximum = model.selectedDocument?.pageCount ?? 0
            for component in range.split(separator: ",", omittingEmptySubsequences: false) {
                let pair = component.trimmingCharacters(in: .whitespaces).split(separator: "-", omittingEmptySubsequences: false)
                guard (1...2).contains(pair.count),
                      let first = Int(pair[0]), first >= 1, first <= maximum,
                      let last = Int(pair.last!), last >= first, last <= maximum else {
                    pageError = "页码格式不正确。请输入 1 至 \(maximum) 之间的页码，例如 1-5, 8。"
                    return
                }
            }
        }
        if appending {
            let existing = Set(draftPages)
            let wanted = range.split(separator: ",").flatMap { part -> [Int] in
                let pair = part.trimmingCharacters(in: .whitespaces).split(separator: "-").compactMap { Int($0) }
                return pair.isEmpty ? [] : Array(pair[0]...(pair.last ?? pair[0]))
            }
            guard wanted.contains(where: { !existing.contains($0) }) else { pageError = "这些页已在阅读稿中，请选择尚未翻译的页。"; return }
        }
        model.startTranslation(sourceLanguage: sourceLanguage, targetLanguage: targetLanguage, pages: selectedRange == "custom" ? range : nil,
                               translateTables: appending ? nil : translateTables, glossaryIDs: glossaryIDs,
                               scope: appending ? .append : .full)
        dismiss()
    }
}
