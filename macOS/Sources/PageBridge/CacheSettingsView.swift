import AppKit
import PageBridgeCore
import SwiftUI

struct CacheSettingsView: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var usage: [CacheCategory: CacheUsage] = [:]
    @State private var loading = true
    @State private var confirmation: CacheCategory?
    @State private var notice: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("缓存与存储").font(.title2.weight(.semibold))
                Spacer()
                if let root = model.libraryDataRoot {
                    Button("打开存储目录") { NSWorkspace.shared.open(root) }
                }
                Button("刷新") { Task { await refresh() } }
                    .disabled(loading || model.isClearingCache)
            }
            if loading && usage.isEmpty {
                ProgressView("正在统计本地文件…").frame(maxWidth: .infinity, minHeight: 230)
            } else {
                Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 11) {
                    ForEach(CacheCategory.allCases) { category in
                        GridRow {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(category.title).fontWeight(.medium)
                                Text(category.detail).font(.caption).foregroundStyle(.secondary)
                            }
                            let item = usage[category]
                            Text(category == .minerU && item == nil ? "未安装" : sizeLabel(item?.bytes ?? 0))
                                .monospacedDigit().gridColumnAlignment(.trailing)
                            HStack(spacing: 6) {
                                Button {
                                    if let locations = item?.locations { NSWorkspace.shared.activateFileViewerSelecting(locations) }
                                } label: { Image(systemName: "folder") }
                                .help("在 Finder 中显示\(category.title)")
                                .accessibilityLabel("在 Finder 中显示\(category.title)")
                                .disabled(item?.locations.isEmpty != false)
                                if category.canClear {
                                    Button(category.action) { confirmation = category }
                                        .disabled(loading || model.isWorkerBusy || model.isClearingCache || item?.locations.isEmpty != false)
                                }
                            }.fixedSize()
                        }
                    }
                }
            }
            Divider()
            HStack {
                if model.isClearingCache {
                    ProgressView().controlSize(.small)
                    Text("正在清理…")
                } else if let notice {
                    Text(notice).foregroundStyle(.secondary)
                } else if model.isWorkerBusy {
                    Text("当前任务结束后可清理缓存。").foregroundStyle(.secondary)
                }
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.defaultAction)
                    .disabled(model.isClearingCache)
            }
        }
        .padding(22).frame(width: 640)
        .task { await refresh() }
        .interactiveDismissDisabled(model.isClearingCache)
        .alert(item: $confirmation) { category in
            let title = category == .readingHistory ? "将历史阅读稿移到废纸篓？" : "清理\(category.title)？"
            return Alert(title: Text(title),
                         message: Text(category == .readingHistory ? "历史版本可在废纸篓恢复。" : category.detail + "清理后可按需重新生成。"),
                         primaryButton: .destructive(Text(category.action)) { Task { await clear(category) } },
                         secondaryButton: .cancel())
        }
    }

    private func refresh() async {
        guard let root = model.libraryDataRoot else { loading = false; return }
        loading = true
        defer { loading = false }
        do {
            usage = try await Task.detached(priority: .utility) { try CacheStorage(root: root).usage() }.value
        } catch { notice = error.localizedDescription }
    }

    private func sizeLabel(_ bytes: Int64) -> String {
        bytes > 0 ? ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file) : "0 B"
    }

    private func clear(_ category: CacheCategory) async {
        do {
            let result = try await model.clearCache(category)
            let summary = category == .readingHistory ? "已将 \(result.removed) 项移到废纸篓。" : "已清理 \(result.removed) 项。"
            notice = result.failed > 0 ? summary + "\(result.failed) 项未能处理。" : summary
            await refresh()
        } catch { notice = error.localizedDescription }
    }
}

private extension CacheCategory {
    var title: String {
        switch self {
        case .currentReadings: "当前阅读稿"
        case .readingHistory: "历史阅读稿"
        case .taskFiles: "任务临时文件"
        case .pendingReadings: "未提交的阅读稿"
        case .searchIndex: "全文搜索缓存"
        case .minerU: "MinerU 本地组件"
        case .minerUDownloads: "MinerU 下载缓存"
        }
    }

    var detail: String {
        switch self {
        case .currentReadings: "文档库正在使用的已保存译稿。"
        case .readingHistory: "追加或重译时留下的旧版本。"
        case .taskFiles: "解析和排版过程中写入的临时文件。"
        case .pendingReadings: "已生成、尚待保存到文档库的译稿所在目录。"
        case .searchIndex: "用于加快下次搜索的原文提取结果。"
        case .minerU: "本机共用的识别模型和 Python 运行环境。"
        case .minerUDownloads: "安装组件时缓存的依赖包和模型副本。"
        }
    }

    var action: String { self == .readingHistory ? "移到废纸篓" : "清理" }
}
