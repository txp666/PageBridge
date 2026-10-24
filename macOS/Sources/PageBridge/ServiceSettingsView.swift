import SwiftUI
import PageBridgeCore

@MainActor
struct ServiceSettingsView: View {
    @ObservedObject var model: AppModel
    @State private var draft = ServiceConfiguration.defaultOllama
    @State private var baseline = ServiceConfiguration.defaultOllama
    @State private var apiKey = ""
    @State private var baselineKey = ""
    @State private var showKey = false
    @State private var advanced = false
    @State private var initialized = false
    @State private var isNew = false
    @State private var keyLoadFailed = false
    @State private var formError: String?
    @State private var notice: String?
    @State private var testResult: String?
    @State private var testCompletedAt: Date?
    @State private var testError: String?
    @State private var testing = false
    @State private var testTask: Task<Void, Never>?
    @State private var testRevision = UUID()
    @State private var availableModels: [String] = []
    @State private var modelListLoaded = false
    @State private var manualModel = false
    @State private var discovering = false
    @State private var modelListNotice: String?
    @State private var modelListError: String?
    @State private var modelsTask: Task<Void, Never>?
    @State private var modelsRevision = UUID()
    @State private var pendingAction: (() -> Void)?
    @State private var discardPresented = false
    @State private var deletePresented = false

    private var dirty: Bool { isNew || draft != baseline || apiKey != baselineKey }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            HStack(alignment: .top, spacing: 0) {
                profiles
                    .frame(width: 205)
                Divider()
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        connectionCard
                        testCard
                        privacyNote
                    }
                    .padding(26)
                    .frame(maxWidth: 760)
                    .frame(maxWidth: .infinity)
                }
                .background(Color(nsColor: .windowBackgroundColor))
            }
            Divider()
            footer
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
        .interactiveDismissDisabled(dirty || testing || discovering)
        .onAppear {
            guard !initialized else { return }
            initialized = true
            if let service = model.activeService ?? model.services.first { load(service) }
            else { newService(.ollama) }
        }
        .onChange(of: draft) { _, _ in invalidateTest(); if dirty { notice = nil } }
        .onChange(of: apiKey) { _, value in
            invalidateTest(); invalidateModels(); if dirty { notice = nil }
            if !value.isEmpty { keyLoadFailed = false; formError = nil }
        }
        .onDisappear { testTask?.cancel(); modelsTask?.cancel() }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("PageBridge.RequestSettingsClose"))) { _ in
            guarded { model.settingsPresented = false }
        }
        .alert("放弃未保存的更改？", isPresented: $discardPresented) {
            Button("继续编辑", role: .cancel) { pendingAction = nil }
            Button("放弃更改", role: .destructive) {
                let action = pendingAction
                pendingAction = nil
                action?()
            }
        } message: {
            Text("当前修改尚未保存，离开后将丢失。")
        }
        .alert("删除“\(draft.name)”？", isPresented: $deletePresented) {
            Button("取消", role: .cancel) {}
            Button("删除服务", role: .destructive) { deleteSelected() }
        } message: {
            Text("这会删除此服务配置及其钥匙串密钥。已有文档和正在运行的任务不受影响。")
        }
    }

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 7) {
                Text("翻译服务").font(.system(size: 25, weight: .semibold))
                Text("选择厂商和模型，填入 API Key 即可开始翻译。")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
            }
            Spacer()
            Button { guarded { model.settingsPresented = false } } label: {
                Label("返回文档", systemImage: "arrow.left")
                    .font(.system(size: 12, weight: .medium))
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("关闭翻译服务设置")
            .keyboardShortcut(.cancelAction)
        }
        .padding(.horizontal, 28).padding(.vertical, 24)
    }

    private var profiles: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("我的服务").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                Spacer()
                Menu {
                    ForEach(ServiceProvider.allCases) { provider in
                        Button(provider.title) { guarded { newService(provider) } }
                    }
                } label: { Image(systemName: "plus") }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .accessibilityLabel("添加服务配置")
            }
            .padding(.horizontal, 9)
            ScrollView {
                VStack(spacing: 5) {
                    ForEach(model.services) { service in
                        profileRow(service, isDraft: service.id == draft.id)
                    }
                    if isNew { profileRow(draft, isDraft: true) }
                }
            }
            Text("服务设置仅影响新任务。")
                .font(.system(size: 11)).foregroundStyle(.secondary).padding(.horizontal, 9)
        }
        .padding(16)
    }

    private func profileRow(_ service: ServiceConfiguration, isDraft: Bool) -> some View {
        Button {
            guard service.id != draft.id else { return }
            guarded { load(service) }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: service.kind == .ollama ? "desktopcomputer" : "cloud")
                    .font(.system(size: 18))
                VStack(alignment: .leading, spacing: 5) {
                    Text(isDraft ? draft.name : service.name)
                        .font(.system(size: 12, weight: .medium)).lineLimit(1)
                    Text(service.id == model.activeServiceID ? "当前使用" : (isNew && isDraft ? "尚未保存" : service.provider.title))
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                if service.id == model.activeServiceID { Image(systemName: "checkmark").font(.system(size: 10, weight: .semibold)) }
            }
            .padding(11)
            .foregroundStyle(isDraft ? Color.accentColor : Color.primary)
            .background(isDraft ? Color.accentColor.opacity(0.10) : .clear, in: RoundedRectangle(cornerRadius: 7))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var connectionCard: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("服务配置").font(.system(size: 14, weight: .semibold))
            field("厂商") {
                Picker("厂商", selection: Binding(get: { draft.provider }, set: { changeProvider($0) })) {
                    ForEach(ServiceProvider.allCases) { provider in Text(provider.title).tag(provider) }
                }
                .labelsHidden().pickerStyle(.menu)
            }
            Text(draft.provider.setupHint)
                .font(.system(size: 11)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if draft.provider == .custom {
                field("服务地址", hint: "填写 Base URL，不包含 /chat/completions。") {
                    TextField("https://api.example.com/v1", text: endpointBinding).autocorrectionDisabled()
                }
            } else {
                Label(draft.displayHost, systemImage: draft.isLocal ? "desktopcomputer" : "checkmark.shield")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            field("模型") {
                HStack(spacing: 10) {
                    Picker("模型", selection: Binding(get: { manualModel ? "__manual__" : draft.model }, set: { selectModel($0) })) {
                        if draft.model.isEmpty && !manualModel { Text("请选择模型").tag("") }
                        ForEach(modelChoices, id: \.self) { item in Text(item).tag(item) }
                        Divider()
                        Text("手动输入模型 ID…").tag("__manual__")
                    }
                    .labelsHidden().pickerStyle(.menu)
                    if discovering {
                        ProgressView().controlSize(.small)
                        Button("取消") { invalidateModels() }
                    } else {
                        Button("获取模型") { fetchModels() }
                            .disabled(testing || (draft.kind == .openAI && apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty))
                    }
                }
            }
            if manualModel {
                field("模型 ID") { TextField("输入服务提供方的模型 ID", text: $draft.model).autocorrectionDisabled() }
            }
            Text(modelListNotice ?? (modelListLoaded ? "已获取此账户的模型列表。" : "可直接使用预设模型；填入密钥后点击“获取模型”更新列表。"))
                .font(.system(size: 11)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let error = modelListError { errorLabel(error) }
            if draft.kind == .openAI {
                field("API Key", hint: "仅保存在 macOS 钥匙串。获取模型和测试连接均由你点击后发送。") {
                    HStack(spacing: 7) {
                        Image(systemName: "key").foregroundStyle(.secondary)
                        Group {
                            if showKey { TextField("粘贴厂商提供的 API Key", text: $apiKey) }
                            else { SecureField("粘贴厂商提供的 API Key", text: $apiKey) }
                        }
                        .autocorrectionDisabled()
                        Button { showKey.toggle() } label: { Image(systemName: showKey ? "eye.slash" : "eye") }
                            .buttonStyle(.plain)
                            .help(showKey ? "隐藏 API Key" : "显示 API Key")
                            .accessibilityLabel(showKey ? "隐藏 API Key" : "显示 API Key")
                    }
                }
            }
            Divider()
            DisclosureGroup(isExpanded: $advanced) {
                VStack(alignment: .leading, spacing: 14) {
                    field("配置名称") { TextField("为此服务命名", text: $draft.name) }
                    if draft.provider != .custom {
                        if draft.kind == .ollama {
                            field("本机服务地址") { TextField("http://127.0.0.1:11434", text: endpointBinding).autocorrectionDisabled() }
                        } else {
                            HStack {
                                Text(draft.baseURL).font(.system(size: 11)).foregroundStyle(.secondary).textSelection(.enabled)
                                Spacer()
                                Button("使用自定义地址") { useCustomEndpoint() }
                            }
                        }
                    }
                    HStack(alignment: .top, spacing: 20) {
                        field("请求超时（秒）") { TextField("120", value: $draft.timeoutSeconds, format: .number).frame(maxWidth: 120) }
                        field("最大并发请求") {
                            Stepper(value: $draft.concurrency, in: 1...8) {
                                Text("\(draft.concurrency)").monospacedDigit().frame(width: 24, alignment: .leading)
                            }
                        }
                    }
                    Text("本机大模型建议并发 1；在线服务的并发越高，越容易触发速率限制。")
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
                .padding(.top, 13)
            } label: {
                HStack {
                    Text("高级设置").font(.system(size: 12, weight: .medium))
                    Spacer()
                    Text("名称、地址与性能").font(.system(size: 11)).foregroundStyle(.secondary)
                }
            }
        }
        .padding(23)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.08)))
    }

    private var modelChoices: [String] {
        var models = modelListLoaded ? availableModels : draft.provider.suggestedModels
        if !draft.model.isEmpty && !models.contains(draft.model) { models.insert(draft.model, at: 0) }
        return models
    }

    private var endpointBinding: Binding<String> {
        Binding(get: { draft.baseURL }, set: { value in
            var updated = draft
            updated.baseURL = value
            updateDestination(updated)
        })
    }

    private func selectModel(_ model: String) {
        if model == "__manual__" { manualModel = true }
        else {
            manualModel = false; draft.model = model
            if modelListLoaded {
                modelListNotice = availableModels.contains(model)
                    ? "已获取 \(availableModels.count) 个模型，选择后可测试连接。"
                    : "当前模型不在返回列表中，请手动确认模型访问权限。"
            }
        }
    }

    private func field<Content: View>(_ title: String, hint: String? = nil, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
            content().font(.system(size: 14)).textFieldStyle(.roundedBorder).accessibilityLabel(title)
            if let hint {
                Text(hint).font(.system(size: 11)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var testCard: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text("连接测试").font(.system(size: 14, weight: .semibold))
                    Text("发送固定短句，验证模型能够返回实际译文。")
                        .font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
                if testing {
                    ProgressView().controlSize(.small)
                    Button("取消") { invalidateTest() }
                } else {
                    Button("测试连接") { runTest() }.controlSize(.large).disabled(discovering)
                }
            }
            Text("测试内容：The document is ready. 不发送你的 PDF。在线 API 可能产生少量费用。")
                .font(.system(size: 11)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let result = testResult {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("连接成功 · 已收到译文").font(.system(size: 12, weight: .semibold))
                        Text(result).font(.system(size: 13)).textSelection(.enabled)
                        Text("验证于 \((testCompletedAt ?? Date.now).formatted(date: .omitted, time: .shortened)) · \(draft.displayHost)")
                            .font(.system(size: 11)).foregroundStyle(.secondary)
                    }
                }
                .padding(13).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.green.opacity(0.07), in: RoundedRectangle(cornerRadius: 7))
            }
            if let error = testError { errorLabel(error) }
        }
        .padding(23)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.08)))
    }

    private var privacyNote: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: "lock.shield").font(.system(size: 17))
            Text(draft.isLocal
                 ? "当前地址指向本机。文档解析与排版在 Mac 上完成，文本发送至此本机服务。离线使用前需准备全部翻译资源和模型。"
                 : "文档解析与排版在 Mac 上完成；待翻译文本会发送至所选远程服务。请确认服务地址及其隐私政策。")
                .font(.system(size: 11)).fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(.secondary)
    }

    private var footer: some View {
        HStack(spacing: 15) {
            VStack(alignment: .leading, spacing: 3) {
                if let error = formError { errorLabel(error) }
                else if let notice {
                    Label(notice, systemImage: "checkmark.circle").foregroundStyle(.secondary)
                } else {
                    Label(dirty ? "有未保存的更改" : "密钥由 macOS 钥匙串保管", systemImage: dirty ? "circle.fill" : "lock")
                        .foregroundStyle(.secondary)
                }
            }
            .font(.system(size: 11))
            .frame(maxWidth: .infinity, alignment: .leading)
            Button(role: .destructive) { deletePresented = true } label: { Image(systemName: "trash") }
                .disabled(isNew || model.services.count <= 1)
                .help("删除服务配置")
            Button("重置") {
                if isNew, let service = model.activeService ?? model.services.first { load(service) }
                else { load(baseline) }
            }
            .disabled(!dirty)
            Button(draft.id == model.activeServiceID ? "保存配置" : "保存并使用") { save() }
                .buttonStyle(.borderedProminent)
                .disabled(keyLoadFailed || discovering || testing)
                .keyboardShortcut("s", modifiers: .command)
        }
        .controlSize(.large)
        .padding(.horizontal, 25).padding(.vertical, 16)
    }

    private func errorLabel(_ message: String) -> some View {
        Label(message, systemImage: "exclamationmark.triangle.fill")
            .font(.system(size: 12)).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
    }

    private func guarded(_ action: @escaping () -> Void) {
        if dirty { pendingAction = action; discardPresented = true }
        else { action() }
    }

    private func load(_ service: ServiceConfiguration) {
        invalidateTest(); invalidateModels()
        advanced = false
        manualModel = service.model.isEmpty || (service.provider == .custom)
        draft = service
        baseline = service
        isNew = false
        showKey = false
        notice = nil
        formError = nil
        keyLoadFailed = false
        do { apiKey = try model.key(for: service.id) }
        catch {
            apiKey = ""
            keyLoadFailed = true
            formError = error.localizedDescription
        }
        baselineKey = apiKey
    }

    private func uniqueName(_ name: String, excluding id: UUID? = nil) -> String {
        var result = name, suffix = 2
        while model.services.contains(where: { $0.id != id && $0.name == result }) {
            result = "\(name) \(suffix)"; suffix += 1
        }
        return result
    }

    private func newService(_ provider: ServiceProvider) {
        invalidateTest(); invalidateModels()
        var service = ServiceConfiguration.preset(provider)
        service.name = uniqueName(service.name)
        draft = service; baseline = service
        apiKey = ""; baselineKey = ""; isNew = true
        keyLoadFailed = false; showKey = false; formError = nil; notice = nil
        advanced = false; manualModel = provider == .custom
    }

    private func changeProvider(_ provider: ServiceProvider) {
        guard draft.provider != provider else { return }
        var updated = draft.selectingProvider(provider)
        updated.name = uniqueName(ServiceConfiguration.preset(provider).name, excluding: draft.id)
        updateDestination(updated)
        manualModel = provider == .custom
        advanced = false
    }

    private func useCustomEndpoint() {
        var updated = draft
        updated.provider = .custom
        updateDestination(updated)
        advanced = true
    }

    private func updateDestination(_ updated: ServiceConfiguration) {
        // Clear before changing the destination, so an old vendor key can never be sent
        // by a button press before SwiftUI delivers onChange callbacks.
        if !draft.hasSameCredentialDestination(as: updated) {
            apiKey = ""; showKey = false; keyLoadFailed = false
            invalidateTest(); invalidateModels()
            notice = updated.kind == .openAI ? "服务已切换，请填写对应厂商的 API Key。" : nil
        }
        draft = updated
    }

    private func save() {
        do {
            let clean = draft.normalized()
            try clean.validate()
            if model.services.contains(where: { $0.id != clean.id && $0.name == clean.name }) {
                formError = "配置名称已经存在，请使用不同名称。"; return
            }
            let cleanKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
            if clean.kind == .openAI && cleanKey.isEmpty { throw ServiceConnectionError.missingKey }
            if cleanKey.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) { throw ServiceConnectionError.invalidKey }
            try model.saveService(clean, apiKey: clean.kind == .openAI ? cleanKey : "")
            model.activateService(clean.id)
            draft = clean; baseline = clean
            apiKey = clean.kind == .ollama ? "" : cleanKey
            baselineKey = apiKey; isNew = false
            notice = "已保存并设为当前服务。仅影响新任务。"
            formError = nil
        } catch { formError = error.localizedDescription }
    }

    private func deleteSelected() {
        do {
            try model.deleteService(draft.id)
            if let next = model.activeService ?? model.services.first { load(next) }
            else { newService(.ollama) }
        } catch { formError = error.localizedDescription }
    }

    private func invalidateModels() {
        modelsTask?.cancel(); modelsTask = nil
        modelsRevision = UUID(); discovering = false
        availableModels = []; modelListLoaded = false
        modelListNotice = nil; modelListError = nil
    }

    private func fetchModels() {
        invalidateModels()
        let service = draft, key = apiKey, revision = UUID()
        modelsRevision = revision; discovering = true
        modelsTask = Task { @MainActor in
            do {
                let models = try await ServiceConnectionTester.fetchModels(service: service, apiKey: key)
                guard !Task.isCancelled, modelsRevision == revision,
                      service.hasSameCredentialDestination(as: draft), key == apiKey else { return }
                availableModels = models; modelListLoaded = true; discovering = false
                if draft.model.isEmpty { draft.model = models[0]; manualModel = false }
                modelListNotice = models.contains(draft.model)
                    ? "已获取 \(models.count) 个模型，选择后可测试连接。"
                    : "已获取 \(models.count) 个模型；当前模型不在返回列表中，请重新选择或手动确认。"
            } catch is CancellationError {
                if modelsRevision == revision { discovering = false }
            } catch {
                guard !Task.isCancelled, modelsRevision == revision else { return }
                discovering = false; modelListError = error.localizedDescription
            }
        }
    }

    private func invalidateTest() {
        testTask?.cancel(); testTask = nil
        testRevision = UUID()
        testing = false; testResult = nil; testError = nil; testCompletedAt = nil
    }

    private func runTest() {
        invalidateTest()
        let service = draft
        let key = apiKey
        let revision = UUID()
        testRevision = revision
        testing = true
        testTask = Task { @MainActor in
            do {
                let result = try await ServiceConnectionTester.test(service: service, apiKey: key)
                guard !Task.isCancelled, testRevision == revision, service == draft, key == apiKey else { return }
                testing = false; testResult = result; testCompletedAt = Date.now
            } catch is CancellationError {
                if testRevision == revision { testing = false }
            } catch {
                guard !Task.isCancelled, testRevision == revision else { return }
                testing = false; testError = error.localizedDescription
            }
        }
    }
}
