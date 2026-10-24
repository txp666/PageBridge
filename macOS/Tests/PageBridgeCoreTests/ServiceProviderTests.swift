import XCTest
@testable import PageBridgeCore

final class ServiceProviderTests: XCTestCase {
    func testAllPresetsHaveOfficialEndpointsAndUsableModels() throws {
        for provider in ServiceProvider.allCases where provider != .custom {
            let config = ServiceConfiguration.preset(provider)
            try config.validate()
            XCTAssertEqual(config.provider, provider)
            XCTAssertEqual(config.kind, provider.kind)
            XCTAssertFalse(config.model.isEmpty)
            XCTAssertEqual(try config.requestURL().host, URL(string: provider.baseURL)?.host)
        }
    }

    func testLegacyDecodePreservesProfileDestinationModelAndIdentity() throws {
        let id = UUID()
        for (endpoint, kind, expected) in [
            ("https://api.deepseek.com/v1/", "openai", ServiceProvider.deepSeek),
            ("https://dashscope.aliyuncs.com/compatible-mode/v1", "openai", .alibaba),
            ("https://api.siliconflow.cn/v1", "openai", .siliconFlow),
            ("https://api.openai.com/v1", "openai", .openAI),
            ("https://api.deepseek.com.proxy.example/v1", "openai", .custom),
            ("https://custom.example/team/v1", "openai", .custom),
            ("http://localhost:11435/custom/api", "ollama", .ollama)
        ] {
            let json: [String: Any] = ["id": id.uuidString, "name": "已有配置", "kind": kind, "baseURL": endpoint,
                                       "model": "saved-model", "timeoutSeconds": 90, "concurrency": 3]
            let config = try JSONDecoder().decode(ServiceConfiguration.self, from: JSONSerialization.data(withJSONObject: json))
            XCTAssertEqual(config.provider, expected)
            XCTAssertEqual(config.id, id)
            XCTAssertEqual(config.baseURL, endpoint)
            XCTAssertEqual(config.model, "saved-model")
            XCTAssertEqual(config.timeoutSeconds, 90)
            XCTAssertEqual(config.concurrency, 3)
        }
    }

    func testProviderSpoofCannotChangeOfficialDestination() throws {
        var config = ServiceConfiguration.preset(.deepSeek)
        config.baseURL = "https://other.example/v1"
        XCTAssertThrowsError(try config.validate()) { XCTAssertEqual($0 as? ServiceConfigurationError, .providerMismatch) }
        let decoded = try JSONDecoder().decode(ServiceConfiguration.self, from: JSONEncoder().encode(config))
        XCTAssertEqual(decoded.provider, .custom)
        XCTAssertEqual(decoded.baseURL, config.baseURL)
        XCTAssertEqual(decoded.id, config.id)
    }

    func testVendorAndTenantChangesRequireNewCredentials() {
        let existing = ServiceConfiguration.preset(.deepSeek)
        for provider in ServiceProvider.allCases where provider != .deepSeek {
            let switched = existing.selectingProvider(provider)
            XCTAssertFalse(existing.hasSameCredentialDestination(as: switched))
            XCTAssertEqual(switched.id, existing.id)
        }
        var renamed = existing
        renamed.name = "新名字"; renamed.model = "deepseek-v4-pro"; renamed.baseURL += "/"
        XCTAssertTrue(existing.hasSameCredentialDestination(as: renamed))
        var custom = ServiceConfiguration.defaultOpenAI
        custom.baseURL = "https://gateway.example/team-a/v1"
        var otherTenant = custom
        otherTenant.baseURL = "https://gateway.example/team-b/v1"
        XCTAssertFalse(custom.hasSameCredentialDestination(as: otherTenant))
    }

    func testModelsEndpointWorksWithoutSelectedModel() throws {
        var config = ServiceConfiguration.preset(.custom)
        config.baseURL = "https://gateway.example/v1"
        XCTAssertEqual(try config.modelsURL().absoluteString, "https://gateway.example/v1/models")
        XCTAssertThrowsError(try config.validate())
        config = .preset(.siliconFlow)
        XCTAssertEqual(try config.modelsURL().absoluteString, "https://api.siliconflow.cn/v1/models?sub_type=chat")
        for (base, path) in [("http://localhost:11434", "/api/tags"), ("http://localhost:11434/api/", "/api/tags"),
                             ("http://localhost:11434/custom/v1", "/custom/api/tags")] {
            config = .preset(.ollama); config.baseURL = base
            XCTAssertEqual(try config.modelsURL().path, path)
        }
    }

    func testDiscoveryIsGETWithoutPromptAndOllamaNeverReceivesKey() throws {
        let remote = try ServiceConnectionTester.modelsRequest(service: .preset(.deepSeek), apiKey: " fixture-key ")
        XCTAssertEqual(remote.httpMethod, "GET")
        XCTAssertNil(remote.httpBody)
        XCTAssertEqual(remote.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-key")
        let local = try ServiceConnectionTester.modelsRequest(service: .preset(.ollama), apiKey: "must-not-send")
        XCTAssertNil(local.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(local.url?.path, "/api/tags")
    }

    func testMissingAndControlCharacterKeysRejectedBeforeNetworking() {
        for key in ["", " ", "fixture\nsecret", "fixture\rsecret", "fixture\tsecret"] {
            XCTAssertThrowsError(try ServiceConnectionTester.modelsRequest(service: .preset(.deepSeek), apiKey: key))
            XCTAssertThrowsError(try ServiceConnectionTester.testRequest(service: .preset(.deepSeek), apiKey: key))
        }
    }

    func testProviderTestBodiesUseSupportedCompletionParameters() throws {
        for provider in [ServiceProvider.openAI, .deepSeek, .alibaba, .siliconFlow] {
            let request = try ServiceConnectionTester.testRequest(service: .preset(provider), apiKey: "fixture")
            let body = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(request.httpBody)) as? [String: Any])
            XCTAssertEqual(body["stream"] as? Bool, false)
            XCTAssertNil(body["apiKey"])
            XCTAssertEqual(request.httpMethod, "POST")
            switch provider {
            case .openAI:
                XCTAssertNil(body["max_tokens"])
                XCTAssertEqual(body["max_completion_tokens"] as? Int, 1024)
                XCTAssertEqual(body["reasoning_effort"] as? String, "none")
            case .deepSeek:
                XCTAssertEqual((body["thinking"] as? [String: String])?["type"], "disabled")
            case .alibaba:
                XCTAssertEqual(body["enable_thinking"] as? Bool, false)
            default:
                XCTAssertNil(body["reasoning_effort"])
                XCTAssertNil(body["thinking"])
            }
        }
    }

    func testModelResponseDeduplicatesAndRejectsInvalidIDs() throws {
        let data = Data(#"{"data":[{"id":"b-model"},{"id":"a-model"},{"id":"a-model"},{"id":""},{"id":"bad\nmodel"},{"id":42},{"id":"no-text","output_modalities":["image"]}]}"#.utf8)
        XCTAssertEqual(try ServiceConnectionTester.parseModels(data, kind: .openAI, provider: .custom), ["a-model", "b-model"])
        let local = Data(#"{"models":[{"name":"qwen3.5:35b"},{"name":"gemma:12b"}]}"#.utf8)
        XCTAssertEqual(try ServiceConnectionTester.parseModels(local, kind: .ollama, provider: .ollama), ["gemma:12b", "qwen3.5:35b"])
    }

    func testNonChatModelsAreNotOfferedForOpenAI() throws {
        let data = Data(#"{"data":[{"id":"gpt-6-luna"},{"id":"text-embedding-3-small"},{"id":"gpt-image-1"},{"id":"gpt-realtime"},{"id":"whisper-1"},{"id":"sora-2"}]}"#.utf8)
        XCTAssertEqual(try ServiceConnectionTester.parseModels(data, kind: .openAI, provider: .openAI), ["gpt-6-luna"])
    }

    func testInvalidModelListDoesNotReplacePresets() {
        for value in ["{}", "not JSON", #"{"data":[]}"#, #"{"choices":[{"message":{"content":"text"}}]}"#] {
            XCTAssertThrowsError(try ServiceConnectionTester.parseModels(Data(value.utf8), kind: .openAI, provider: .custom))
        }
    }

    func testProviderRoundTripContainsNoSecrets() throws {
        let config = ServiceConfiguration.preset(.alibaba)
        let data = try JSONEncoder().encode(config)
        XCTAssertEqual(try JSONDecoder().decode(ServiceConfiguration.self, from: data), config)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object["provider"] as? String, "alibaba")
        XCTAssertNil(object["apiKey"])
        let engine = EngineService(configuration: config, apiKey: "fixture")
        XCTAssertEqual(engine.provider, "alibaba")
    }
}
