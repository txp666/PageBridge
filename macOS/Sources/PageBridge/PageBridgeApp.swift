import AppKit
import SwiftUI
import Combine
import PageBridgeCore

@main
struct PageBridgeApp: App {
    @StateObject private var model = AppModel()
    @StateObject private var updater = UpdateController()
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    var body: some Scene {
        WindowGroup("PageBridge") {
            ContentView(model: model)
                .pageBridgeWindowChrome()
                .frame(minWidth: 1100, minHeight: 740)
                .onAppear {
                    delegate.model = model
                    delegate.updater = updater
                    updater.start(model: model)
                    NSApp.activate(ignoringOtherApps: true)
                }
                .onOpenURL { model.importURLs([$0]) }
        }
        .defaultSize(width: 1480, height: 960)
        .windowToolbarStyle(.unifiedCompact)
        .commands {
            CommandGroup(after: .appInfo) {
                UpdateMenu(updater: updater)
            }
            CommandGroup(replacing: .newItem) {
                Button("添加 PDF…") { model.importPDFs() }.keyboardShortcut("o")
                Button("导出译文…") { model.exportSelected(.translated) }.keyboardShortcut("e", modifiers: [.command, .shift]).disabled(model.selectedDocument?.translatedURL == nil)
            }
            CommandGroup(replacing: .appSettings) {
                Button("翻译服务…") { model.settingsPresented = true }.keyboardShortcut(",")
            }
            CommandGroup(after: .textEditing) {
                Button("搜索文档") { model.searchPresented = true }.keyboardShortcut("f").disabled(model.selectedDocument == nil)
            }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?
    weak var updater: UpdateController?
    private var termination: AnyCancellable?
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model, model.isTranslating else { return .terminateNow }
        // A translation may start after Sparkle's earlier idle check. In that
        // race, cancel the update termination rather than offer to cancel work.
        if updater?.isRelaunchingForUpdate == true { return .terminateCancel }
        let alert = NSAlert(); alert.messageText = "翻译仍在进行"
        alert.informativeText = "退出会取消当前翻译。已发送的在线 API 请求可能仍计费。"
        alert.addButton(withTitle: "取消翻译并退出"); alert.addButton(withTitle: "继续翻译")
        guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
        termination = model.$isTranslating.filter { !$0 }.prefix(1).receive(on: RunLoop.main).sink { _ in sender.reply(toApplicationShouldTerminate: true) }
        model.cancelTranslation()
        return .terminateLater
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
