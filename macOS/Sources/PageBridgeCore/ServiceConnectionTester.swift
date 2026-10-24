import Foundation

public enum ServiceConnectionTester {
    public static func test(service: ServiceConfiguration, apiKey: String) async throws -> String {
        let request = try testRequest(service: service, apiKey: apiKey)
        let data = try await perform(request)
        return try parseResponse(data, kind: service.kind)
    }

    /// Invoked only by the explicit “获取模型” action; never on key entry or profile load.
    public static func fetchModels(service: ServiceConfiguration, apiKey: String) async throws -> [String] {
        let request = try modelsRequest(service: service, apiKey: apiKey)
        let data = try await perform(request)
        return try parseModels(data, kind: service.kind, provider: service.provider)
    }

    static func cleanAPIKey(_ apiKey: String, kind: ServiceKind) throws -> String {
        let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if kind == .openAI && key.isEmpty { throw ServiceConnectionError.missingKey }
        if key.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) {
            throw ServiceConnectionError.invalidKey
        }
        return key
    }

    static func modelsRequest(service: ServiceConfiguration, apiKey: String) throws -> URLRequest {
        let service = service.normalized()
        let key = try cleanAPIKey(apiKey, kind: service.kind)
        var request = URLRequest(url: try service.modelsURL())
        request.httpMethod = "GET"
        request.timeoutInterval = min(service.timeoutSeconds, 30)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if service.kind == .openAI { request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization") }
        return request
    }

    static func testRequest(service: ServiceConfiguration, apiKey: String) throws -> URLRequest {
        let service = service.normalized()
        try service.validate()
        let cleanKey = try cleanAPIKey(apiKey, kind: service.kind)
        var request = URLRequest(url: try service.requestURL())
        request.httpMethod = "POST"
        request.timeoutInterval = service.timeoutSeconds
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if service.kind == .openAI { request.setValue("Bearer \(cleanKey)", forHTTPHeaderField: "Authorization") }
        let messages = [
            ["role": "system", "content": "Translate the user sentence into Simplified Chinese. Reply only with the translated sentence, without explanation."],
            ["role": "user", "content": "The document is ready."]
        ]
        var body: [String: Any] = ["model": service.model, "messages": messages, "stream": false]
        if service.kind == .ollama {
            body["think"] = false
            body["options"] = ["temperature": 0.1, "num_predict": 128]
        } else if service.provider == .openAI && (service.model.hasPrefix("gpt-6") || service.model.hasPrefix("gpt-5") || service.model.hasPrefix("o")) {
            body["max_completion_tokens"] = 1024
            if service.model.hasPrefix("gpt-6") { body["reasoning_effort"] = "none" }
        } else {
            body["max_tokens"] = 1024
        }
        if service.provider == .deepSeek { body["thinking"] = ["type": "disabled"] }
        if service.provider == .alibaba && ["qwen-plus", "qwen-flash"].contains(service.model) { body["enable_thinking"] = false }
        if service.provider == .siliconFlow && service.model == "deepseek-ai/DeepSeek-V4-Flash" { body["enable_thinking"] = false }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        return request
    }

    private static func perform(_ request: URLRequest) async throws -> Data {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = request.timeoutInterval
        configuration.timeoutIntervalForResource = request.timeoutInterval
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        let session = URLSession(configuration: configuration, delegate: NoRedirectDelegate(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        do {
            let (data, response) = try await session.data(for: request)
            try Task.checkCancellation()
            guard let http = response as? HTTPURLResponse else { throw ServiceConnectionError.invalidResponse }
            guard (200...299).contains(http.statusCode) else { throw ServiceConnectionError.httpStatus(http.statusCode) }
            guard data.count <= 1_048_576 else { throw ServiceConnectionError.invalidResponse }
            return data
        } catch let error as URLError {
            if error.code == .cancelled { throw CancellationError() }
            if error.code == .timedOut { throw ServiceConnectionError.timeout }
            if [.serverCertificateUntrusted, .serverCertificateHasBadDate, .serverCertificateHasUnknownRoot, .secureConnectionFailed].contains(error.code) {
                throw ServiceConnectionError.certificate
            }
            throw ServiceConnectionError.network(error.code.rawValue)
        }
    }

    static func parseModels(_ data: Data, kind: ServiceKind, provider: ServiceProvider) throws -> [String] {
        guard data.count <= 1_048_576,
              let response = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let entries = response[kind == .ollama ? "models" : "data"] as? [[String: Any]], entries.count <= 10_000 else {
            throw ServiceConnectionError.invalidResponse
        }
        let nonChatTerms = ["embedding", "rerank", "tts", "whisper", "transcrib", "audio", "realtime", "image", "dall-e", "sora", "moderation", "babbage", "davinci", "search"]
        let identifiers = entries.compactMap { entry -> String? in
            guard let raw = (entry[kind == .ollama ? "name" : "id"] ?? entry["model"]) as? String else { return nil }
            let id = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !id.isEmpty, id.count <= 256, !id.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else { return nil }
            if let output = entry["output_modalities"] as? [String], !output.contains("text") { return nil }
            if provider == .openAI && nonChatTerms.contains(where: { id.lowercased().contains($0) }) { return nil }
            return id
        }
        let models = Set(identifiers).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        guard !models.isEmpty else { throw ServiceConnectionError.noModels }
        return models
    }

    static func parseResponse(_ data: Data, kind: ServiceKind) throws -> String {
        guard let response = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ServiceConnectionError.invalidResponse
        }
        let text: String?
        if kind == .ollama {
            text = (response["message"] as? [String: Any])?["content"] as? String
        } else {
            let choices = response["choices"] as? [[String: Any]]
            text = (choices?.first?["message"] as? [String: Any])?["content"] as? String
        }
        guard let result = text?.trimmingCharacters(in: .whitespacesAndNewlines), !result.isEmpty else {
            throw ServiceConnectionError.emptyResponse
        }
        return result
    }
}

private final class NoRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        // Never forward credentials, prompts, or model settings through a redirect.
        completionHandler(nil)
    }
}

public enum ServiceConnectionError: LocalizedError, Equatable {
    case missingKey, invalidKey, invalidResponse, emptyResponse, timeout, certificate, noModels
    case httpStatus(Int), network(Int)

    public var errorDescription: String? {
        switch self {
        case .noModels: return "未找到可用的对话模型。可选择预设模型或在高级设置中填写模型 ID。"
        case .missingKey: return "请先填写 API Key。"
        case .invalidKey: return "API Key 中不能包含换行，请检查粘贴的密钥是否完整。"
        case .invalidResponse: return "服务返回的内容不是预期的兼容 JSON 响应。请检查协议和 Base URL。"
        case .emptyResponse: return "服务没有返回有效译文；获取模型列表或收到空响应不能证明翻译可用。"
        case .timeout: return "连接超时。请检查服务是否运行、模型是否已加载，或增加请求超时。"
        case .certificate: return "无法验证服务的 TLS 证书。请检查服务地址及证书配置。"
        case .network: return "无法连接翻译服务。请检查网络、服务地址；本机 Ollama 需要处于运行状态。"
        case .httpStatus(let status):
            switch status {
            case 300...399: return "服务返回了重定向（HTTP \(status)）。为保护密钥，PageBridge 不会跟随重定向，请直接填写最终 Base URL。"
            case 401, 403: return "认证失败（HTTP \(status)）。请检查 API Key 及模型访问权限。"
            case 404: return "服务地址或模型不存在（HTTP 404）。请检查 Base URL 和模型 ID。"
            case 429: return "请求受到限流（HTTP 429）。请稍后重试，并考虑降低并发请求数。"
            case 500...599: return "模型服务暂时不可用（HTTP \(status)），请稍后重试。"
            default: return "连接测试失败（HTTP \(status)），请检查服务协议与配置。"
            }
        }
    }
}
