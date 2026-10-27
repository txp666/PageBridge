import AppKit
import PageBridgeCore
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @ObservedObject var model: AppModel
    @StateObject private var scrollSynchronizer = PDFScrollSynchronizer()
    @StateObject private var workspace = ReaderWorkspace()
    @StateObject private var paperAssistant = PaperAssistantModel()
    @AppStorage("PageBridge.showPageNavigation") private var showsNavigation = true
    @AppStorage("PageBridge.assistantServiceID") private var assistantServiceID = ""
    @State private var inspector: ReaderInspector?
    @State private var navigationVariant: PDFDocumentVariant = .original
    @State private var pageNavigation = PDFPageNavigation()
    @State private var independentTranslatedPage = 1
    @State private var isDropTargeted = false
    @State private var showsSearchResults = false
    @State private var showsDemos = false
    @State private var showsLiteratureSearch = false
    @StateObject private var literatureSearch = LiteratureSearchModel()
    @FocusState private var searchFocused: Bool

    init(model: AppModel) {
        self.model = model
        _pageNavigation = State(initialValue: PDFPageNavigation(page: model.page))
        _independentTranslatedPage = State(initialValue: model.page)
    }

    var body: some View {
        NavigationSplitView {
            LibrarySidebar(model: model, library: model.library, onShowDemos: { showsDemos = true },
                           onShowLiteratureSearch: { showsLiteratureSearch = true })
                .navigationSplitViewColumnWidth(min: 220, ideal: 252, max: 320)
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
        .sheet(isPresented: $showsLiteratureSearch) { LiteratureSearchView(model: model, search: literatureSearch) }
        .alert("无法完成操作", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.errorMessage = nil } }
        )) {
            Button("好", role: .cancel) { model.errorMessage = nil }
        } message: { Text(model.errorMessage ?? "") }
        .onAppear { openReadingWorkspace() }
        .onChange(of: model.selectedID) { _, _ in
            scrollSynchronizer.reset()
            pageNavigation = PDFPageNavigation(page: model.page)
            independentTranslatedPage = model.page
            showsSearchResults = false
            openReadingWorkspace()
        }
        .onChange(of: model.selectedDocument?.translatedPath) { _, _ in openReadingWorkspace() }
        .onChange(of: model.selectedDocument?.readingPath) { _, _ in openReadingWorkspace() }
        .onChange(of: model.readingNavigationRequest) { _, request in
            if let request { navigate(to: request.page, variant: .original) }
        }
        .onChange(of: model.usesStructuredReading) { _, _ in
            workspace.clearSelection(); openReadingWorkspace()
            model.searchHits = []; model.searchQuery = ""
        }
        .onChange(of: model.selectedDocument?.name) { _, _ in
            if let document = model.selectedDocument { workspace.open(document: document) }
        }
        .onChange(of: model.readingMode) { _, mode in
            if mode != .dual {
                let structured = model.selectedDocument.map(usesStructuredReader) ?? false
                workspace.selectedVariant = mode == .translated && !structured ? .translated : .original
                if structured { workspace.clearSelection() }
            }
        }
        .onChange(of: model.page) { _, value in
            if model.syncScrolling || workspace.selectedVariant == .original || model.readingMode != .dual {
                workspace.currentPage = value
            }
        }
        .onChange(of: model.searchHits.count) { _, count in
            if model.searchPresented && count > 0 { showsSearchResults = true }
        }
        .onChange(of: workspace.errorMessage) { _, value in
            if let value { model.errorMessage = value; workspace.errorMessage = nil }
        }
        .onChange(of: model.syncScrolling) { _, enabled in
            if !enabled { independentTranslatedPage = model.page }
        }
        .onChange(of: model.searchPresented) { _, visible in
            if visible { DispatchQueue.main.async { searchFocused = true } }
        }
    }

    private func reader(_ document: DocumentRecord) -> some View {
        VStack(spacing: 0) {
            readerToolbar(document)
            if model.searchPresented { searchBar }
            readingTools
            Divider()
            GeometryReader { geometry in
                HStack(spacing: 0) {
                    if showsNavigation && (inspector == nil || geometry.size.width >= 1180) {
                        PDFNavigationPanel(document: document, url: navigationURL(document), page: workspace.currentPage,
                                           bookmarks: workspace.bookmarkPageNumbers) { navigate(to: $0, variant: workspace.selectedVariant) }
                        Divider()
                    }
                    HStack(spacing: 1) {
                        if effectiveMode(document) != .translated {
                            pane(document.originalURL, title: "原文", document: document)
                        }
                        if effectiveMode(document) == .dual { Divider() }
                        if effectiveMode(document) != .original {
                            if usesStructuredReader(document) {
                                StructuredReadingView(model: model, document: document,
                                    pageNumber: effectiveMode(document) == .dual && !model.syncScrolling ? $independentTranslatedPage : $model.page,
                                    navigation: pageNavigation,
                                    acceptsNavigation: model.syncScrolling || effectiveMode(document) != .dual || navigationVariant == .translated,
                                    synchronizesScrolling: model.syncScrolling && effectiveMode(document) == .dual,
                                    synchronizer: scrollSynchronizer,
                                    onLocate: { page, bbox in
                                        model.readingMode = .dual
                                        navigate(to: page, variant: .original)
                                        pageNavigation = PDFPageNavigation(page: page, sourceBounds: bbox)
                                    })
                            } else if let url = readableTranslation(document) {
                                pane(url, title: "译文", document: document)
                            }
                        }
                    }.frame(maxWidth: .infinity, maxHeight: .infinity)
                    if let inspector {
                        Divider()
                        readerInspector(inspector, document: document).frame(width: 346)
                    }
                }
            }
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
        HStack(spacing: 10) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) {
                    documentTitle(document)
                    Text(documentSummary(document))
                        .font(.caption).foregroundStyle(.secondary).fixedSize()
                }
                documentTitle(document)
            }
            .frame(minWidth: 90, maxWidth: .infinity, alignment: .leading)
            .help("\(document.name)\n\(documentSummary(document))")
            if document.readingPath != nil && readableTranslation(document) != nil {
                Picker("译文形式", selection: $model.usesStructuredReading) {
                    Text("原页对照").tag(true)
                    Text("重排 PDF").tag(false)
                }.labelsHidden().frame(width: 125).help("选择原页版式对照或重排 PDF")
            }
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
                Label(hasReadableTranslation(document) ? "重新翻译" : "翻译", systemImage: "character.bubble")
            }
            .disabled(model.isTranslating).help("翻译当前 PDF")
            Menu {
                ForEach(ExportKind.allCases) { kind in
                    Button(kind.title) { model.exportSelected(kind) }
                        .disabled(!canExport(kind, document: document))
                }
                Divider()
                Button("带批注的 PDF…") { workspace.exportAnnotatedPDF() }
                Button("阅读笔记（Markdown）…") { workspace.exportNotes() }
                Divider()
                Button("在 Finder 中显示输出") { model.openOutputFolder() }
                    .disabled(!document.hasTranslation && document.bilingualURL == nil)
            } label: {
                Text("导出 PDF")
            }
            .menuStyle(.borderedButton).tint(.blue).fixedSize()
        }
        .controlSize(.regular)
        .padding(.horizontal, 16).padding(.vertical, 7)
        .documentToolbarWindowChrome()
        .background(.bar)
    }

    private func documentTitle(_ document: DocumentRecord) -> some View {
        Text(URL(fileURLWithPath: document.name).deletingPathExtension().lastPathComponent)
            .font(.system(size: 14, weight: .semibold))
            .lineLimit(1).truncationMode(.middle)
    }

    private func documentSummary(_ document: DocumentRecord) -> String {
        "\(languageName(document.sourceLanguage)) → \(languageName(document.targetLanguage)) · \(document.pageCount) 页"
    }

    private func readingModeControl(_ document: DocumentRecord) -> some View {
        HStack(spacing: 2) {
            ForEach(ReadingMode.allCases) { mode in
                Button {
                    model.readingMode = mode
                } label: {
                    Text(mode.title).font(.system(size: 12, weight: .medium))
                        .frame(width: 44).padding(.vertical, 4)
                        .foregroundStyle(effectiveMode(document) == mode ? Color.white : Color.primary)
                        .background(effectiveMode(document) == mode ? Color.accentColor : .clear, in: RoundedRectangle(cornerRadius: 5))
                }
                .buttonStyle(.plain)
                .disabled(mode != .original && !hasReadableTranslation(document))
                .opacity(mode != .original && !hasReadableTranslation(document) ? 0.4 : 1)
                .help(mode != .original && !hasReadableTranslation(document) ? "翻译完成后可查看" : mode.title)
                .accessibilityAddTraits(effectiveMode(document) == mode ? .isSelected : [])
            }
        }
        .padding(2).background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 7))
    }

    private func pane(_ url: URL, title: String, document: DocumentRecord) -> some View {
        VStack(spacing: 0) {
            if effectiveMode(document) == .dual {
                HStack {
                    Text(title).font(.system(size: 10.5, weight: .medium)).foregroundStyle(.secondary)
                    Spacer()
                    Text(title == "原文" ? languageName(document.sourceLanguage) : languageName(document.targetLanguage) + (model.syncScrolling ? "" : " · 第 \(independentTranslatedPage) 页"))
                        .font(.system(size: 10.5)).foregroundStyle(.tertiary)
                }.lineLimit(1).padding(.horizontal, 16).frame(height: 22)
            }
            PDFReaderView(url: url, page: title == "译文" && effectiveMode(document) == .dual && !model.syncScrolling ? $independentTranslatedPage : $model.page,
                          navigation: pageNavigation,
                          acceptsNavigation: model.syncScrolling || effectiveMode(document) != .dual || navigationVariant == (title == "译文" ? .translated : .original),
                          zoom: model.zoom,
                          synchronizesScrolling: model.syncScrolling && effectiveMode(document) == .dual,
                          synchronizer: scrollSynchronizer,
                          searchQuery: model.searchPresented ? model.searchQuery : "",
                          workspace: workspace, documentID: document.id,
                          variant: title == "译文" ? .translated : .original)
        }
    }

    private var searchBar: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField(model.readingMode == .translated ? "搜索译文中的文字" : "搜索原文中的文字", text: $model.searchQuery)
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
                                        navigate(to: hit.page, variant: model.readingMode == .translated ? .translated : .original)
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

    private func navigate(to requestedPage: Int, variant requestedVariant: PDFDocumentVariant? = nil) {
        let variant = requestedVariant ?? workspace.selectedVariant
        guard let document = model.selectedDocument else { return }
        let page = max(1, min(document.pageCount, requestedPage))
        navigationVariant = variant
        workspace.selectedVariant = variant
        workspace.currentPage = page
        if effectiveMode(document) == .dual && !model.syncScrolling && variant == .translated {
            independentTranslatedPage = page
        } else { model.page = page }
        if effectiveMode(document) != .dual && !(usesStructuredReader(document) && requestedVariant == nil) {
            model.readingMode = variant == .translated && hasReadableTranslation(document) ? .translated : .original
        }
        pageNavigation = PDFPageNavigation(page: page)
    }

    private func openReadingWorkspace() {
        guard let document = model.selectedDocument else { return }
        workspace.open(document: document)
        workspace.selectedVariant = effectiveMode(document) == .translated && !usesStructuredReader(document) ? .translated : .original
        workspace.currentPage = model.page
        if let error = workspace.errorMessage { model.errorMessage = error; workspace.errorMessage = nil }
    }

    private func navigationURL(_ document: DocumentRecord) -> URL {
        workspace.selectedVariant == .translated ? (readableTranslation(document) ?? document.originalURL) : document.originalURL
    }

    private var readingTools: some View {
        HStack(spacing: 12) {
            Button { showsNavigation.toggle(); if showsNavigation { inspector = nil } } label: {
                Image(systemName: "sidebar.left")
            }.help("页面缩略图、目录与书签").accessibilityLabel("页面导航")
            Divider().frame(height: 18)
            Button(action: workspace.highlightSelection) { Label("高亮", systemImage: "highlighter") }
                .disabled(!workspace.canAnnotateSelection).help("先在 PDF 中选择文字，再添加高亮")
            Button(action: workspace.underlineSelection) { Image(systemName: "underline") }
                .disabled(!workspace.canAnnotateSelection).help("为选中文字添加下划线").accessibilityLabel("下划线")
            Button(action: workspace.addNote) { Label("笔记", systemImage: "square.and.pencil") }.help("记录当前页面或选中文字的笔记")
            Button(action: workspace.toggleBookmark) {
                Image(systemName: workspace.bookmarkPageNumbers.contains(workspace.currentPage) ? "bookmark.fill" : "bookmark")
            }.help("添加或移除当前页书签").accessibilityLabel("当前页书签")
            Menu {
                Button("复制选中文字") {
                    NSPasteboard.general.clearContents(); NSPasteboard.general.setString(workspace.selectionText, forType: .string)
                }.disabled(workspace.selectionText.isEmpty)
                Button("复制文档与页码") {
                    guard let document = model.selectedDocument else { return }
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString("\(document.name) · \(workspace.selectedVariant.title) 第 \(workspace.currentPage) 页", forType: .string)
                }
            } label: { Image(systemName: "doc.on.clipboard") }.menuStyle(.borderlessButton).frame(width: 24).help("复制选文或页码").accessibilityLabel("复制选文或页码")
            Spacer(minLength: 6)
            if !workspace.selectionText.isEmpty {
                Text("已选 \(workspace.selectionText.count) 字").font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            Button { inspector = inspector == .notes ? nil : .notes } label: { Label("阅读笔记", systemImage: "list.bullet.rectangle") }
                .foregroundStyle(inspector == .notes ? Color.accentColor : .primary)
            Button { inspector = inspector == .assistant ? nil : .assistant } label: { Label("助手", systemImage: "sparkles") }
                .foregroundStyle(inspector == .assistant ? Color.accentColor : .primary)
        }.buttonStyle(.borderless).controlSize(.small).font(.system(size: 12))
            .padding(.horizontal, 16).padding(.vertical, 6).background(.bar)
    }

    private func readerInspector(_ selected: ReaderInspector, document: DocumentRecord) -> some View {
        VStack(spacing: 0) {
            HStack {
                Picker("阅读工具", selection: Binding(get: { inspector ?? .assistant }, set: { inspector = $0 })) {
                    Text("助手").tag(ReaderInspector.assistant)
                    Text("笔记").tag(ReaderInspector.notes)
                }.pickerStyle(.segmented).labelsHidden()
                Button { inspector = nil } label: { Image(systemName: "xmark") }
                    .buttonStyle(.plain).help("关闭侧栏").accessibilityLabel("关闭阅读侧栏")
            }.padding(10)
            Divider()
            if selected == .assistant {
                PaperAssistantPanel(model: paperAssistant, document: document, page: workspace.currentPage,
                                    selectionText: workspace.selectionText, service: assistantService,
                                    services: model.services, selectedServiceID: Binding(
                                        get: { assistantService?.id },
                                        set: { assistantServiceID = $0?.uuidString ?? "" }
                                    ), apiKey: { try model.key(for: $0) },
                                    onNavigate: { navigate(to: $0, variant: .original) }, onSaveNote: { text in
                    workspace.addNote(text: text); inspector = .notes
                })
            } else {
                AnnotationsPanel(workspace: workspace) { navigate(to: $0, variant: workspace.selectedVariant) }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color(nsColor: .controlBackgroundColor))
    }

    private var assistantService: ServiceConfiguration? {
        // Assistant choice is independent of the translation profile. Deleted profiles fall back safely.
        model.services.first { $0.id == UUID(uuidString: assistantServiceID) }
            ?? model.activeService ?? model.services.first
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

    private func usesStructuredReader(_ document: DocumentRecord) -> Bool {
        model.usesStructuredReading && document.readingPath != nil
    }

    private func hasReadableTranslation(_ document: DocumentRecord) -> Bool {
        usesStructuredReader(document) || readableTranslation(document) != nil
    }

    private func effectiveMode(_ document: DocumentRecord) -> ReadingMode {
        hasReadableTranslation(document) ? model.readingMode : .original
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

private enum ReaderInspector: Hashable { case assistant, notes }
