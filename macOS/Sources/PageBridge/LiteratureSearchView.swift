import AppKit
import PageBridgeCore
import SwiftUI

struct LiteratureSearchView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var search: LiteratureSearchModel
    @Environment(\.dismiss) private var dismiss
    @FocusState private var queryFocused: Bool
    @AppStorage("PageBridge.literatureFullTextSource") private var fullTextSource: LiteratureFullTextSource = .sciHub
    @AppStorage("PageBridge.sciHubBaseURL") private var sciHubBaseURL = "https://sci-hub.ru"
    @State private var showsSourceSettings = false
    @State private var sourceBrowserRequest: SciHubBrowserRequest?
    @State private var sourceBrowserWork: LiteratureWork?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("搜索在线文献").font(.title2.bold())
                        Text("用 OpenAlex 搜索文献，选择全文来源后直接阅读。")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("关闭") { search.cancelAll(); dismiss() }.keyboardShortcut(.cancelAction)
                }
                HStack(spacing: 10) {
                    TextField("标题、关键词或 DOI / doi.org 链接", text: $search.query)
                        .textFieldStyle(.roundedBorder).focused($queryFocused)
                        .onSubmit { search.search() }.disabled(search.isDownloading)
                        .accessibilityLabel("文献搜索词")
                    Button("搜索", action: search.search)
                        .buttonStyle(.borderedProminent).disabled(!search.canSearch)
                }
                HStack {
                    Picker("全文来源", selection: $fullTextSource) {
                        ForEach(LiteratureFullTextSource.allCases, id: \.self) { source in
                            Text(source.title).tag(source)
                        }
                    }.frame(width: 175).disabled(search.isDownloading)
                    if fullTextSource == .sciHub {
                        Button(showsSourceSettings ? "收起地址" : "设置地址") { showsSourceSettings.toggle() }
                            .font(.caption).disabled(search.isDownloading)
                    }
                    Spacer()
                    Toggle("仅开放获取", isOn: $search.openAccessOnly).toggleStyle(.checkbox)
                        .disabled(search.isDownloading)
                }
                if showsSourceSettings && fullTextSource == .sciHub {
                    HStack {
                        TextField("Sci-Hub HTTPS 地址", text: $sciHubBaseURL)
                            .textFieldStyle(.roundedBorder).disabled(search.isDownloading)
                            .accessibilityLabel("Sci-Hub 地址")
                        Button("恢复默认") { sciHubBaseURL = "https://sci-hub.ru" }.disabled(search.isDownloading)
                    }
                }
                HStack {
                    Text("检索结果不代表所选来源已收录全文。")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if search.isSearching {
                        ProgressView().controlSize(.small)
                        Text("正在搜索…").font(.caption).foregroundStyle(.secondary)
                        Button("取消搜索", action: search.cancelSearch).font(.caption)
                    } else if search.hasSearched {
                        Text("已显示 \(search.works.count) 条结果").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }.padding(22)
            Divider()
            if search.works.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: search.hasSearched ? "doc.text.magnifyingglass" : "globe")
                        .font(.system(size: 34)).foregroundStyle(.secondary)
                    Text(search.hasSearched ? "没有找到匹配的文献" : "从一个标题或 DOI 开始").font(.headline)
                    Text(search.hasSearched ? "试试英文标题、较短的关键词，或取消“仅开放获取”。" : "例如：Ten Simple Rules for Better Figures\n或 10.1371/journal.pcbi.1003833")
                        .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    if fullTextSource == .sciHub, let work = search.directDOIWork {
                        if search.isDownloading {
                            ProgressView().controlSize(.small)
                            Text(search.downloadPhase).font(.caption)
                            Button("取消下载", action: search.cancelDownload)
                        } else {
                            Button("直接打开此 DOI 的全文") { open(work) }
                                .buttonStyle(.borderedProminent)
                            Text("无需等待 OpenAlex 返回检索结果。")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(search.works) { work in resultRow(work) }
                        if search.hasMore {
                            Button("加载更多", action: search.loadMore)
                                .disabled(search.isSearching || search.isDownloading).padding(.vertical, 8)
                        }
                    }.padding(20)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            if let error = search.errorMessage {
                VStack(alignment: .leading, spacing: 6) {
                    Text(error).font(.callout).foregroundStyle(.red)
                        .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                    if let url = search.fallbackURL {
                        HStack {
                            if fullTextSource == .sciHub, let work = search.failedWork, let doi = work.doi {
                                Button("在 App 中验证并继续") {
                                    sourceBrowserWork = work
                                    sourceBrowserRequest = SciHubBrowserRequest(doi: doi, pageURL: url, title: work.title)
                                }
                            }
                            Link("在浏览器查看来源页面", destination: url)
                        }.font(.caption)
                    }
                }
                    .padding(.horizontal, 22).padding(.bottom, 14)
            }
            Divider()
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "network")
                Text("搜索词发送给 OpenAlex；Sci-Hub 获取全文时发送 DOI，公开 PDF 则请求来源链接。PDF 保存到本机后可翻译、批注。需要验证码或登录时，可在浏览器查看来源页面。")
                    .fixedSize(horizontal: false, vertical: true)
            }.font(.caption).foregroundStyle(.secondary).padding(18)
        }
        .frame(width: 800, height: 650)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { queryFocused = true }
        .onDisappear { search.cancelAll() }
        .onChange(of: fullTextSource) { _, _ in search.clearDownloadError() }
        .onChange(of: sciHubBaseURL) { _, _ in search.clearDownloadError() }
        .sheet(item: $sourceBrowserRequest, onDismiss: { sourceBrowserWork = nil }) { request in
            SciHubBrowserView(request: request) { url in
                let work = sourceBrowserWork
                sourceBrowserRequest = nil
                sourceBrowserWork = nil
                if let work { open(work, resolvedURL: url) }
            }
        }
    }

    private func resultRow(_ work: LiteratureWork) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(work.title).font(.headline).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            if !work.authors.isEmpty {
                Text(work.authors.prefix(5).joined(separator: " · ") + (work.authors.count > 5 ? " 等" : ""))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Text([work.publication, work.year.map(String.init)].compactMap { $0 }.joined(separator: " · "))
                .font(.caption).foregroundStyle(.secondary)
            if let doi = work.doi {
                Text("DOI：\(doi)").font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
            HStack(spacing: 12) {
                if search.downloadingID == work.id {
                    ProgressView().controlSize(.small)
                    Text(search.downloadPhase).font(.caption)
                    Button("取消下载", action: search.cancelDownload)
                } else if fullTextSource.canOpen(work) {
                    Text(fullTextSource == .sciHub ? "通过 Sci-Hub 获取全文" : "公开 PDF")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer(minLength: 8)
                    Button("打开全文") { open(work) }
                    .buttonStyle(.borderedProminent).disabled(search.isDownloading)
                    .help("下载并加入文档库；相同文件会打开已有副本")
                    .accessibilityLabel("打开全文：\(work.title)")
                } else {
                    Text(fullTextSource == .sciHub ? "没有 DOI，可尝试公开 PDF 来源" : "未提供公开 PDF")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer(minLength: 8)
                }
                if let url = work.landingURL {
                    Link("原文页面", destination: url).help(url.host ?? "打开原文页面")
                }
            }.font(.system(size: 12))
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
    }

    private func open(_ work: LiteratureWork, resolvedURL: URL? = nil) {
        search.open(work, source: fullTextSource, sciHubBaseURL: sciHubBaseURL,
                    resolvedURL: resolvedURL,
                    importPDF: { try model.importLiteraturePDF($0) }) {
            dismiss()
            if model.settingsPresented {
                NotificationCenter.default.post(name: Notification.Name("PageBridge.RequestSettingsClose"), object: nil)
            }
        }
    }
}
