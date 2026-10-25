import SwiftUI
import AppKit
import PageBridgeCore

struct PaperAssistantPanel: View {
    @ObservedObject var model: PaperAssistantModel
    let document: DocumentRecord
    let page: Int
    let selectionText: String
    let service: ServiceConfiguration?
    let services: [ServiceConfiguration]
    @Binding var selectedServiceID: UUID?
    let apiKey: (UUID) throws -> String
    let onNavigate: (Int) -> Void
    let onSaveNote: (String) -> Void
    @State private var mode: PaperAssistantMode = .paper
    @State private var question = ""
    @State private var confirmClear = false
    @State private var notice: String?
    @State private var feedbackHeight: CGFloat = 16

    private var messages: [PaperAssistantMessage] { model.messages(for: document) }
    private var previewID: String { "\(document.id)|\(document.inputSHA256)|\(page)|\(mode.rawValue)|\(selectionText.count)|\(String(selectionText.prefix(PaperAssistantLimits.selectionCharacters)))" }
    private var disabled: Bool {
        service == nil || model.isBusy || question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || question.count > PaperAssistantLimits.questionCharacters || (mode == .selection && selectionText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }
    private var quickQuestionsDisabled: Bool {
        service == nil || model.isBusy || (mode == .selection && selectionText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        if messages.isEmpty {
                            Text("从下方提问，开始了解这份文档。")
                                .font(.system(size: 12)).foregroundStyle(.secondary)
                        }
                        ForEach(messages) { message in messageView(message).id(message.id) }
                        Color.clear.frame(height: 1).id("assistant-bottom")
                    }.padding(12)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityIdentifier("AssistantMessages")
                .onChange(of: messages.count) { _, count in
                    guard count > 0 else { return }
                    withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("assistant-bottom", anchor: .bottom) }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            Divider()
            composer
        }
        .frame(minWidth: 300, idealWidth: 346, maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color(nsColor: .controlBackgroundColor))
        .task(id: previewID) { await model.preview(document: document, mode: mode, page: page, selection: selectionText) }
        .onChange(of: document.id) { _, _ in question = ""; notice = nil; confirmClear = false }
        .confirmationDialog("清空这份文档的助手对话？", isPresented: $confirmClear, titleVisibility: .visible) {
            Button("清空当前对话", role: .destructive) { model.clear(document: document) }
        } message: { Text("只删除这份文档的对话记录，不删除 PDF 或阅读笔记。") }
    }
    private var header: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text("文档助手").font(.system(size: 14, weight: .semibold))
                Spacer()
                Button { confirmClear = true } label: { Image(systemName: "trash").frame(width: 24, height: 24) }.buttonStyle(.plain).help("清空当前文档对话")
                    .accessibilityLabel("清空当前文档对话")
                    .disabled(messages.isEmpty && model.activeDocumentID != document.id && model.errors[document.id] == nil)
            }
            Text(document.name).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1).help(document.name)
        }.padding(.horizontal, 12).padding(.vertical, 9)
            .accessibilityIdentifier("AssistantHeader")
    }
    private var modelPicker: some View {
        Menu {
            ForEach(services) { configured in
                Button {
                    selectedServiceID = configured.id
                } label: {
                    let title = "\(configured.name) · \(configured.provider.title) · \(configured.model)"
                    if configured.id == service?.id {
                        Label(title, systemImage: "checkmark")
                    } else {
                        Text(title)
                    }
                }
            }
        } label: {
            Text(service.map { "\($0.name) · \($0.model)" } ?? "请先配置模型")
                .lineLimit(1).truncationMode(.middle)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .menuStyle(.borderedButton).menuIndicator(.visible).controlSize(.small)
        .font(.system(size: 12)).frame(maxWidth: .infinity, alignment: .leading)
        .disabled(model.isBusy || services.isEmpty)
        .help(modelHelp).accessibilityLabel("助手模型").accessibilityHint(modelHelp)
        .accessibilityIdentifier("AssistantModel")
    }
    private var modelHelp: String {
        guard let service else { return "请在翻译服务设置中添加模型。" }
        let destination = service.kind == .ollama ? "使用本机 Ollama；模型不随应用打包。"
            : "点击发送时，所选片段将发送至 \(URL(string: service.baseURL)?.host ?? "所选服务")。"
        return "\(service.name) · \(service.provider.title) · \(service.model)\n\(destination)"
    }
    private func messageView(_ message: PaperAssistantMessage) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(message.role == .user ? "你" : "文档助手").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Spacer()
                if message.role == .assistant {
                    Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(message.text, forType: .string); notice = "回答已复制" } label: { Image(systemName: "doc.on.doc") }.buttonStyle(.plain).help("复制回答")
                    Button { onSaveNote(noteText(message)); notice = "已交给阅读笔记" } label: { Image(systemName: "square.and.pencil") }.buttonStyle(.plain).help("保存到阅读笔记")
                }
            }
            // Plain text: model-generated URLs/page markup never become automatic navigation actions.
            Text(message.text).font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            ForEach(message.citations) { evidence in
                DisclosureGroup {
                    Text(evidence.text).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                    if evidence.truncated { Text("发送时已截取；不是完整页面。") .font(.caption2).foregroundStyle(.secondary) }
                } label: {
                    HStack {
                        Button("P\(evidence.page)") { if (1...document.pageCount).contains(evidence.page) { onNavigate(evidence.page) } }
                            .buttonStyle(.bordered).controlSize(.small)
                        Text(evidence.origin == .selection ? "对应选文 · 点击展开核对" : "对应原文片段 · 点击展开核对").font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            if !message.unverifiedPages.isEmpty {
                Text("未提供的页码引用：\(message.unverifiedPages.map { "P\($0)" }.joined(separator: "、"))，无法核对，未提供跳转。")
                    .font(.caption).foregroundStyle(.orange)
            }
        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .background(message.role == .user ? Color.accentColor.opacity(0.08) : Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 10))
    }
    private var composer: some View {
        VStack(alignment: .leading, spacing: 7) {
            GeometryReader { geometry in
                HStack(spacing: 6) {
                    modelPicker.frame(width: max(0, geometry.size.width - 6) * 1.45 / 2.45)
                    Picker("提问范围", selection: $mode) { ForEach(PaperAssistantMode.allCases) { Text($0.title).tag($0) } }
                        .labelsHidden().pickerStyle(.menu).controlSize(.small).font(.system(size: 12))
                        .frame(maxWidth: .infinity)
                        .help(scopeDescription).accessibilityLabel("提问范围").accessibilityHint(scopeDescription)
                        .accessibilityIdentifier("AssistantScope")
                }.frame(height: 28)
            }.frame(height: 28)
            if mode == .selection && !selectionText.isEmpty {
                Text("已选择 \(selectionText.count.formatted()) 字").font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
                    .help(String(selectionText.prefix(600)) + (selectionText.count > 600 ? "…" : ""))
            }
            TextField("询问文档内容、条款或操作步骤…", text: $question, axis: .vertical)
                .font(.system(size: 12)).textFieldStyle(.plain).lineLimit(3...6).frame(minHeight: 48).padding(8)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.secondary.opacity(0.18), lineWidth: 1).allowsHitTesting(false))
                .help("⌘Return 发送。回答可能出错，请核对引用片段；页码只表示模型收到该页片段。对话保存在本机，最多保留最近 40 条。")
                .accessibilityLabel("助手问题").accessibilityIdentifier("AssistantQuestion")
            feedback
            HStack(spacing: 8) {
                quickQuestions
                Spacer()
                Text("\(question.count.formatted())/\(PaperAssistantLimits.questionCharacters.formatted())").font(.system(size: 10)).foregroundStyle(question.count > PaperAssistantLimits.questionCharacters ? .red : .secondary)
                if model.isBusy {
                    Button("取消", role: .cancel) { model.cancel() }.controlSize(.small).accessibilityIdentifier("AssistantCancel")
                }
                Button("发送") { send(question) }
                    .buttonStyle(.borderedProminent).controlSize(.small).disabled(disabled).keyboardShortcut(.return, modifiers: .command)
                    .accessibilityIdentifier("AssistantSend")
            }
        }.padding(.horizontal, 12).padding(.vertical, 10)
            .accessibilityIdentifier("AssistantComposer")
    }
    private var quickQuestions: some View {
        Menu("快捷提问") {
            ForEach([("文档概述", "请根据提供的内容，概述文档的主题、用途和主要内容，并说明无法从现有片段确认的部分。"),
                     ("提炼要点", "请提炼文档的重要信息、结论、要求或数据，并区分原文明示的信息与推断。"),
                     ("操作与注意事项", "请提取文档中明确的操作步骤、前提条件和注意事项。如果没有相关内容，请直接说明。"),
                     ("解释选文／公式", "请解释当前页或选中段落的含义；如有公式，请解释变量、假设和公式作用，不要猜测缺失符号。")], id: \.0) { title, prompt in
                Button(title) { question = prompt; send(prompt) }
            }
        }.menuStyle(.borderedButton).menuIndicator(.visible).controlSize(.small)
            .fixedSize(horizontal: true, vertical: false)
            .disabled(quickQuestionsDisabled)
            .help("选择问题后，立即使用当前模型和范围发送。")
            .accessibilityHint("选择问题后立即发送，不会先填入输入框。")
            .accessibilityIdentifier("AssistantQuickQuestions")
    }
    @ViewBuilder private var feedback: some View {
        if model.errors[document.id] != nil || model.isBusy || notice != nil {
            ScrollView {
                VStack(alignment: .leading, spacing: 3) {
                    if let error = model.errors[document.id] {
                        Text(error).foregroundStyle(.orange).textSelection(.enabled)
                    }
                    if model.activeDocumentID == document.id {
                        HStack(spacing: 6) { ProgressView().controlSize(.mini); Text(model.status).foregroundStyle(.secondary) }
                    } else if model.isBusy {
                        Text("另一份文档的回答仍在生成，结果会保存回原文档。").foregroundStyle(.secondary)
                    }
                    if let notice { Text(notice).foregroundStyle(.secondary) }
                }.font(.system(size: 11)).frame(maxWidth: .infinity, alignment: .leading)
                    .background(GeometryReader { geometry in
                        Color.clear.preference(key: PaperAssistantFeedbackHeight.self, value: geometry.size.height)
                    })
            }.frame(height: min(56, max(16, feedbackHeight)))
                .onPreferenceChange(PaperAssistantFeedbackHeight.self) { feedbackHeight = $0 }
                .accessibilityIdentifier("AssistantStatus")
        }
    }
    private var scopeDescription: String {
        if let context = model.contexts[document.id], context.mode == mode { return context.scopeDescription + (context.evidence.contains(where: \.truncated) || context.extractionLimited ? " 内容已限长。" : "") }
        switch mode {
        case .paper: return "本机检索最多 500 页、24 万字符，发送最多 6 页片段、12,000 字符；含当前页及相关内容。附带最近 10 条、最多 8,000 字符对话。"
        case .currentPage: return "仅发送当前原文页的前部或相关片段（最多 2,400 字符）与最近对话。"
        case .selection: return "选择文字后点击发送；最多发送 6,000 字符选文及最近对话，不自动上传。"
        case .freeQuestion: return "不提取 PDF；只发送问题与最近对话，对话可能含此前文档内容。"
        }
    }
    private func send(_ text: String) {
        guard let service, !model.isBusy else { return }
        let started = model.send(question: text, document: document, mode: mode, page: page, selection: selectionText,
                   service: service, apiKey: { try apiKey(service.id) })
        if started { question = ""; notice = nil }
    }
    private func noteText(_ message: PaperAssistantMessage) -> String {
        let pages = message.citations.map { "P\($0.page)" }.joined(separator: "、")
        return "AI 文档助手\(pages.isEmpty ? "" : " · \(pages)")\n\n\(message.text)\n\nAI 生成，请对照原文核实。"
    }
}

private struct PaperAssistantFeedbackHeight: PreferenceKey {
    static var defaultValue: CGFloat = 16
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}
