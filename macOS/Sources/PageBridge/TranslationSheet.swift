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

    private let languages: [(String, String)] = [
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
                        ForEach(languages, id: \.0) { Text($0.1).tag($0.0) }
                    }.labelsHidden().frame(width: 175)
                }
                LabeledContent("目标语言") {
                    Picker("目标语言", selection: $targetLanguage) {
                        ForEach(languages, id: \.0) { Text($0.1).tag($0.0) }
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
            }
            .formStyle(.columns)

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
            if model.selectedDocument?.translatedURL != nil {
                Text("本次翻译成功后，将更新此文档的译文。原 PDF 会保留。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            HStack {
                Text("输出译文与双语 PDF").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("开始翻译", action: start)
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(!model.engineReady || model.activeService == nil || model.selectedDocument == nil || model.isTranslating || sourceLanguage == targetLanguage)
            }
        }
        .padding(26).frame(width: 490)
        .onAppear {
            sourceLanguage = model.selectedDocument?.sourceLanguage ?? "en"
            targetLanguage = model.selectedDocument?.targetLanguage ?? "zh"
        }
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
        model.startTranslation(sourceLanguage: sourceLanguage, targetLanguage: targetLanguage, pages: selectedRange == "custom" ? range : nil)
        dismiss()
    }
}
