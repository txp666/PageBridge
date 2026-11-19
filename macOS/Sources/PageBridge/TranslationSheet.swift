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
    @State private var translateTables = true
    @State private var automaticallyContinue = false
    @State private var parseWorkers = 0
    private static let batchLimit = AppModel.translationBatchLimit

    static let languages: [(String, String)] = [
        ("en", "英语"), ("zh", "简体中文"), ("zh-TW", "繁体中文"),
        ("ja", "日语"), ("ko", "韩语"), ("fr", "法语"),
        ("de", "德语"), ("es", "西班牙语"), ("ru", "俄语")
    ]

    var body: some View {
        VStack(spacing: 0) {
            header
                .padding(.horizontal, 24).padding(.vertical, 18)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    translationOptions
                    serviceCard
                    hint("首次使用会下载本地识别组件，请预留约 4 GB 磁盘空间。")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 24).padding(.vertical, 16)
            }
            .frame(maxHeight: 480)
            Divider()
            footer
                .padding(.horizontal, 24).padding(.vertical, 16)
        }
        .frame(width: 560)
        .sheet(isPresented: $glossaryManagerPresented, onDismiss: dropUnavailableGlossaries) {
            GlossaryManagerView(model: model, initialSelection: glossaryIDs.first)
        }
        .onAppear {
            sourceLanguage = model.selectedDocument?.sourceLanguage ?? "en"
            targetLanguage = model.selectedDocument?.targetLanguage ?? "zh"
            glossaryIDs = model.selectedDocument?.glossaryIDs ?? model.glossaries.lastUsedIDs
            if pageCount > Self.batchLimit {
                selectedRange = "custom"
                if let nextBatch { selectBatch(nextBatch) }
                else if draftPages.count == pageCount { append = false }
            }
            dropUnavailableGlossaries()
        }
        .onChange(of: sourceLanguage) { _, _ in dropUnavailableGlossaries() }
        .onChange(of: targetLanguage) { _, _ in dropUnavailableGlossaries() }
        .onChange(of: appending) { _, value in
            // Appended pages must use the draft's languages.
            if value, let reading = draft { sourceLanguage = reading.sourceLanguage; targetLanguage = reading.targetLanguage }
        }
    }

    private static let labelWidth: CGFloat = 116
    private static let fieldGap: CGFloat = 14
    private static let pickerWidth: CGFloat = 204

    private var header: some View {
        HStack(spacing: 13) {
            Image(systemName: "character.book.closed.fill")
                .font(.system(size: 25)).foregroundStyle(.blue)
                .frame(width: 44, height: 44).background(.blue.opacity(0.09), in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text("翻译 PDF").font(.title2.weight(.semibold))
                Text(model.selectedDocument?.name ?? "请选择文档")
                    .font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 0)
        }
    }

    private var translationOptions: some View {
        VStack(alignment: .leading, spacing: 9) {
            row("源语言") {
                Picker("源语言", selection: $sourceLanguage) {
                    ForEach(Self.languages, id: \.0) { Text($0.1).tag($0.0) }
                }.labelsHidden().frame(width: Self.pickerWidth, alignment: .leading).disabled(appending)
            }
            row("目标语言") {
                Picker("目标语言", selection: $targetLanguage) {
                    ForEach(Self.languages, id: \.0) { Text($0.1).tag($0.0) }
                }.labelsHidden().frame(width: Self.pickerWidth, alignment: .leading).disabled(appending)
            }
            row("翻译范围") {
                Picker("翻译范围", selection: $selectedRange) {
                    Text("全部页面").tag("all").disabled(pageCount > Self.batchLimit)
                    Text("指定页面").tag("custom")
                }.labelsHidden().frame(width: Self.pickerWidth, alignment: .leading)
            }
            if selectedRange == "custom" {
                row("页码") {
                    TextField("页码", text: $pages, prompt: Text("例如 1-5, 8, 12-16"))
                        .textFieldStyle(.roundedBorder).frame(width: Self.pickerWidth)
                        .accessibilityLabel("页码")
                }
                aligned {
                    VStack(alignment: .leading, spacing: 6) {
                        hint(pageCount > Self.batchLimit
                             ? "原文共 \(pageCount) 页，每批最多 \(Self.batchLimit) 页。"
                             : "请输入原文页码，用逗号分隔。本文件共 \(pageCount) 页。")
                        if let nextBatch {
                            Button("选下一批（\(batchText(nextBatch))）") { selectBatch(nextBatch) }
                                .buttonStyle(.link)
                        }
                    }
                }
            }
            if canAppend {
                aligned { Toggle("追加到现有阅读稿（保留已处理的 \(draftPages.count) 页）", isOn: $append) }
            }
            if pageCount > Self.batchLimit {
                aligned { Toggle("自动完成后续未译页", isOn: $automaticallyContinue) }
                if automaticallyContinue {
                    aligned { hint("每批保存后继续。未译段落可在完成后单独重试。") }
                }
            }
            row("MinerU 并行解析") {
                Picker("MinerU 并行解析", selection: $parseWorkers) {
                    Text("自动（最多 3 路）").tag(0)
                    ForEach(1...8, id: \.self) { count in Text("\(count) 路").tag(count) }
                }.labelsHidden().frame(width: Self.pickerWidth, alignment: .leading)
                .help("设置 MinerU 同时解析的最大页数。")
            }
            aligned {
                hint("Apple M5 Pro 样本实测，1–3 路中 3 路最快。公式密集时可按文档试用更多路数。")
            }
            aligned {
                Toggle("自动翻译表格", isOn: appending ? .constant(draft?.translateTables ?? true) : $translateTables)
                    .disabled(appending)
            }
            row("术语表") {
                HStack(spacing: 8) {
                    Menu {
                        Button("不使用术语表") { glossaryIDs = [] }
                        let own = applicable.filter { !$0.isBuiltIn }, builtIn = applicable.filter(\.isBuiltIn)
                        if !own.isEmpty { Section("我的术语表") { ForEach(own) { toggle($0) } } }
                        if !builtIn.isEmpty { Section("内置") { ForEach(builtIn) { toggle($0) } } }
                    } label: { Text(glossarySummary).lineLimit(1) }
                    .frame(width: Self.pickerWidth, alignment: .leading)
                    .accessibilityLabel("术语表")
                    Button("管理…") { glossaryManagerPresented = true }.buttonStyle(.link)
                }
            }
            aligned {
                let unavailable = hiddenGlossaries > 0 ? "已隐藏 \(hiddenGlossaries) 个语言不匹配的术语表。" : ""
                hint(unavailable + "可选多个，同一术语以“我的术语表”为准。")
            }
        }
    }

    private var serviceCard: some View {
        VStack(alignment: .leading, spacing: 8) {
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
                Text(model.engineDescription).fixedSize(horizontal: false, vertical: true)
            }.font(.caption).foregroundStyle(.secondary)
            hint("PDF 在本机解析。使用在线 API 时，待翻译文本会发送至所选服务。")
        }
        .padding(14).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let pageError {
                Label(pageError, systemImage: "exclamationmark.circle.fill")
                    .font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
            if sourceLanguage == targetLanguage {
                Text("源语言和目标语言需要不同。").font(.caption).foregroundStyle(.orange)
            }
            if appending {
                hint("追加页沿用现有阅读稿的语言和表格设置。")
            } else if model.selectedDocument?.hasTranslation == true {
                hint("翻译成功后，本次所选页将替换此文档的阅读稿。")
            }
            HStack {
                if draft != nil {
                    Button("重新排版已有译文", action: model.rebuildReadingLayout)
                        .disabled(!model.engineReady || model.isWorkerBusy || model.isClearingCache)
                        .help("用已保存的译文在本机重新生成原页版式。")
                }
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("开始翻译", action: start)
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(!model.engineReady || model.activeService == nil || model.selectedDocument == nil
                              || model.isWorkerBusy || model.isClearingCache || sourceLanguage == targetLanguage)
            }
        }
    }

    private func row<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: Self.fieldGap) {
            Text(title).frame(width: Self.labelWidth, alignment: .leading).accessibilityHidden(true)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func aligned<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content()
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.leading, Self.labelWidth + Self.fieldGap)
    }

    private func hint(_ text: String) -> some View {
        Text(text).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
    }

    private var draft: StructuredReadingDocument? {
        model.selectedDocument?.readingPath == nil ? nil : model.structuredReading
    }
    private var draftPages: [Int] { draft?.selectedPages ?? [] }
    private var pageCount: Int { model.selectedDocument?.pageCount ?? 0 }
    private var canAppend: Bool { selectedRange == "custom" && draft != nil }
    private var appending: Bool { canAppend && append }

    private var nextBatch: ClosedRange<Int>? {
        guard pageCount > Self.batchLimit, model.selectedDocument?.hasTranslation != true || draft != nil else { return nil }
        let existing = Set(draftPages)
        guard let first = (1...pageCount).first(where: { !existing.contains($0) }) else { return nil }
        let end = min(pageCount, first + Self.batchLimit - 1)
        let last = (first...end).first(where: { existing.contains($0) }).map { $0 - 1 } ?? end
        return first...last
    }

    private func batchText(_ batch: ClosedRange<Int>) -> String {
        batch.lowerBound == batch.upperBound ? String(batch.lowerBound) : "\(batch.lowerBound)-\(batch.upperBound)"
    }

    private func selectBatch(_ batch: ClosedRange<Int>) {
        selectedRange = "custom"
        append = true
        pages = batchText(batch)
        pageError = nil
    }

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
        let batchError = "每批最多翻译 \(Self.batchLimit) 页，请选择页码分批翻译。"
        if selectedRange == "custom" {
            guard !range.isEmpty else { pageError = "请输入要翻译的页码。"; return }
            let existing = appending ? Set(draftPages) : []
            var wanted = Set<Int>()
            for component in range.split(separator: ",", omittingEmptySubsequences: false) {
                let pair = component.trimmingCharacters(in: .whitespaces).split(separator: "-", omittingEmptySubsequences: false)
                guard (1...2).contains(pair.count),
                      let first = Int(pair[0]), first >= 1, first <= pageCount,
                      let last = Int(pair.last!), last >= first, last <= pageCount else {
                    pageError = "页码格式不正确。请输入 1 至 \(pageCount) 之间的页码，例如 1-5, 8。"
                    return
                }
                for number in first...last where !existing.contains(number) {
                    wanted.insert(number)
                    if wanted.count > Self.batchLimit { pageError = batchError; return }
                }
            }
            guard !wanted.isEmpty else { pageError = "这些页已在阅读稿中，请选择尚未翻译的页。"; return }
        } else if pageCount > Self.batchLimit {
            pageError = batchError
            return
        }
        model.startTranslation(sourceLanguage: sourceLanguage, targetLanguage: targetLanguage, pages: selectedRange == "custom" ? range : nil,
                               translateTables: appending ? nil : translateTables, parseWorkers: parseWorkers == 0 ? nil : parseWorkers,
                               glossaryIDs: glossaryIDs,
                               scope: appending ? .append : .full, automaticallyContinue: automaticallyContinue)
        dismiss()
    }
}
