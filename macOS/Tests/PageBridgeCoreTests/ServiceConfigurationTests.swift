import XCTest
@testable import PageBridgeCore

final class ServiceConfigurationTests: XCTestCase {
    private var customAPI: ServiceConfiguration {
        var config = ServiceConfiguration.preset(.custom); config.model = "translation-model"; return config
    }

    func testLocalAndSecureURLsAreAccepted() throws {
        for address in ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"] {
            var config = ServiceConfiguration.preset(.ollama)
            config.baseURL = address
            try config.validate()
        }
        var remote = customAPI
        remote.baseURL = "https://models.example.com/v1"
        try remote.validate()
    }

    func testOllamaRejectsRemoteEvenOverHTTPS() {
        var config = ServiceConfiguration.preset(.ollama)
        config.baseURL = "https://models.example.com"
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? ServiceConfigurationError, .remoteOllamaUnsupported)
        }
    }

    func testUnsafeAndMalformedURLsAreRejected() {
        for address in ["http://models.example.com/v1", "https://user:password@example.com/v1", "https://example.com/v1?api_key=secret", "https://example.com/v1#secret", "https://example.com/v1/chat/completions/", "file:///tmp/model", "example.com/v1"] {
            var config = customAPI
            config.baseURL = address
            XCTAssertThrowsError(try config.validate(), address)
        }
    }

    func testRequestEndpointsAndNormalization() throws {
        var openAI = customAPI
        openAI.baseURL = " https://example.com/v1/ "
        XCTAssertEqual(try openAI.requestURL().absoluteString, "https://example.com/v1/chat/completions")
        var ollama = ServiceConfiguration.preset(.ollama)
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
        var config = ServiceConfiguration.preset(.ollama)
        config.timeoutSeconds = .infinity
        XCTAssertThrowsError(try config.validate())
        config.timeoutSeconds = 0
        XCTAssertThrowsError(try config.validate())
        config.timeoutSeconds = 60
        config.concurrency = 9
        XCTAssertThrowsError(try config.validate())
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
}
