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

    private var messages: [PaperAssistantMessage] { model.messages(for: document) }
    private var previewID: String { "\(document.id)|\(document.inputSHA256)|\(page)|\(mode.rawValue)|\(selectionText.count)|\(String(selectionText.prefix(PaperAssistantLimits.selectionCharacters)))" }
    private var disabled: Bool {
        service == nil || model.isBusy || question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || question.count > PaperAssistantLimits.questionCharacters || (mode == .selection && selectionText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        if messages.isEmpty { welcome }
                        ForEach(messages) { message in messageView(message).id(message.id) }
                        if let error = model.errors[document.id] {
                            Label(error, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.orange).textSelection(.enabled)
                        }
                        if model.activeDocumentID == document.id {
                            HStack { ProgressView().controlSize(.small); Text(model.status).font(.caption).foregroundStyle(.secondary) }
                        }
                        Color.clear.frame(height: 1).id("assistant-bottom")
                    }.padding(16)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .onChange(of: messages.count) { _, _ in withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("assistant-bottom", anchor: .bottom) } }
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
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("文档助手", systemImage: "sparkles").font(.headline)
                Spacer()
                Button { confirmClear = true } label: { Image(systemName: "trash") }.buttonStyle(.plain).help("清空当前文档对话")
                    .disabled(messages.isEmpty && model.activeDocumentID != document.id && model.errors[document.id] == nil)
            }
            Text(document.name).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            Picker("提问范围", selection: $mode) { ForEach(PaperAssistantMode.allCases) { Text($0.title).tag($0) } }
                .pickerStyle(.menu)
            modelPicker
            if let service {
                if service.kind == .openAI { Text("点击发送时，选定内容将发送到 \(URL(string: service.baseURL)?.host ?? "所选服务")。") .font(.caption2).foregroundStyle(.secondary) }
            }
        }.padding(16)
    }
    private var modelPicker: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("助手模型").font(.caption).foregroundStyle(.secondary)
            if services.isEmpty {
                Text("请在设置的“翻译服务”中添加模型，助手可使用已配置的服务。")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            } else {
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
                    Text(service.map { $0.name == $0.provider.title ? $0.name : "\($0.name) · \($0.provider.title)" } ?? "选择已配置模型")
                        .lineLimit(1).truncationMode(.middle)
                }
                .menuStyle(.borderedButton).menuIndicator(.visible)
                .frame(maxWidth: .infinity, alignment: .leading)
                .disabled(model.isBusy)
                .help(service.map { "\($0.name) · \($0.provider.title) · \($0.model)" } ?? "选择已配置模型")
                .accessibilityLabel("助手模型")
                if let service {
                    Text(service.model)
                        .font(.caption).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .help(service.model)
                }
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("读懂文档，随时提问").font(.title3.weight(.semibold))
            Text("选择范围后再发送。回答可能出错；页码只表示模型收到了该页片段，展开片段可核对依据。")
                .font(.callout).foregroundStyle(.secondary)
            ForEach([("文档概述", "请根据提供的内容，概述这份文档的主题、用途和主要内容，并说明无法从现有片段确认的部分。"),
                     ("提炼要点", "请提炼文档中的重要信息、结论、要求或数据，按内容实际类型组织，并区分原文明示的信息与推断。"),
                     ("操作与注意事项", "请提取文档中明确的操作步骤、前提条件和注意事项。如果没有相关内容，请直接说明，不要编造步骤。"),
                     ("解释选文／公式", "请解释当前页或选中段落的含义；如有公式，请解释变量、假设和公式作用，不要猜测缺失符号。")], id: \.0) { title, prompt in
                Button { question = prompt; send(prompt) } label: {
                    HStack { Text(title); Spacer(); Image(systemName: "arrow.up.right") }.padding(10)
                }.buttonStyle(.bordered).disabled(service == nil || model.isBusy || (mode == .selection && selectionText.isEmpty))
            }
            Text("快捷问题会立即按当前范围发送。对话保存在本机，最多保留最近 40 条。")
                .font(.caption2).foregroundStyle(.secondary)
        }
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
        VStack(alignment: .leading, spacing: 8) {
            Text(scopeDescription).font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if mode == .selection && !selectionText.isEmpty {
                Text(String(selectionText.prefix(160))).font(.caption).foregroundStyle(.secondary).lineLimit(3)
            }
            TextField("询问文档内容、条款或操作步骤…", text: $question, axis: .vertical)
                .textFieldStyle(.plain).lineLimit(3...6).padding(10)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
            HStack {
                Text("\(question.count)/\(PaperAssistantLimits.questionCharacters)").font(.caption2).foregroundStyle(question.count > PaperAssistantLimits.questionCharacters ? .red : .secondary)
                if let notice { Text(notice).font(.caption2).foregroundStyle(.secondary).lineLimit(1) }
                Spacer()
                if model.isBusy {
                    Button("取消", role: .cancel) { model.cancel() }.controlSize(.small)
                }
                Button { send(question) } label: { Label("发送", systemImage: "arrow.up") }
                    .buttonStyle(.borderedProminent).disabled(disabled).keyboardShortcut(.return, modifiers: .command)
            }
            if model.isBusy && model.activeDocumentID != document.id { Text("另一份文档的回答仍在生成，结果会保存回原文档。") .font(.caption2).foregroundStyle(.secondary) }
        }.padding(12)
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
