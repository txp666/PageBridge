import AppKit
import Combine
import PageBridgeCore
import Sparkle
import SwiftUI

@MainActor
final class UpdateController: NSObject, ObservableObject, SPUUpdaterDelegate {
    @Published private(set) var canCheckForUpdates = false
    @Published private(set) var automaticallyChecksForUpdates = false
    @Published private(set) var isTranslating = false
    private(set) var isRelaunchingForUpdate = false

    private weak var model: AppModel?
    private var controller: SPUStandardUpdaterController!
    private var observations = Set<AnyCancellable>()
    private var translationObservation: AnyCancellable?
    private var installationGate = UpdateInstallationGate()
    private var deferredCheck = false
    private var started = false

    override init() {
        super.init()
        controller = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
        controller.updater.publisher(for: \.canCheckForUpdates)
            .receive(on: RunLoop.main)
            .sink { [weak self] available in
                guard let self else { return }
                self.canCheckForUpdates = available
                if available { self.translationActivityChanged() }
            }.store(in: &observations)
        controller.updater.publisher(for: \.automaticallyChecksForUpdates)
            .receive(on: RunLoop.main).assign(to: &$automaticallyChecksForUpdates)
    }

    func start(model: AppModel) {
        if self.model !== model {
            self.model = model
            isTranslating = model.isTranslating
            translationObservation = model.$isTranslating.removeDuplicates()
                .receive(on: RunLoop.main)
                .sink { [weak self] _ in self?.translationActivityChanged() }
        }
        guard !started else { return }
        started = true
        controller.startUpdater()
    }

    func checkForUpdates() {
        guard !translationIsRunning, controller.updater.canCheckForUpdates else { return }
        controller.checkForUpdates(nil)
    }

    func setAutomaticallyChecksForUpdates(_ enabled: Bool) {
        // Sparkle persists this preference. Do not maintain a second defaults key.
        controller.updater.automaticallyChecksForUpdates = enabled
    }

    private var translationIsRunning: Bool { model?.isTranslating ?? false }

    private func translationActivityChanged() {
        let busy = translationIsRunning
        isTranslating = busy
        // $isTranslating emits before the stored property changes. Receiving on
        // the main run loop and reading the model again avoids resuming for a
        // stale idle event if a new translation has already started.
        if let resume = installationGate.takePendingInstallation(whileBusy: busy) {
            resume()
        } else if !busy, deferredCheck, controller.updater.canCheckForUpdates {
            deferredCheck = false
            if controller.updater.automaticallyChecksForUpdates {
                controller.updater.resetUpdateCycleAfterShortDelay()
            }
        }
    }

    private func requireIdle() throws {
        guard !translationIsRunning else {
            deferredCheck = true
            throw NSError(domain: "tech.pagebridge.updater", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "翻译正在进行，更新检查会暂缓。请在翻译完成后重试。"
            ])
        }
    }

    func updater(_ updater: SPUUpdater, mayPerform updateCheck: SPUUpdateCheck) throws {
        try requireIdle()
    }

    func updater(_ updater: SPUUpdater, shouldProceedWithUpdate updateItem: SUAppcastItem,
                 updateCheck: SPUUpdateCheck) throws {
        try requireIdle()
    }

    func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem,
                 untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
        let postponed = installationGate.postpone(whileBusy: translationIsRunning, resume: installHandler)
        if postponed { model?.statusMessage = "更新已下载，将在当前翻译结束后安装并重新打开应用。" }
        return postponed
    }

    func updaterWillRelaunchApplication(_ updater: SPUUpdater) {
        isRelaunchingForUpdate = true
    }

    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        installationGate.cancel()
        isRelaunchingForUpdate = false
    }

    func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck,
                 error: Error?) {
        // Dismissing or skipping an update may finish without an error.
        installationGate.cancel()
        isRelaunchingForUpdate = false
    }

    func allowedSystemProfileKeys(for updater: SPUUpdater) -> [String]? { [] }
}

struct UpdateMenu: View {
    @ObservedObject var updater: UpdateController

    var body: some View {
        Button("检查更新…", action: updater.checkForUpdates)
            .disabled(!updater.canCheckForUpdates || updater.isTranslating)
        Toggle("自动检查更新", isOn: Binding(
            get: { updater.automaticallyChecksForUpdates },
            set: updater.setAutomaticallyChecksForUpdates
        ))
        if updater.isTranslating {
            Text("翻译期间暂缓更新").foregroundStyle(.secondary)
        }
    }
}
