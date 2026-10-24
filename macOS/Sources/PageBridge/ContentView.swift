import AppKit
import PageBridgeCore
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @ObservedObject var model: AppModel
    @StateObject private var scrollSynchronizer = PDFScrollSynchronizer()
    @State private var pageNavigation = PDFPageNavigation()
    @State private var independentTranslatedPage = 1
    @State private var isDropTargeted = false
    @State private var showsSearchResults = false
    @State private var showsDemos = false
    @FocusState private var searchFocused: Bool

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 215, ideal: 244, max: 310)
        } detail: {
            Group {
                if model.settingsPresented {
                    ServiceSettingsView(model: model)
                } else if let document = model.selectedDocument {
                    reader(document)
                } else {
                    emptyLibrary
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(nsColor: .windowBackgroundColor))
            .overlay {
                if isDropTargeted {
                    RoundedRectangle(cornerRadius: 12).strokeBorder(.blue, style: StrokeStyle(lineWidth: 3, dash: [8, 5]))
                        .padding(8).background(.blue.opacity(0.06)).allowsHitTesting(false)
                }
            }
            .dropDestination(for: URL.self) { urls, _ in
                let pdfs = urls.filter { $0.pathExtension.lowercased() == "pdf" }
                guard !pdfs.isEmpty else { return false }
                model.importURLs(pdfs)
                return true
            } isTargeted: { isDropTargeted = $0 }
        }
        .navigationSplitViewStyle(.balanced)
        .frame(minWidth: 1000, minHeight: 680)
        .sheet(isPresented: $model.translationPresented) { TranslationSheet(model: model) }
        .sheet(isPresented: $showsDemos) { DemoLibraryView(model: model) }
        .alert("无法完成操作", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.errorMessage = nil } }
        )) {
            Button("好", role: .cancel) { model.errorMessage = nil }
        } message: { Text(model.errorMessage ?? "") }
        .onChange(of: model.selectedID) { _, _ in
            scrollSynchronizer.reset()
            pageNavigation = PDFPageNavigation()
            independentTranslatedPage = 1
            showsSearchResults = false
        }
        .onChange(of: model.syncScrolling) { _, enabled in
            if !enabled { independentTranslatedPage = model.page }
        }
        .onChange(of: model.searchPresented) { _, visible in
            if visible { DispatchQueue.main.async { searchFocused = true } }
        }
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                if let icon = NSImage(named: "PageBridgeIcon") {
                    Image(nsImage: icon).resizable().scaledToFit().frame(width: 34, height: 34)
                        .accessibilityHidden(true)
                } else {
                    Image(systemName: "book.pages.fill").font(.system(size: 25)).foregroundStyle(.blue)
                }
                Text("PageBridge").font(.system(size: 19, weight: .semibold))
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 17).padding(.top, 15).padding(.bottom, 25)
            HStack {
                Text("文档").font(.caption.weight(.medium)).foregroundStyle(.secondary)
                Spacer()
                Button(action: model.importPDFs) { Image(systemName: "plus") }
                    .buttonStyle(.plain).help("添加 PDF").accessibilityLabel("添加 PDF")
            }.padding(.horizontal, 19).padding(.bottom, 7)
            if model.documents.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("尚未添加文档").font(.subheadline)
                    Text("拖入 PDF，或点击 + 添加。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading).padding(19)
                Spacer()
            } else {
                List(selection: Binding<UUID?>(
                    get: { model.settingsPresented ? nil : model.selectedID },
                    set: { if let id = $0 {
                        model.selectDocument(id)
                        if model.settingsPresented {
                            NotificationCenter.default.post(name: Notification.Name("PageBridge.RequestSettingsClose"), object: nil)
                        }
                    } }
                )) {
                    ForEach(model.documents) { document in
                        documentRow(document)
                            .tag(document.id)
                            .contextMenu {
                                Button("在 Finder 中显示") { NSWorkspace.shared.activateFileViewerSelecting([document.originalURL]) }
                                Divider()
                                Button("移出文档库", role: .destructive) { model.removeDocument(document.id) }
                                    .disabled(document.status == .running || document.status == .cancelling)
                            }
                    }
                }
                .listStyle(.sidebar)
            }
            Divider().padding(.horizontal, 14)
            Button { showsDemos = true } label: {
                Label("示例 PDF", systemImage: "books.vertical")
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 12).padding(.vertical, 10)
            }.buttonStyle(.plain).padding(.horizontal, 10).padding(.top, 6)
            Button {
                model.settingsPresented = true
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "gearshape").font(.system(size: 18))
                    Text("翻译服务").font(.subheadline)
                    Spacer(minLength: 4)
                    Text(model.activeService?.provider.title ?? "设置")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold)).foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 12).padding(.vertical, 11)
                .background(model.settingsPresented ? Color.blue.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain).padding(10).help("配置 API 与翻译引擎")
        }
    }

    private func documentRow(_ document: DocumentRecord) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: document.status == .completed ? "doc.text.fill" : "doc.text")
                .font(.system(size: 23, weight: .light)).foregroundStyle(document.status == .completed ? Color.blue : Color.secondary)
                .frame(width: 25).padding(.top, 3)
            VStack(alignment: .leading, spacing: 5) {
                Text(document.name).font(.system(size: 12.5, weight: .medium)).lineLimit(2)
                HStack(spacing: 5) {
                    if document.status == .running || document.status == .cancelling {
                        ProgressView().controlSize(.mini).scaleEffect(0.8)
                    }
                    Text("\(document.pageCount) 页 · \(document.status.title)")
                        .font(.system(size: 10.5)).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 7)
        .accessibilityElement(children: .combine)
    }

    private func reader(_ document: DocumentRecord) -> some View {
        VStack(spacing: 0) {
            readerToolbar(document)
            if model.searchPresented { searchBar }
            Divider()
            HStack(spacing: 1) {
                if effectiveMode(document) != .translated {
                    pane(document.originalURL, title: "原文", document: document)
                }
                if effectiveMode(document) == .dual { Divider() }
                if effectiveMode(document) != .original, let url = readableTranslation(document) {
                    pane(url, title: "译文", document: document)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(nsColor: .separatorColor).opacity(0.2))
            if model.isTranslating { translationProgress }
            else if !model.statusMessage.isEmpty {
                HStack(spacing: 7) {
                    Image(systemName: "info.circle").foregroundStyle(.secondary)
                    Text(model.statusMessage).lineLimit(2).foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                }
                .font(.caption).padding(.horizontal, 17).padding(.vertical, 8)
                .background(Color(nsColor: .controlBackgroundColor))
            }
            Divider()
            readerFooter(document)
        }
    }

    private func readerToolbar(_ document: DocumentRecord) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(URL(fileURLWithPath: document.name).deletingPathExtension().lastPathComponent)
                    .font(.system(size: 18, weight: .semibold)).lineLimit(1).truncationMode(.middle)
                Text("\(languageName(document.sourceLanguage)) → \(languageName(document.targetLanguage)) · \(document.pageCount) 页")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .frame(minWidth: 120, maxWidth: .infinity, alignment: .leading)
            readingModeControl(document)
            Menu {
                Button("适合宽度") { model.zoom = 1 }
                Divider()
                ForEach([0.5, 0.75, 1.0, 1.25, 1.5, 2.0], id: \.self) { scale in
                    Button("\(Int(scale * 100))%") { model.zoom = scale }
                }
                Divider()
                Button("放大") { model.zoom = min(3, model.zoom + 0.25) }
                Button("缩小") { model.zoom = max(0.5, model.zoom - 0.25) }
            } label: {
                Text("\(Int(model.zoom * 100))%").monospacedDigit().frame(width: 44)
            }.menuStyle(.borderedButton).fixedSize().help("缩放，100% 适合阅读区宽度")
            Button { model.searchPresented.toggle() } label: {
                Image(systemName: "magnifyingglass").font(.system(size: 15))
            }
            .buttonStyle(.borderless).help("搜索文档").accessibilityLabel("搜索文档")
            Button {
                model.translationPresented = true
            } label: {
                Label(readableTranslation(document) == nil ? "翻译" : "重新翻译", systemImage: "character.bubble")
            }
            .disabled(model.isTranslating).help("翻译当前 PDF")
            Menu {
                ForEach(ExportKind.allCases) { kind in
                    Button(kind.title) { model.exportSelected(kind) }
                        .disabled(!canExport(kind, document: document))
                }
                Divider()
                Button("在 Finder 中显示输出") { model.openOutputFolder() }
                    .disabled(readableTranslation(document) == nil && document.bilingualURL == nil)
            } label: {
                Text("导出 PDF")
            }
            .menuStyle(.borderedButton).tint(.blue).fixedSize()
        }
        .controlSize(.regular)
        .padding(.horizontal, 18).padding(.vertical, 17)
        .documentToolbarWindowChrome()
        .background(.bar)
    }

    private func readingModeControl(_ document: DocumentRecord) -> some View {
        HStack(spacing: 2) {
            ForEach(ReadingMode.allCases) { mode in
                Button {
                    model.readingMode = mode
                } label: {
                    Text(mode.title).font(.system(size: 12, weight: .medium))
                        .frame(width: 44).padding(.vertical, 6)
                        .foregroundStyle(effectiveMode(document) == mode ? Color.white : Color.primary)
                        .background(effectiveMode(document) == mode ? Color.accentColor : .clear, in: RoundedRectangle(cornerRadius: 5))
                }
                .buttonStyle(.plain)
                .disabled(mode != .original && readableTranslation(document) == nil)
                .opacity(mode != .original && readableTranslation(document) == nil ? 0.4 : 1)
                .help(mode != .original && readableTranslation(document) == nil ? "翻译完成后可查看" : mode.title)
                .accessibilityAddTraits(effectiveMode(document) == mode ? .isSelected : [])
            }
        }
        .padding(3).background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 7))
    }

    private func pane(_ url: URL, title: String, document: DocumentRecord) -> some View {
        VStack(spacing: 0) {
            if effectiveMode(document) == .dual {
                HStack {
                    Text(title).font(.system(size: 10.5, weight: .medium)).foregroundStyle(.secondary)
                    Spacer()
                    Text(title == "原文" ? languageName(document.sourceLanguage) : languageName(document.targetLanguage) + (model.syncScrolling ? "" : " · 第 \(independentTranslatedPage) 页"))
                        .font(.system(size: 10.5)).foregroundStyle(.tertiary)
                }.padding(.horizontal, 21).padding(.top, 9).padding(.bottom, 3)
            }
            PDFReaderView(url: url, page: title == "译文" && effectiveMode(document) == .dual && !model.syncScrolling ? $independentTranslatedPage : $model.page,
                          navigation: pageNavigation,
                          acceptsNavigation: !(title == "译文" && effectiveMode(document) == .dual && !model.syncScrolling),
                          zoom: model.zoom,
                          synchronizesScrolling: model.syncScrolling && effectiveMode(document) == .dual,
                          synchronizer: scrollSynchronizer,
                          searchQuery: model.searchPresented ? model.searchQuery : "")
        }
    }

    private var searchBar: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField("搜索原文中的文字", text: $model.searchQuery)
                .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                .focused($searchFocused).onSubmit { model.search(); showsSearchResults = !model.searchHits.isEmpty }
            Button("搜索") { model.search(); showsSearchResults = !model.searchHits.isEmpty }
            Button("结果 \(model.searchHits.count)") { showsSearchResults.toggle() }
                .disabled(model.searchHits.isEmpty)
                .popover(isPresented: $showsSearchResults, arrowEdge: .bottom) {
                    VStack(alignment: .leading, spacing: 0) {
                        Text("搜索结果 · \(model.searchHits.count) 页")
                            .font(.headline).padding(14)
                        Divider()
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 0) {
                                ForEach(model.searchHits) { hit in
                                    Button {
                                        navigate(to: hit.page)
                                        showsSearchResults = false
                                    } label: {
                                        VStack(alignment: .leading, spacing: 6) {
                                            Text("第 \(hit.page) 页").font(.caption.weight(.semibold)).foregroundStyle(.blue)
                                            Text(hit.text).font(.subheadline).lineLimit(3).foregroundStyle(.primary)
                                        }.frame(maxWidth: .infinity, alignment: .leading).padding(13)
                                    }.buttonStyle(.plain)
                                    Divider()
                                }
                            }
                        }
                    }.frame(width: 380, height: 360)
                }
            Spacer()
            Button { model.searchPresented = false; showsSearchResults = false } label: { Image(systemName: "xmark") }
                .buttonStyle(.borderless).help("关闭搜索").accessibilityLabel("关闭搜索")
        }
        .controlSize(.small).padding(.horizontal, 19).padding(.vertical, 9)
        .background(.bar)
    }

    private var translationProgress: some View {
        HStack(spacing: 12) {
            if let progress = model.progress {
                ProgressView(value: max(0, min(1, progress))).frame(width: 110)
            } else {
                ProgressView().controlSize(.small)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(activeJobName).font(.caption.weight(.medium)).lineLimit(1)
                Text(model.statusMessage.isEmpty ? "正在处理 PDF…" : model.statusMessage)
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 5)
            Button("取消翻译") { model.cancelTranslation() }
                .controlSize(.small)
                .disabled(model.documents.first(where: { $0.id == model.activeJobDocumentID })?.status == .cancelling)
        }
        .padding(.horizontal, 17).padding(.vertical, 11)
        .background(.blue.opacity(0.055))
    }

    private var activeJobName: String {
        model.documents.first(where: { $0.id == model.activeJobDocumentID })?.name ?? "正在翻译"
    }

    private func readerFooter(_ document: DocumentRecord) -> some View {
        HStack(spacing: 10) {
            Text(document.status.title).font(.caption).foregroundStyle(.secondary)
            Spacer()
            Button { navigate(to: max(1, model.page - 1)) } label: { Image(systemName: "chevron.left") }
                .disabled(model.page <= 1).help("上一页").accessibilityLabel("上一页")
            HStack(spacing: 4) {
                Text("第")
                TextField("页码", value: $model.page, format: .number)
                    .textFieldStyle(.plain).multilineTextAlignment(.center).frame(width: 35)
                    .onSubmit { navigate(to: max(1, min(document.pageCount, model.page))) }
                    .accessibilityLabel("当前页码")
                Text("/ \(document.pageCount) 页")
            }.font(.system(size: 12)).foregroundStyle(.secondary).monospacedDigit()
            Button { navigate(to: min(document.pageCount, model.page + 1)) } label: { Image(systemName: "chevron.right") }
                .disabled(model.page >= document.pageCount).help("下一页").accessibilityLabel("下一页")
            Spacer()
            Toggle(isOn: $model.syncScrolling) {
                Label("同步滚动", systemImage: "link").font(.caption)
            }
            .toggleStyle(.switch).controlSize(.mini).fixedSize()
            .disabled(effectiveMode(document) != .dual)
        }
        .buttonStyle(.borderless).padding(.horizontal, 20).padding(.vertical, 12)
        .background(.bar)
    }

    private func navigate(to page: Int) {
        model.page = page
        pageNavigation = PDFPageNavigation(page: page)
    }

    private var emptyLibrary: some View {
        VStack(spacing: 18) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 58, weight: .ultraLight)).foregroundStyle(.blue)
                .padding(.bottom, 3)
            Text("把 PDF 带到这里").font(.system(size: 25, weight: .semibold))
            Text("拖入文档，或从 Mac 选择 PDF。\n保留原文阅读，翻译完成后并排对照。")
                .font(.system(size: 14)).foregroundStyle(.secondary).multilineTextAlignment(.center).lineSpacing(5)
            Button(action: model.importPDFs) {
                Label("选择 PDF…", systemImage: "plus").padding(.horizontal, 14).padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent).controlSize(.large).padding(.top, 4)
            Button("先打开一份示例 PDF") { showsDemos = true }.buttonStyle(.link)
            HStack(spacing: 24) {
                Label("本地文档", systemImage: "internaldrive")
                Label("自定义 API", systemImage: "network")
                Label("原文与译文对照", systemImage: "rectangle.split.2x1")
            }
            .font(.caption).foregroundStyle(.tertiary).padding(.top, 13)
            if !model.engineReady {
                Button("配置翻译服务与引擎") { model.settingsPresented = true }
                    .buttonStyle(.link).font(.caption).padding(.top, 5)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(30)
    }

    private func readableTranslation(_ document: DocumentRecord) -> URL? {
        guard let url = document.translatedURL, FileManager.default.fileExists(atPath: url.path) else { return nil }
        return url
    }

    private func effectiveMode(_ document: DocumentRecord) -> ReadingMode {
        readableTranslation(document) == nil ? .original : model.readingMode
    }

    private func canExport(_ kind: ExportKind, document: DocumentRecord) -> Bool {
        let url: URL?
        switch kind {
        case .original: url = document.originalURL
        case .translated: url = document.translatedURL
        case .bilingual: url = document.bilingualURL
        }
        return url.map { FileManager.default.fileExists(atPath: $0.path) } ?? false
    }

    private func languageName(_ code: String) -> String {
        ["en": "英语", "zh": "简体中文", "zh-TW": "繁体中文", "ja": "日语", "ko": "韩语", "fr": "法语", "de": "德语", "es": "西班牙语", "ru": "俄语"][code] ?? code
    }
}
