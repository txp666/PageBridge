import XCTest
@testable import PageBridgeCore

final class ServiceConfigurationTests: XCTestCase {
    func testLocalAndSecureURLsAreAccepted() throws {
        for address in ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"] {
            var config = ServiceConfiguration.defaultOllama
            config.baseURL = address
            try config.validate()
        }
        var remote = ServiceConfiguration.defaultOpenAI
        remote.baseURL = "https://models.example.com/v1"
        try remote.validate()
    }

    func testOllamaRejectsRemoteEvenOverHTTPS() {
        var config = ServiceConfiguration.defaultOllama
        config.baseURL = "https://models.example.com"
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? ServiceConfigurationError, .remoteOllamaUnsupported)
        }
    }

    func testUnsafeAndMalformedURLsAreRejected() {
        for address in ["http://models.example.com/v1", "https://user:password@example.com/v1", "https://example.com/v1?api_key=secret", "https://example.com/v1#secret", "https://example.com/v1/chat/completions/", "file:///tmp/model", "example.com/v1"] {
            var config = ServiceConfiguration.defaultOpenAI
            config.baseURL = address
            XCTAssertThrowsError(try config.validate(), address)
        }
    }

    func testRequestEndpointsAndNormalization() throws {
        var openAI = ServiceConfiguration.defaultOpenAI
        openAI.baseURL = " https://example.com/v1/ "
        XCTAssertEqual(try openAI.requestURL().absoluteString, "https://example.com/v1/chat/completions")
        var ollama = ServiceConfiguration.defaultOllama
        XCTAssertEqual(try ollama.requestURL().absoluteString, "http://127.0.0.1:11434/api/chat")
        ollama.baseURL = "http://localhost:11434/api/"
        XCTAssertEqual(try ollama.requestURL().absoluteString, "http://localhost:11434/api/chat")
        for (base, expected) in [
            ("http://localhost:11434/v1", "http://localhost:11434/api/chat"),
            ("http://localhost:11434/custom/api", "http://localhost:11434/custom/api/chat"),
            ("http://localhost:11434/custom/v1/", "http://localhost:11434/custom/api/chat")
        ] {
            ollama.baseURL = base
            XCTAssertEqual(try ollama.requestURL().absoluteString, expected)
        }
    }

    func testTimeoutAndConcurrencyBounds() {
        var config = ServiceConfiguration.defaultOllama
        config.timeoutSeconds = .infinity
        XCTAssertThrowsError(try config.validate())
        config.timeoutSeconds = 0
        XCTAssertThrowsError(try config.validate())
        config.timeoutSeconds = 60
        config.concurrency = 9
        XCTAssertThrowsError(try config.validate())
    }

    func testConfigurationContainsNoSecretField() throws {
        let data = try JSONEncoder().encode(ServiceConfiguration.defaultOpenAI)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertNil(json["apiKey"])
        XCTAssertEqual(json["kind"] as? String, "openai")
    }

    func testConnectionTestRequiresActualText() throws {
        let openAI = Data(#"{"choices":[{"message":{"content":"文档已准备就绪。"}}]}"#.utf8)
        XCTAssertEqual(try ServiceConnectionTester.parseResponse(openAI, kind: .openAI), "文档已准备就绪。")
        let ollama = Data(#"{"message":{"content":" 文件已就绪。 "}}"#.utf8)
        XCTAssertEqual(try ServiceConnectionTester.parseResponse(ollama, kind: .ollama), "文件已就绪。")
        for json in [#"{"choices":[]}"#, #"{"message":{"content":" "}}"#, #"{"models":[]}"#, "not JSON"] {
            XCTAssertThrowsError(try ServiceConnectionTester.parseResponse(Data(json.utf8), kind: .ollama))
        }
    }

    func testLiveOllamaTranslationWhenExplicitlyEnabled() async throws {
        guard ProcessInfo.processInfo.environment["PAGEBRIDGE_TEST_OLLAMA"] == "1" else {
            throw XCTSkip("Set PAGEBRIDGE_TEST_OLLAMA=1 to verify the real local model with a fixed sentence.")
        }
        var service = ServiceConfiguration.defaultOllama
        if let model = ProcessInfo.processInfo.environment["PAGEBRIDGE_TEST_OLLAMA_MODEL"] { service.model = model }
        let translation = try await ServiceConnectionTester.test(service: service, apiKey: "")
        XCTAssertFalse(translation.isEmpty)
        XCTAssertTrue(translation.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) },
                      "The fixed English sample should produce a Chinese translation.")
        print("Live Ollama connection test: \(translation)")
    }

}
