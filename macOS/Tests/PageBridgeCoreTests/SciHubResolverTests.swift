import Foundation
import XCTest
@testable import PageBridgeCore

final class SciHubResolverTests: XCTestCase {
    private let doi = "10.1371/journal.pcbi.1003833"
    private let pageURL = URL(string: "https://sci-hub.ru/10.1371/journal.pcbi.1003833")!
    private var hosts: [String] = []

    override func tearDown() {
        hosts.forEach(SciHubFixtureProtocol.remove)
        hosts = []
        super.tearDown()
    }

    func testDOINormalizationAndArticleURLKeepReservedCharactersInPath() throws {
        for input in ["10.1234/A+B&c(1)?#é", " DOI: 10.1234/A+B&c(1)?#é ",
                      "https://doi.org/10.1234/A%2BB%26c(1)%3F%23%C3%A9"] {
            let url = try SciHubResolver.articleURL(doi: input, baseURL: " https://sci-hub.ru/ ")
            XCTAssertEqual(url.path, "/10.1234/a+b&c(1)?#é")
            XCTAssertNil(url.query)
            XCTAssertNil(url.fragment)
            XCTAssertEqual(url.host, "sci-hub.ru")
        }
        XCTAssertNil(SciHubResolver.normalizedDOI("article title"))
        XCTAssertNil(SciHubResolver.normalizedDOI("10.1234/has a space"))
        XCTAssertNil(SciHubResolver.normalizedDOI("https://user:secret@doi.org/10.1234/a"))
    }

    func testInvalidEndpointsAndDOIsAreRejected() {
        for root in ["http://sci-hub.ru", "https://user:secret@sci-hub.ru", "https://127.0.0.1", "https://198.18.0.17",
                     "https://reader.local", "https://sci-hub.ru:8080", "https://sci-hub.ru/?api_key=x", "https://sci-hub.ru/#x"] {
            XCTAssertThrowsError(try SciHubResolver.articleURL(doi: doi, baseURL: root), root)
        }
        XCTAssertThrowsError(try SciHubResolver.articleURL(doi: "not a DOI", baseURL: "https://sci-hub.ru"))
    }

    func testCurrentMetadataShapeResolvesRelativePDFDespiteUnrelatedCaptchaWidget() throws {
        let html = """
        <meta name="citation_doi" content="10.1371/journal.pcbi.1003833">
        <meta name="citation_pdf_url" content="/storage/2024/6962/2e35ec4bd92f2cbc5eedbeb0458ffc5a/rougier2014.pdf">
        <script src="/scripts/altcha.min.js"></script>
        <object type="application/pdf" data="/storage/fallback.pdf#navpanes=0"></object>
        <altcha-widget challengeurl="/captcha/challenge"></altcha-widget>
        """
        let url = try SciHubResolver.pdfURL(in: html, pageURL: pageURL, expectedDOI: doi)
        XCTAssertEqual(url.absoluteString, "https://sci-hub.ru/storage/2024/6962/2e35ec4bd92f2cbc5eedbeb0458ffc5a/rougier2014.pdf")
    }

    func testPDFMetadataDecodesEntitiesAndProtocolRelativeURL() throws {
        let html = #"<META CONTENT='//cdn.example.org/download.pdf?a=1&amp;b=&#x32;&#35;view=FitH' NAME='citation_pdf_url'>"#
        let url = try SciHubResolver.pdfURL(in: html, pageURL: pageURL, expectedDOI: doi)
        XCTAssertEqual(url.absoluteString, "https://cdn.example.org/download.pdf?a=1&b=2")
    }

    func testObjectEmbedAndIframeFallbacksResolveWithoutExecutingScripts() throws {
        let cases = [
            #"<object type='application/pdf' data='../storage/article.pdf#navpanes=0'></object>"#,
            #"<embed type=application/pdf src='/storage/article.pdf'>"#,
            #"<iframe src='/storage/article.pdf'></iframe>"#,
            #"<iframe id=pdf src='/storage/article.pdf'></iframe>"#
        ]
        for html in cases {
            let url = try SciHubResolver.pdfURL(in: html, pageURL: pageURL, expectedDOI: doi)
            XCTAssertEqual(url.path, "/storage/article.pdf")
            XCTAssertNil(url.fragment)
        }
        let scriptOnly = #"<script>var html = '<iframe src="/fake.pdf">';</script><!-- <meta name="citation_pdf_url" content="/comment.pdf"> -->"#
        XCTAssertThrowsError(try SciHubResolver.pdfURL(in: scriptOnly, pageURL: pageURL, expectedDOI: doi))
    }

    func testDOIMismatchPreventsOpeningAnotherDocument() throws {
        let html = #"<meta name="citation_doi" content="10.1234/other"><meta name="citation_pdf_url" content="/wrong.pdf">"#
        XCTAssertThrowsError(try SciHubResolver.pdfURL(in: html, pageURL: pageURL, expectedDOI: doi)) {
            XCTAssertTrue($0.localizedDescription.contains("DOI"))
        }
        let matching = #"<meta name="citation_doi" content="https://doi.org/10.1371/JOURNAL.PCBI.1003833"><meta name="citation_pdf_url" content="/correct.pdf">"#
        XCTAssertEqual(try SciHubResolver.pdfURL(in: matching, pageURL: pageURL, expectedDOI: doi).path, "/correct.pdf")
    }

    func testCaptchaAndMissingPDFProduceDistinctActionableErrors() {
        for (html, expected) in [("<altcha-widget challengeurl='/captcha/challenge'>", "验证码"),
                                 ("<h1>Please verify you are human</h1>", "验证"),
                                 ("<h1>Article not found</h1>", "未返回")] {
            XCTAssertThrowsError(try SciHubResolver.pdfURL(in: html, pageURL: pageURL, expectedDOI: doi)) {
                XCTAssertTrue($0.localizedDescription.contains(expected), $0.localizedDescription)
            }
        }
    }

    func testUnsafePDFTargetsAreRejected() {
        for url in ["http://example.org/a.pdf", "https://127.0.0.1/a.pdf", "https://198.18.0.17/a.pdf",
                    "https://192.168.1.1/a.pdf", "file:///tmp/a.pdf", "javascript:alert(1)",
                    "https://user:secret@example.org/a.pdf", "https://reader.local/a.pdf"] {
            let html = "<meta name='citation_pdf_url' content='\(url)'>"
            XCTAssertThrowsError(try SciHubResolver.pdfURL(in: html, pageURL: pageURL, expectedDOI: doi), url)
        }
    }

    func testAnonymousNetworkRequestResolvesHTMLAndKeepsHostnameUnderFakeIPDNS() async throws {
        let (resolver, root) = fixture(html: #"<meta name='citation_pdf_url' content='/article.pdf'>"#) { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/10.1371/journal.pcbi.1003833")
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            XCTAssertNil(request.value(forHTTPHeaderField: "Cookie"))
            XCTAssertNil(request.value(forHTTPHeaderField: "X-Api-Key"))
            XCTAssertFalse(request.httpShouldHandleCookies)
            XCTAssertEqual(request.timeoutInterval, 25)
        }
        let result = try await resolver.resolve(doi: doi, baseURL: root)
        XCTAssertEqual(result.absoluteString, root + "/article.pdf")
    }

    func testPrivateDNSNeverStartsNetworkRequest() async {
        let (resolver, root) = fixture(html: "", addresses: ["192.168.1.1"]) { _ in XCTFail("Private destination should not be requested") }
        do { _ = try await resolver.resolve(doi: doi, baseURL: root); XCTFail("Expected rejection") }
        catch { XCTAssertTrue(error.localizedDescription.contains("私有网络")) }
    }

    func testHTTPStatusAndOversizedHTMLFailClearly() async {
        for (status, expected) in [(403, "验证"), (404, "未找到"), (429, "过于频繁"), (503, "不可用")] {
            let (resolver, root) = fixture(html: "", status: status)
            do { _ = try await resolver.resolve(doi: doi, baseURL: root); XCTFail("Expected HTTP rejection") }
            catch { XCTAssertTrue(error.localizedDescription.contains(expected), error.localizedDescription) }
        }
        let (resolver, root) = fixture(html: "", headers: ["Content-Length": "2097153"])
        do { _ = try await resolver.resolve(doi: doi, baseURL: root); XCTFail("Expected HTML limit") }
        catch { XCTAssertTrue(error.localizedDescription.contains("上限")) }
    }

    func testActualByteLimitStopsChunkedHTML() async {
        let (resolver, root) = fixture(html: String(repeating: "x", count: 2 * 1_024 * 1_024 + 1))
        do { _ = try await resolver.resolve(doi: doi, baseURL: root); XCTFail("Expected stream limit") }
        catch { XCTAssertTrue(error.localizedDescription.contains("上限")) }
    }

    func testCancellationAndTimeoutRemainDistinct() async {
        let started = expectation(description: "Request started")
        let (resolver, root) = fixture(html: "", stalls: true) { _ in started.fulfill() }
        let task = Task { try await resolver.resolve(doi: self.doi, baseURL: root) }
        await fulfillment(of: [started], timeout: 2)
        task.cancel()
        do { _ = try await task.value; XCTFail("Expected cancellation") }
        catch { XCTAssertTrue(error is CancellationError, error.localizedDescription) }
        let (timeoutResolver, timeoutRoot) = fixture(html: "", error: URLError(.timedOut))
        do { _ = try await timeoutResolver.resolve(doi: doi, baseURL: timeoutRoot); XCTFail("Expected timeout") }
        catch { XCTAssertTrue(error.localizedDescription.contains("超时")) }
    }

    func testPDFResponseReturnsFinalURLForExistingDownloader() async throws {
        let (resolver, root) = fixture(html: "%PDF-1.7", headers: ["Content-Type": "application/pdf"])
        let result = try await resolver.resolve(doi: doi, baseURL: root)
        XCTAssertEqual(result.path, "/" + doi)
    }

    private func fixture(html: String, status: Int = 200, headers: [String: String] = [:],
                         addresses: [String] = ["198.18.0.17"], stalls: Bool = false, error: Error? = nil,
                         inspect: @escaping (URLRequest) -> Void = { _ in }) -> (SciHubResolver, String) {
        let host = "fixture-\(UUID().uuidString.lowercased()).example.org"
        hosts.append(host)
        SciHubFixtureProtocol.register(host, fixture: .init(data: Data(html.utf8), status: status,
                                                          headers: headers, stalls: stalls, error: error, inspect: inspect))
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [SciHubFixtureProtocol.self]
        config.httpAdditionalHeaders = ["Authorization": "Bearer test-secret", "Cookie": "test=secret", "X-Api-Key": "secret"]
        return (SciHubResolver(configuration: config, addressResolver: { _ in addresses }), "https://" + host)
    }
}

private final class SciHubFixtureProtocol: URLProtocol {
    struct Fixture {
        let data: Data
        let status: Int
        let headers: [String: String]
        let stalls: Bool
        let error: Error?
        let inspect: (URLRequest) -> Void
    }
    private static let lock = NSLock()
    private static var fixtures: [String: Fixture] = [:]
    static func register(_ host: String, fixture: Fixture) { lock.lock(); defer { lock.unlock() }; fixtures[host] = fixture }
    static func remove(_ host: String) { lock.lock(); defer { lock.unlock() }; fixtures.removeValue(forKey: host) }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.lock.lock()
        let fixture = Self.fixtures[request.url?.host ?? ""]
        Self.lock.unlock()
        guard let fixture else { client?.urlProtocol(self, didFailWithError: URLError(.badURL)); return }
        fixture.inspect(request)
        if let error = fixture.error { client?.urlProtocol(self, didFailWithError: error); return }
        var headers = ["Content-Type": "text/html; charset=utf-8"]
        headers.merge(fixture.headers) { _, new in new }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: fixture.status,
                                                           httpVersion: "HTTP/1.1", headerFields: headers)!, cacheStoragePolicy: .notAllowed)
        if fixture.stalls { return }
        client?.urlProtocol(self, didLoad: fixture.data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
