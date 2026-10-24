import Foundation

public enum ServiceKind: String, Codable, CaseIterable, Identifiable, Sendable {
    case openAI = "openai"
    case ollama = "ollama"

    public var id: String { rawValue }
    public var title: String { self == .openAI ? "自定义 API" : "Ollama" }
}

/// Presets verified against official provider documentation on 2026-10-24.
/// Provider identity is presentation metadata; secrets remain in Keychain by profile UUID.
public enum ServiceProvider: String, Codable, CaseIterable, Identifiable, Sendable {
    case deepSeek, alibaba, siliconFlow, openAI, ollama, custom

    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .deepSeek: return "DeepSeek"
        case .alibaba: return "阿里云百炼（北京）"
        case .siliconFlow: return "硅基流动（中国站）"
        case .openAI: return "OpenAI"
        case .ollama: return "Ollama · 本机"
        case .custom: return "自定义 API"
        }
    }
    public var kind: ServiceKind { self == .ollama ? .ollama : .openAI }
    public var baseURL: String {
        switch self {
        case .deepSeek: return "https://api.deepseek.com"
        case .alibaba: return "https://dashscope.aliyuncs.com/compatible-mode/v1"
        case .siliconFlow: return "https://api.siliconflow.cn/v1"
        case .openAI: return "https://api.openai.com/v1"
        case .ollama: return "http://127.0.0.1:11434"
        case .custom: return "https://api.example.com/v1"
        }
    }
    public var suggestedModels: [String] {
        switch self {
        case .deepSeek: return ["deepseek-flash", "deepseek-v4-pro"]
        case .alibaba: return ["qwen-plus", "qwen-flash"]
        case .siliconFlow: return ["deepseek-ai/DeepSeek-V4-Flash"]
        case .openAI: return ["gpt-6-luna"]
        case .ollama: return ["qwen3.5:35b-a3b-int4"]
        case .custom: return []
        }
    }
    public var setupHint: String {
        switch self {
        case .alibaba: return "使用北京地域的 API Key；其他地域或专属地址可选“自定义 API”。"
        case .siliconFlow: return "使用中国站 API Key；国际站可选“自定义 API”。"
        case .ollama: return "启动本机 Ollama 后，点击“获取模型”选择已下载的模型。"
        case .custom: return "连接支持 OpenAI Chat Completions 的服务。"
        default: return "服务地址已配置，选择模型并粘贴 API Key 即可。"
        }
    }
    public static func infer(kind: ServiceKind, baseURL: String) -> Self {
        if kind == .ollama { return .ollama }
        let address = baseURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        // Exact endpoints only. Never redirect a legacy custom URL while migrating.
        if address == "https://api.deepseek.com/v1" { return .deepSeek }
        return [.deepSeek, .alibaba, .siliconFlow, .openAI].first { $0.baseURL == address } ?? .custom
    }
    public func accepts(baseURL: String) -> Bool {
        self == .custom || self == .ollama || Self.infer(kind: .openAI, baseURL: baseURL) == self
    }
}

public struct ServiceConfiguration: Codable, Identifiable, Equatable, Sendable {
    public var id: UUID
    public var name: String
    public var kind: ServiceKind
    public var provider: ServiceProvider
    public var baseURL: String
    public var model: String
    public var timeoutSeconds: Double
    public var concurrency: Int

    public init(id: UUID = UUID(), name: String = "本机 Ollama", kind: ServiceKind = .ollama,
                baseURL: String = "http://127.0.0.1:11434", model: String = "qwen3.5:35b-a3b-int4",
                timeoutSeconds: Double = 120, concurrency: Int = 1, provider: ServiceProvider? = nil) {
        self.id = id
        self.name = name
        self.kind = kind
        self.provider = provider ?? ServiceProvider.infer(kind: kind, baseURL: baseURL)
        self.baseURL = baseURL
        self.model = model
        self.timeoutSeconds = timeoutSeconds
        self.concurrency = concurrency
    }

    private enum CodingKeys: String, CodingKey {
        case id, name, kind, provider, baseURL, model, timeoutSeconds, concurrency
    }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(UUID.self, forKey: .id)
        name = try values.decode(String.self, forKey: .name)
        kind = try values.decode(ServiceKind.self, forKey: .kind)
        baseURL = try values.decode(String.self, forKey: .baseURL)
        model = try values.decode(String.self, forKey: .model)
        timeoutSeconds = try values.decode(Double.self, forKey: .timeoutSeconds)
        concurrency = try values.decode(Int.self, forKey: .concurrency)
        let stored = try values.decodeIfPresent(String.self, forKey: .provider)
        provider = stored.flatMap(ServiceProvider.init(rawValue:)) ?? ServiceProvider.infer(kind: kind, baseURL: baseURL)
        if provider.kind != kind || !provider.accepts(baseURL: baseURL) {
            provider = ServiceProvider.infer(kind: kind, baseURL: baseURL)
        }
    }

    public static var defaultOllama: Self { Self() }

    public static func preset(_ provider: ServiceProvider) -> Self {
        Self(name: provider == .ollama ? "本机 Ollama" : provider.title,
             kind: provider.kind, baseURL: provider.baseURL,
             model: provider.suggestedModels.first ?? "",
             timeoutSeconds: 120,
             concurrency: provider == .ollama ? 1 : 2, provider: provider)
    }

    /// A key may be reused only for the same exact destination and provider.
    /// Include the URL path because different tenants can share a host.
    public func hasSameCredentialDestination(as other: Self) -> Bool {
        provider == other.provider && kind == other.kind && normalized().baseURL == other.normalized().baseURL
    }

    public func selectingProvider(_ selected: ServiceProvider) -> Self {
        let template = Self.preset(selected)
        var copy = self
        copy.provider = selected; copy.kind = template.kind
        copy.baseURL = template.baseURL; copy.model = template.model
        copy.timeoutSeconds = template.timeoutSeconds; copy.concurrency = template.concurrency
        return copy
    }

    public static var defaultOpenAI: Self {
        Self(name: "自定义 API", kind: .openAI, baseURL: "https://api.example.com/v1", model: "translation-model", timeoutSeconds: 60, concurrency: 2)
    }

    public var isLocal: Bool {
        guard let host = URLComponents(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines))?.host else { return false }
        return Self.isLoopback(host)
    }

    public var displayHost: String {
        URLComponents(string: baseURL)?.host ?? baseURL
    }

    public func normalized() -> Self {
        var copy = self
        copy.name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        copy.baseURL = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        while copy.baseURL.hasSuffix("/") { copy.baseURL.removeLast() }
        copy.model = model.trimmingCharacters(in: .whitespacesAndNewlines)
        return copy
    }

    public func validate() throws {
        try validateConnection()
        let service = normalized()
        guard !service.name.isEmpty, service.name.count <= 48 else { throw ServiceConfigurationError.invalidName }
        guard !service.model.isEmpty, service.model.count <= 256,
              !service.model.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else { throw ServiceConfigurationError.invalidModel }
    }

    public func validateConnection() throws {
        let service = normalized()
        guard let components = URLComponents(string: service.baseURL),
              let scheme = components.scheme?.lowercased(), ["https", "http"].contains(scheme),
              let host = components.host, !host.isEmpty, components.url != nil else {
            throw ServiceConfigurationError.invalidURL
        }
        guard components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil else {
            throw ServiceConfigurationError.credentialsInURL
        }
        guard scheme == "https" || Self.isLoopback(host) else { throw ServiceConfigurationError.insecureRemoteURL }
        guard kind != .ollama || Self.isLoopback(host) else { throw ServiceConfigurationError.remoteOllamaUnsupported }
        guard !components.path.lowercased().hasSuffix("/chat/completions") else { throw ServiceConfigurationError.fullEndpoint }
        guard provider.kind == kind, provider.accepts(baseURL: service.baseURL) else { throw ServiceConfigurationError.providerMismatch }
        guard timeoutSeconds.isFinite, (10...600).contains(timeoutSeconds) else { throw ServiceConfigurationError.invalidTimeout }
        guard (1...8).contains(concurrency) else { throw ServiceConfigurationError.invalidConcurrency }
    }

    public func requestURL() throws -> URL {
        try validate()
        let service = normalized()
        guard let base = URL(string: service.baseURL) else { throw ServiceConfigurationError.invalidURL }
        if kind == .openAI { return base.appendingPathComponent("chat/completions") }
        // Accept native and OpenAI-compatible Ollama base paths, retaining any proxy prefix.
        // /, /api, /v1 -> /api/chat; /custom/api or /custom/v1 -> /custom/api/chat.
        if base.lastPathComponent == "api" { return base.appendingPathComponent("chat") }
        let root = base.lastPathComponent == "v1" ? base.deletingLastPathComponent() : base
        return root.appendingPathComponent("api/chat")
    }

    public func modelsURL() throws -> URL {
        try validateConnection()
        guard let base = URL(string: normalized().baseURL) else { throw ServiceConfigurationError.invalidURL }
        if kind == .ollama {
            if base.lastPathComponent == "api" { return base.appendingPathComponent("tags") }
            let root = base.lastPathComponent == "v1" ? base.deletingLastPathComponent() : base
            return root.appendingPathComponent("api/tags")
        }
        let endpoint = base.appendingPathComponent("models")
        if provider == .siliconFlow {
            var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false)!
            components.queryItems = [URLQueryItem(name: "sub_type", value: "chat")]
            return components.url!
        }
        return endpoint
    }

    private static func isLoopback(_ host: String) -> Bool {
        ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host.lowercased())
    }
}

public enum ServiceConfigurationError: LocalizedError, Equatable {
    case invalidName, invalidURL, credentialsInURL, insecureRemoteURL, remoteOllamaUnsupported, fullEndpoint
    case invalidModel, invalidTimeout, invalidConcurrency, providerMismatch

    public var errorDescription: String? {
        switch self {
        case .providerMismatch: return "厂商与服务地址不匹配；修改服务地址请使用自定义 API。"
        case .invalidName: return "请输入 1–48 个字符的配置名称。"
        case .invalidURL: return "请输入完整的 HTTP 或 HTTPS 服务地址。"
        case .credentialsInURL: return "服务地址不能包含用户名、密码、查询参数或锚点；API Key 请填入独立字段。"
        case .insecureRemoteURL: return "远程服务必须使用 HTTPS；HTTP 仅支持本机 localhost、127.0.0.1 或 ::1。"
        case .remoteOllamaUnsupported: return "Ollama 仅支持本机 localhost、127.0.0.1 或 ::1。远程模型请使用自定义 API 配置。"
        case .fullEndpoint: return "请填写 Base URL，并移除末尾的 /chat/completions。"
        case .invalidModel: return "请输入有效的模型 ID（不超过 256 个字符）。"
        case .invalidTimeout: return "请求超时应为 10–600 秒。"
        case .invalidConcurrency: return "最大并发请求应为 1–8。"
        }
    }
}
