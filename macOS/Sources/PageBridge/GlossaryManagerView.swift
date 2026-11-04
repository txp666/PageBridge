import AppKit
import PageBridgeCore
import SwiftUI
import UniformTypeIdentifiers

/// Lists built-in and own glossaries; own ones can be created, copied, edited, deleted, imported and exported.
struct GlossaryManagerView: View {
    @ObservedObject var model: AppModel
    var initialSelection: String?
    @Environment(\.dismiss) private var dismiss
    @State private var selection: String?
    @State private var loadedID: String?
    @State private var name = ""
    @State private var text = ""
    @State private var sourceLanguage = ""
    @State private var targetLanguage = ""
    @State private var error: String?
    @State private var confirmDelete = false
    @State private var suggesting = false

    private var loaded: Glossary? { model.glossaries.glossary(loadedID) }
    private var dirty: Bool {
        guard let loaded, !loaded.isBuiltIn else { return false }
        return loaded.name != name || loaded.text != text
            || (loaded.sourceLanguage ?? "") != sourceLanguage || (loaded.targetLanguage ?? "") != targetLanguage
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                sidebar.frame(width: 250)
                Divider()
                editor.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            HStack(spacing: 10) {
                if let error {
                    Label(error, systemImage: "exclamationmark.circle.fill").foregroundStyle(.red).lineLimit(2)
                } else {
                    Text(loaded?.isBuiltIn == true ? "内置术语表不能修改；点“复制”生成可编辑的副本。" : "修改在切换术语表或点“完成”时保存。")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("完成") { if commit() { dismiss() } }.keyboardShortcut(.defaultAction)
            }
            .font(.caption).padding(.horizontal, 16).padding(.vertical, 12)
        }
        .frame(width: 840, height: 580)
        .onAppear {
            let start = model.glossaries.glossary(initialSelection) ?? model.glossaries.all.first
            load(start?.id); selection = start?.id
        }
        .onChange(of: selection) { _, new in
            guard new != loadedID else { return }
            if commit() { load(new) } else { selection = loadedID }
        }
        .sheet(isPresented: $suggesting) {
            TermSuggestionsSheet(model: model, initialGlossaryID: loaded?.isBuiltIn == false ? loadedID : nil) { id in select(id) }
        }
        .confirmationDialog("删除“\(loaded?.name ?? "")”？", isPresented: $confirmDelete) {
            Button("删除", role: .destructive, action: delete)
        } message: {
            Text("已生成的阅读稿不受影响，但用它的阅读稿单段重试时将不再附带术语。")
        }
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            List(selection: $selection) {
                Section("我的术语表") {
                    if model.glossaries.user.isEmpty {
                        Text("还没有。点 + 新建，或复制一个内置术语表。").font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(model.glossaries.user) { row($0) }
                }
                Section("内置") {
                    ForEach(model.glossaries.builtIn) { row($0) }
                }
            }
            .listStyle(.sidebar)
            Divider()
            HStack(spacing: 4) {
                iconButton("plus", help: "新建术语表", action: create)
                iconButton("plus.square.on.square", help: "复制所选术语表", action: duplicate).disabled(loaded == nil)
                iconButton("minus", help: "删除所选术语表") { confirmDelete = true }.disabled(loaded == nil || loaded?.isBuiltIn == true)
                Spacer()
                Button("提取…") { if commit() { suggesting = true } }.controlSize(.small)
                    .help(model.selectedDocument.map { "从“\($0.name)”提取候选术语" } ?? "先在文档库中选择一份文档")
                    .disabled(model.selectedDocument == nil || model.activeService == nil)
                Button("导入…", action: importFiles).controlSize(.small)
                Menu("导出") {
                    Button("文本文件（.txt）…") { export(as: .plainText, extension: "txt") }
                    Button("表格文件（.csv）…") { export(as: .commaSeparatedText, extension: "csv") }
                }
                .menuStyle(.borderlessButton).fixedSize().controlSize(.small).disabled(loaded == nil)
            }
            .padding(8)
        }
    }

    private func row(_ glossary: Glossary) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(glossary.name).lineLimit(1)
            Text("\(glossary.entryCount) 条").font(.caption).foregroundStyle(.secondary)
        }
        .tag(glossary.id)
    }

    private func iconButton(_ symbol: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol).frame(width: 18, height: 16) }
            .buttonStyle(.borderless).help(help)
    }

    @ViewBuilder private var editor: some View {
        if let loaded {
            VStack(alignment: .leading, spacing: 10) {
                if loaded.isBuiltIn {
                    Text(loaded.name).font(.title3.weight(.semibold))
                    Text("适用语言：\(languageTitle(loaded.sourceLanguage)) → \(languageTitle(loaded.targetLanguage))")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    TextField("名称", text: $name).textFieldStyle(.roundedBorder).font(.title3)
                    HStack(spacing: 6) {
                        Text("适用语言").font(.caption).foregroundStyle(.secondary)
                        languagePicker("源语言", selection: $sourceLanguage)
                        Image(systemName: "arrow.right").font(.caption).foregroundStyle(.secondary)
                        languagePicker("目标语言", selection: $targetLanguage)
                        Text("翻译时只列出语言对相符的术语表").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text("每行一条：`原文 = 译文` 固定译法，只写原文则保留不译；也可以从表格粘贴两列（Tab 分隔），# 开头的行是注释。翻译每段时只附上该段出现的术语，英文按整词匹配、不分大小写。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if loaded.isBuiltIn {
                    ScrollView {
                        Text(loaded.text).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(8)
                    }
                    .background(.quaternary.opacity(0.3)).border(.quaternary)
                } else {
                    TextEditor(text: $text).font(.system(.body, design: .monospaced)).border(.quaternary)
                }
                Text("\(Glossary.entryCount(loaded.isBuiltIn ? loaded.text : text)) 条").font(.caption).foregroundStyle(.secondary)
                let conflicts = Glossary.conflicts(loaded.isBuiltIn ? loaded.text : text)
                if !conflicts.isEmpty {
                    Label("同一原文写了不同译法，翻译时以最后一行为准：" + conflicts.prefix(5).map { "\($0.source)（第 \($0.lines.map(String.init).joined(separator: "、")) 行）" }.joined(separator: "；")
                          + (conflicts.count > 5 ? " 等 \(conflicts.count) 处" : ""), systemImage: "exclamationmark.triangle.fill")
                        .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(16)
        } else {
            Text("选择或新建一个术语表").foregroundStyle(.secondary)
        }
    }

    private func load(_ id: String?) {
        let glossary = model.glossaries.glossary(id)
        loadedID = glossary?.id; name = glossary?.name ?? ""; text = glossary?.text ?? ""; error = nil
        sourceLanguage = glossary?.sourceLanguage ?? ""; targetLanguage = glossary?.targetLanguage ?? ""
    }

    private func languageTitle(_ code: String?) -> String {
        guard let code else { return "任意" }
        return TranslationSheet.languages.first { $0.0 == code }?.1 ?? code
    }

    private func languagePicker(_ title: String, selection: Binding<String>) -> some View {
        Picker(title, selection: selection) {
            Text("任意").tag("")
            ForEach(TranslationSheet.languages, id: \.0) { Text($0.1).tag($0.0) }
        }
        .labelsHidden().frame(width: 110).controlSize(.small)
    }

    /// Saves the open glossary if it changed; false keeps it open so the edit is not lost.
    @discardableResult private func commit() -> Bool {
        guard dirty, let id = loadedID else { return true }
        do {
            try model.changeGlossaries { try $0.update(id, name: name, text: text, sourceLanguage: sourceLanguage.isEmpty ? nil : sourceLanguage,
                                                         targetLanguage: targetLanguage.isEmpty ? nil : targetLanguage) }
            name = model.glossaries.glossary(id)?.name ?? name
            error = nil
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    private func select(_ id: String) { load(id); selection = id }

    private func create() {
        guard commit() else { return }
        var created: Glossary?
        do { try model.changeGlossaries { created = try $0.create(name: "新术语表") } } catch { self.error = error.localizedDescription }
        if let created { select(created.id) }
    }

    private func duplicate() {
        guard commit(), let id = loadedID else { return }
        var created: Glossary?
        do { try model.changeGlossaries { created = try $0.duplicate(id) } } catch { self.error = error.localizedDescription }
        if let created { select(created.id) }
    }

    private func delete() {
        guard let id = loadedID else { return }
        do {
            try model.changeGlossaries { $0.delete(id) }
            let next = model.glossaries.user.first ?? model.glossaries.builtIn.first
            load(next?.id); selection = next?.id
        } catch { self.error = error.localizedDescription }
    }

    private func importFiles() {
        guard commit() else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.plainText, .commaSeparatedText, .tabSeparatedText]
        panel.allowsMultipleSelection = true
        panel.title = "导入术语表"; panel.prompt = "导入"
        guard panel.runModal() == .OK else { return }
        var last: Glossary?
        var failures: [String] = []
        for url in panel.urls {
            do {
                let file = try GlossaryFile.read(url)
                try model.changeGlossaries { last = try $0.create(name: file.name, text: file.text, sourceLanguage: file.sourceLanguage, targetLanguage: file.targetLanguage) }
            } catch { failures.append(error.localizedDescription) }
        }
        if let last { select(last.id) }
        error = failures.isEmpty ? nil : failures.joined(separator: " ")
    }

    private func export(as type: UTType, extension fileExtension: String) {
        guard commit(), let glossary = loaded else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [type]
        panel.nameFieldStringValue = glossary.name + "." + fileExtension
        panel.title = "导出术语表"; panel.prompt = "导出"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do { try GlossaryFile.write(glossary, to: url); error = nil } catch { self.error = "导出失败：\(error.localizedDescription)" }
    }
}
