import AppKit
import PageBridgeCore
import SwiftUI

struct LibrarySidebar: View {
    @ObservedObject var model: AppModel
    @ObservedObject var library: LibraryModel
    let onShowDemos: () -> Void
    let onShowLiteratureSearch: () -> Void
    @State private var editing: LibraryEditRequest?
    @State private var folderToDelete: LibraryFolder?

    private var documents: [DocumentRecord] { library.visibleDocuments(model.documents) }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 9) {
                if let icon = NSImage(named: "PageBridgeIcon") {
                    Image(nsImage: icon).resizable().scaledToFit().frame(width: 30, height: 30).accessibilityHidden(true)
                } else { Image(systemName: "book.pages.fill").font(.title2).foregroundStyle(.blue) }
                Text("PageBridge").font(.system(size: 18, weight: .semibold))
                Spacer(minLength: 0)
                Menu {
                    Button("添加 PDF", action: model.importPDFs)
                    Button("搜索在线文献…", action: onShowLiteratureSearch)
                    Button("新建文件夹") { editing = .newFolder }
                        .disabled(!library.isAvailable)
                } label: {
                    Image(systemName: "plus").font(.system(size: 14, weight: .medium))
                }
                    .menuStyle(.borderlessButton).menuIndicator(.hidden)
                    .frame(width: 28, height: 28)
                    .help("添加 PDF 或文件夹").accessibilityLabel("添加 PDF 或文件夹")
            }.padding(.horizontal, 15).padding(.top, 14).padding(.bottom, 13)

            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("搜索文档名称", text: $library.searchQuery).textFieldStyle(.plain)
                if !library.searchQuery.isEmpty {
                    Button { library.searchQuery = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary) }
                        .buttonStyle(.plain).accessibilityLabel("清除文档搜索")
                }
            }.font(.system(size: 12)).padding(8)
                .background(Color(nsColor: .quaternaryLabelColor).opacity(0.35), in: RoundedRectangle(cornerRadius: 7))
                .padding(.horizontal, 12).padding(.bottom, 8)

            Button(action: onShowLiteratureSearch) {
                Label("搜索在线文献", systemImage: "globe")
                    .font(.system(size: 12)).frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 8).padding(.vertical, 7).contentShape(Rectangle())
            }.buttonStyle(.plain).padding(.horizontal, 12).padding(.bottom, 6)
            .help("按标题、关键词或 DOI 搜索，选择来源打开全文")

            if !library.isAvailable {
                VStack(alignment: .leading, spacing: 5) {
                    Label("分类数据暂不可用", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    Text("分类文件未被覆盖。重新读取后可继续整理。").font(.caption).foregroundStyle(.secondary)
                    Button("重新读取", action: library.reload)
                }.font(.caption).frame(maxWidth: .infinity, alignment: .leading).padding(12)
            }

            List {
                Section("文档库") {
                    filterRow(.all, icon: "square.stack")
                    filterRow(.uncategorized, icon: "tray")
                    filterRow(.favorites, icon: "star")
                    filterRow(.recent, icon: "clock")
                    filterRow(.trash, icon: "trash")
                }
                Section {
                    ForEach(library.folders) { folder in
                        filterRow(.folder(folder.id), icon: "folder", title: folder.name)
                            .contextMenu {
                                Button("重命名文件夹") { editing = .folder(folder) }
                                Button("删除文件夹…", role: .destructive) { folderToDelete = folder }
                            }
                    }
                    Button { editing = .newFolder } label: { Label("新建文件夹", systemImage: "folder.badge.plus") }
                        .buttonStyle(.plain).foregroundStyle(.secondary).disabled(!library.isAvailable)
                } header: { Text("文件夹") }
                Section {
                    HStack {
                        Text(library.title).font(.caption.weight(.semibold)).foregroundStyle(.secondary).lineLimit(1)
                        Spacer(minLength: 8)
                        LibrarySortMenu(selection: $library.sortOrder).frame(width: 62, height: 28)
                    }.listRowBackground(Color.clear)
                    if documents.isEmpty {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(library.searchQuery.isEmpty ? "暂无文档" : "没有匹配的文档").font(.subheadline)
                            Text(emptyDescription).font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 10)
                    }
                    ForEach(documents) { document in
                        Button {
                            guard !library.isTrashed(id: document.id) else { return }
                            model.selectDocument(document.id)
                            closeSettingsIfNeeded()
                        } label: { documentRow(document) }
                            .buttonStyle(.plain)
                            .contextMenu { documentMenu(document) }
                    }
                }
            }.listStyle(.sidebar)

            Divider().padding(.horizontal, 12)
            HStack {
                Button(action: onShowDemos) { Label("示例 PDF", systemImage: "books.vertical") }
                Spacer(minLength: 4)
                Button { model.settingsPresented = true } label: { Image(systemName: "gearshape") }
                    .help("翻译服务设置").accessibilityLabel("翻译服务设置")
            }.buttonStyle(.plain).font(.system(size: 12)).padding(15)
        }
        .sheet(item: $editing) { request in
            LibraryNameEditor(title: request.title, initialName: request.initialName) { name in
                switch request {
                case .newFolder:
                    return library.createFolder(name: name) ? nil : library.errorMessage
                case .folder(let folder):
                    return library.renameFolder(id: folder.id, name: name) ? nil : library.errorMessage
                case .document(let document):
                    guard !isBusy(document) else { return "文档正在翻译或取消任务，暂时不能重命名。" }
                    model.renameDocument(id: document.id, name: name)
                    let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
                    let expected = clean.lowercased().hasSuffix(".pdf") ? clean : clean + ".pdf"
                    return model.documents.first(where: { $0.id == document.id })?.name == expected ? nil : (model.errorMessage ?? "无法重命名文档。")
                }
            }
        }
        .confirmationDialog("删除文件夹？", isPresented: Binding(get: { folderToDelete != nil }, set: { if !$0 { folderToDelete = nil } }), titleVisibility: .visible) {
            Button("删除文件夹", role: .destructive) {
                if let folder = folderToDelete { library.deleteFolder(id: folder.id) }
                folderToDelete = nil
            }
            Button("取消", role: .cancel) { folderToDelete = nil }
        } message: { Text("文件夹内的文档会回到“未分类”，原文和译文均会保留。") }
        .alert("无法更新文档库", isPresented: Binding(get: { library.errorMessage != nil && editing == nil }, set: { if !$0 { library.errorMessage = nil } })) {
            Button("好", role: .cancel) { library.errorMessage = nil }
        } message: { Text(library.errorMessage ?? "") }
        .onChange(of: library.filter) { _, _ in reconcileSelection() }
        .onChange(of: library.searchQuery) { _, _ in reconcileSelection() }
        .onChange(of: library.state) { _, _ in reconcileSelection() }
        .onChange(of: model.documents) { _, _ in reconcileSelection() }
    }

    private var emptyDescription: String {
        if !library.searchQuery.isEmpty { return "试试其他名称，或清除搜索。" }
        if library.filter == .trash { return "移入回收站的文档可以在此恢复。" }
        if library.filter == .recent { return "显示最近 30 天打开或导入的文档。" }
        if library.filter == .favorites { return "在文档菜单中选择“加入收藏”。" }
        return "点击 + 添加 PDF，或将已有文档移入此文件夹。"
    }

    private func filterRow(_ filter: LibraryFilter, icon: String, title: String? = nil) -> some View {
        Button { library.filter = filter } label: {
            HStack(spacing: 8) {
                Image(systemName: icon).frame(width: 16)
                Text(title ?? filter.title).lineLimit(1)
                Spacer(minLength: 3)
                Text("\(library.count(filter, documents: model.documents))").font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }.padding(.horizontal, 6).padding(.vertical, 5)
                .background(library.filter == filter ? Color.accentColor.opacity(0.14) : .clear, in: RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
        }.buttonStyle(.plain).disabled(!library.isAvailable)
            .accessibilityAddTraits(library.filter == filter ? .isSelected : [])
    }

    private func documentRow(_ document: DocumentRecord) -> some View {
        HStack(alignment: .center, spacing: 9) {
            PDFThumbnailImage(url: document.originalURL, page: 1, width: 38)
                .frame(width: 38, height: 52).clipped().accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) {
                Text(document.name).font(.system(size: 12, weight: .medium)).lineLimit(2).multilineTextAlignment(.leading)
                HStack(spacing: 4) {
                    if isBusy(document) { ProgressView().controlSize(.mini) }
                    Text("\(document.pageCount) 页 · \(document.status.title)").font(.system(size: 10.5)).foregroundStyle(.secondary).lineLimit(1)
                    if library.isFavorite(id: document.id) { Image(systemName: "star.fill").font(.system(size: 9)).foregroundStyle(.orange) }
                }
            }
            Spacer(minLength: 0)
        }.padding(.horizontal, 5).padding(.vertical, 7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(model.selectedID == document.id && !model.settingsPresented ? Color.accentColor.opacity(0.13) : .clear, in: RoundedRectangle(cornerRadius: 7))
            .contentShape(Rectangle()).accessibilityElement(children: .combine)
    }

    @ViewBuilder private func documentMenu(_ document: DocumentRecord) -> some View {
        if library.isTrashed(id: document.id) {
            Button("恢复文档") { library.restoreDocument(id: document.id) }
        } else {
            Button(library.isFavorite(id: document.id) ? "取消收藏" : "加入收藏") { library.toggleFavorite(id: document.id) }
            Button("重命名…") { editing = .document(document) }.disabled(isBusy(document))
            Menu("移到文件夹") {
                Button("未分类") { library.moveDocument(id: document.id, to: nil) }
                ForEach(library.folders) { folder in
                    Button(folder.name) { library.moveDocument(id: document.id, to: folder.id) }
                }
            }
            Divider()
            Button("移入回收站", role: .destructive) {
                guard let current = model.documents.first(where: { $0.id == document.id }), !isBusy(current) else { return }
                library.trashDocument(current)
            }.disabled(isBusy(document))
        }
        Divider()
        Button("在 Finder 中显示") { NSWorkspace.shared.activateFileViewerSelecting([document.originalURL]) }
    }

    private func isBusy(_ document: DocumentRecord) -> Bool {
        model.activeJobDocumentID == document.id || document.status == .running || document.status == .cancelling
    }
    private func closeSettingsIfNeeded() {
        if model.settingsPresented { NotificationCenter.default.post(name: Notification.Name("PageBridge.RequestSettingsClose"), object: nil) }
    }
    private func reconcileSelection() {
        guard library.isAvailable else { return }
        if let id = model.selectedID, documents.contains(where: { $0.id == id }) { return }
        if library.filter != .trash, let first = documents.first {
            model.selectDocument(first.id)
        } else { model.selectedID = nil }
    }
}

/// Keep the title, arrow and hit area in one native control. SwiftUI's
/// borderless Menu bridge can draw an untinted arrow inside a sidebar List.
private struct LibrarySortMenu: NSViewRepresentable {
    @Binding var selection: LibrarySortOrder
    @Environment(\.colorScheme) private var colorScheme

    func makeCoordinator() -> Coordinator { Coordinator(selection: $selection) }

    func makeNSView(context: Context) -> NSPopUpButton {
        let button = NSPopUpButton(frame: .zero, pullsDown: true)
        button.isBordered = false
        button.font = .systemFont(ofSize: 12)
        button.contentTintColor = .labelColor
        button.autoenablesItems = false
        button.addItem(withTitle: "排序")
        for order in LibrarySortOrder.allCases {
            button.addItem(withTitle: order.title)
            button.lastItem?.representedObject = order.rawValue
        }
        button.target = context.coordinator
        button.action = #selector(Coordinator.selectOrder(_:))
        button.setAccessibilityLabel("文档排序")
        return button
    }

    func updateNSView(_ button: NSPopUpButton, context: Context) {
        context.coordinator.selection = $selection
        button.appearance = NSAppearance(named: colorScheme == .dark ? .darkAqua : .aqua)
        for item in button.itemArray {
            item.state = (item.representedObject as? String) == selection.rawValue ? .on : .off
        }
        if let item = button.itemArray.first(where: { ($0.representedObject as? String) == selection.rawValue }) {
            button.select(item)
        }
        button.toolTip = "文档排序：\(selection.title)"
        button.setAccessibilityValue(selection.title)
    }

    final class Coordinator: NSObject {
        var selection: Binding<LibrarySortOrder>
        init(selection: Binding<LibrarySortOrder>) { self.selection = selection }
        @objc func selectOrder(_ sender: NSPopUpButton) {
            guard let rawValue = sender.selectedItem?.representedObject as? String,
                  let order = LibrarySortOrder(rawValue: rawValue) else { return }
            selection.wrappedValue = order
        }
    }
}

private enum LibraryEditRequest: Identifiable {
    case newFolder, folder(LibraryFolder), document(DocumentRecord)
    var id: String {
        switch self { case .newFolder: "new"; case .folder(let f): f.id.uuidString; case .document(let d): d.id.uuidString }
    }
    var title: String {
        switch self { case .newFolder: "新建文件夹"; case .folder: "重命名文件夹"; case .document: "重命名文档" }
    }
    var initialName: String {
        switch self { case .newFolder: ""; case .folder(let f): f.name; case .document(let d): d.name }
    }
}

private struct LibraryNameEditor: View {
    let title: String
    let onSubmit: (String) -> String?
    @State private var name: String
    @State private var error: String?
    @FocusState private var focused: Bool
    @Environment(\.dismiss) private var dismiss

    init(title: String, initialName: String, onSubmit: @escaping (String) -> String?) {
        self.title = title; self.onSubmit = onSubmit; _name = State(initialValue: initialName)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.headline)
            TextField("名称", text: $name).textFieldStyle(.roundedBorder).focused($focused).onSubmit(save)
            if let error { Text(error).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true) }
            HStack { Spacer(); Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("保存", action: save).keyboardShortcut(.defaultAction).disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.padding(22).frame(width: 350).onAppear { focused = true }
    }
    private func save() {
        if let message = onSubmit(name) { error = message }
        else { dismiss() }
    }
}
