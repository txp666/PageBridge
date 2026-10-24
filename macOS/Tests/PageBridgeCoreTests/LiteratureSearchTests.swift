import Foundation
import XCTest
@testable import PageBridgeCore

final class LiteratureSearchTests: XCTestCase {
    private var fixtureIDs: [String] = []
    private var sessions: [URLSession] = []

    override func tearDown() {
        sessions.forEach { $0.invalidateAndCancel() }
        fixtureIDs.forEach { LiteratureURLProtocol.remove($0) }
        sessions = []
        fixtureIDs = []
        super.tearDown()
    }

    func testKeywordSearchBuildsBoundedAnonymousRequestAndMapsMetadata() async throws {
        var record = work("W1", title: "  Better\n Figures  ")
        record["doi"] = "https://doi.org/10.1371/JOURNAL.PCBI.1003833"
        record["publication_year"] = 2014
        record["authorships"] = [["author": ["display_name": "Nicolas Rougier"]], ["author": ["display_name": "Michael Droettboom"]]]
        record["primary_location"] = ["is_oa": true, "landing_page_url": "https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1003833",
                                       "pdf_url": "https://journals.plos.org/ploscompbiol/article/file?type=printable",
                                       "source": ["display_name": "PLOS Computational Biology"]]
        let client = fixture(results: [record], count: 1) { request in
            XCTAssertEqual(request.url?.host, "api.openalex.org")
            XCTAssertEqual(request.url?.path, "/works")
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.timeoutInterval, 25)
            XCTAssertNil(request.httpBody)
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            let query = self.queryItems(request)
            XCTAssertEqual(query["search"], "better figures 科研")
            XCTAssertEqual(query["per_page"], "20")
            XCTAssertEqual(query["page"], "1")
            XCTAssertNil(query["filter"])
            XCTAssertFalse(query["select"]?.contains("abstract") ?? true)
        }
        let page = try await client.search(" better figures 科研 ")
        let result = try XCTUnwrap(page.works.first)
        XCTAssertEqual(result.title, "Better Figures")
        XCTAssertEqual(result.authors, ["Nicolas Rougier", "Michael Droettboom"])
        XCTAssertEqual(result.year, 2014)
        XCTAssertEqual(result.publication, "PLOS Computational Biology")
        XCTAssertEqual(result.doi, "10.1371/journal.pcbi.1003833")
        XCTAssertEqual(result.pdfURL?.query, "type=printable")
        XCTAssertEqual(result.landingURL?.host, "journals.plos.org")
        XCTAssertFalse(page.hasMore)
    }

    func testDOIFormsUseExactFilterAndPreserveReservedQueryCharacters() async throws {
        for input in ["10.1234/A+B&c(1)", "DOI: 10.1234/A+B&c(1)", "https://doi.org/10.1234/A%2BB%26c(1)?download=1", "http://dx.doi.org/10.1234/A%2BB%26c(1)"] {
            let client = fixture(results: [], count: 0) { request in
                let query = self.queryItems(request)
                XCTAssertEqual(query["filter"], "doi:https://doi.org/10.1234/a+b&c(1),is_oa:true", input)
                XCTAssertNil(query["search"], input)
                XCTAssertTrue(request.url?.absoluteString.contains("%2B") == true)
                XCTAssertTrue(request.url?.absoluteString.contains("%26") == true)
                XCTAssertEqual(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.filter { $0.name == "filter" }.count, 1)
            }
            let result = try await client.search(input, openAccessOnly: true)
            XCTAssertTrue(result.works.isEmpty)
        }
    }

    func testOnlyExplicitOpenAccessPDFLocationsAreAccepted() async throws {
        var closed = work("closed")
        closed["open_access"] = ["is_oa": true]
        closed["primary_location"] = ["is_oa": false, "pdf_url": "https://publisher.org/private.pdf"]
        closed["best_oa_location"] = ["pdf_url": "https://publisher.org/unconfirmed.pdf", "landing_page_url": "https://publisher.org/article"]
        var landingOnly = work("landing")
        landingOnly["best_oa_location"] = ["is_oa": true, "landing_page_url": "https://publisher.org/article.pdf"]
        var opened = work("opened")
        opened["best_oa_location"] = ["is_oa": true, "pdf_url": "http://publisher.org/insecure.pdf"]
        opened["locations"] = [["is_oa": false, "pdf_url": "https://publisher.org/private.pdf"],
                               ["is_oa": true, "pdf_url": "https://repository.org/open.pdf"]]
        let page = try await fixture(results: [closed, landingOnly, opened], count: 3).search("figures")
        XCTAssertNil(page.works[0].pdfURL)
        XCTAssertNil(page.works[1].pdfURL)
        XCTAssertEqual(page.works[2].pdfURL?.absoluteString, "https://repository.org/open.pdf")
        XCTAssertEqual(page.works[0].landingURL?.absoluteString, "https://publisher.org/article")
    }

    func testUnsafeMetadataURLsAreNotExposed() async throws {
        let unsafe = ["http://publisher.org/document.pdf", "https://user:secret@publisher.org/document.pdf",
                      "https://127.0.0.1/file.pdf", "https://192.168.1.2/file.pdf", "https://[::1]/file.pdf",
                      "https://0x7f.0.0.1/file.pdf", "https://reader.local/file.pdf", "https://reader.local./file.pdf",
                      "https://localhost/file.pdf", "https://publisher.org:8080/file.pdf", "file:///tmp/file.pdf",
                      "javascript:alert(1)"]
        let records = unsafe.enumerated().map { index, url -> [String: Any] in
            var record = work("unsafe-\(index)")
            record["best_oa_location"] = ["is_oa": true, "landing_page_url": url, "pdf_url": url]
            return record
        }
        let page = try await fixture(results: records, count: records.count).search("security")
        XCTAssertEqual(page.works.count, records.count)
        for work in page.works {
            XCTAssertNil(work.landingURL, work.id)
            XCTAssertNil(work.pdfURL, work.id)
        }
    }

    func testMissingAndMalformedRecordsAreSkippedAndMetadataIsBounded() async throws {
        var record = work("W1", title: String(repeating: "字", count: 1_000))
        record["authorships"] = (0..<15).map { ["author": ["display_name": "\($0)" + String(repeating: "a", count: 150)]] }
        record["primary_location"] = ["source": ["display_name": String(repeating: "p", count: 300)]]
        var duplicate = work("other-id")
        duplicate["doi"] = "https://doi.org/10.1000/DUPLICATE"
        var sameDOI = work("second-id")
        sameDOI["doi"] = "10.1000/duplicate"
        let records: [Any] = [record, record, ["id": "missing-title"], ["display_name": "missing-id"],
                              ["id": "bad", "display_name": 123], NSNull(), duplicate, sameDOI]
        let page = try await fixture(results: records, count: records.count).search("metadata")
        XCTAssertEqual(page.works.count, 2)
        XCTAssertEqual(page.works[0].title.count, 800)
        XCTAssertEqual(page.works[0].authors.count, 8)
        XCTAssertTrue(page.works[0].authors.allSatisfy { $0.count == 120 })
        XCTAssertEqual(page.works[0].publication?.count, 240)
        XCTAssertEqual(page.works[1].landingURL?.absoluteString, "https://doi.org/10.1000/duplicate")
    }

    func testAuthorDeduplicationIsStableAndKeepsFirstEightNonemptyNames() async throws {
        var record = work("authors")
        let names = ["  ", "Alice", "Alice", " Bob ", "Bob", "Carol", "Dan", "Eve", "Fay", "Gus", "Hal", "Ivy"]
        record["authorships"] = names.map { ["author": ["display_name": $0]] }
        let page = try await fixture(results: [record], count: 1).search("authors")
        let authors = try XCTUnwrap(page.works.first).authors
        XCTAssertEqual(authors, ["Alice", "Bob", "Carol", "Dan", "Eve", "Fay", "Gus", "Hal"])
    }

    func testPaginationUsesMetadataCountBeforeDeduplicationAndHonorsLimit() async throws {
        let duplicates = Array(repeating: work("W1"), count: 20)
        let first = try await fixture(results: duplicates, count: 21).search("figures")
        XCTAssertEqual(first.works.count, 1)
        XCTAssertTrue(first.hasMore)
        let last = try await fixture(results: [work("W2")], count: 21) { request in
            XCTAssertEqual(self.queryItems(request)["page"], "2")
        }.search("figures", page: 2)
        XCTAssertFalse(last.hasMore)
        let capped = try await fixture(results: duplicates, count: 40_000).search("figures", page: 500)
        XCTAssertFalse(capped.hasMore)
        let empty = try await fixture(results: [], count: 40_000).search("figures")
        XCTAssertFalse(empty.hasMore)
    }

    func testRejectsInvalidQueriesBeforeNetworking() async {
        let client = fixture(results: [], count: 0) { _ in XCTFail("Invalid input should not reach the network") }
        for (query, page) in [("  \n ", 1), (String(repeating: "x", count: 1_025), 1), ("test", 0), ("test", 501), ("10.1000/a,is_oa:false", 1), ("10.1000/a|10.1000/b", 1)] {
            do { _ = try await client.search(query, page: page); XCTFail("Expected input error") }
            catch { XCTAssertFalse(error.localizedDescription.isEmpty) }
        }
    }

    func testRateLimitsAndServerFailuresHaveReadableMessages() async {
        for (status, expected) in [(429, "请求过于频繁"), (503, "暂时不可用"), (403, "暂时拒绝访问")] {
            let client = fixture(results: [], count: 0, status: status)
            do { _ = try await client.search("figures"); XCTFail("Expected HTTP error") }
            catch { XCTAssertTrue(error.localizedDescription.contains(expected), error.localizedDescription) }
        }
    }

    func testMalformedOrOversizedResponsesDoNotBecomeEmptySuccess() async {
        for (data, expected) in [(Data("not JSON".utf8), "无法读取"), (Data("{}".utf8), "无法读取"),
                                 (Data(repeating: 32, count: 4 * 1_024 * 1_024 + 1), "结果过大")] {
            let client = fixture(rawData: data)
            do { _ = try await client.search("figures"); XCTFail("Expected invalid response") }
            catch { XCTAssertTrue(error.localizedDescription.contains(expected), error.localizedDescription) }
        }
    }

    func testTimeoutIsFriendlyAndCancellationPropagates() async {
        do {
            _ = try await fixture(error: URLError(.timedOut)).search("figures")
            XCTFail("Expected timeout")
        } catch { XCTAssertTrue(error.localizedDescription.contains("超时")) }
        do {
            _ = try await fixture(error: URLError(.cancelled)).search("figures")
            XCTFail("Expected cancellation")
        } catch { XCTAssertTrue(error is CancellationError) }
    }

    private func work(_ id: String, title: String = "Sample document") -> [String: Any] {
        ["id": "https://openalex.org/\(id)", "display_name": title]
    }

    private func queryItems(_ request: URLRequest) -> [String: String] {
        guard let url = request.url else { return [:] }
        return Dictionary(uniqueKeysWithValues: (URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []).map { ($0.name, $0.value ?? "") })
    }

    private func fixture(results: [Any] = [], count: Int = 0, status: Int = 200, rawData: Data? = nil,
                         error: Error? = nil, inspect: @escaping (URLRequest) -> Void = { _ in }) -> LiteratureSearchClient {
        let id = UUID().uuidString
        fixtureIDs.append(id)
        let data = rawData ?? (try! JSONSerialization.data(withJSONObject: ["meta": ["count": count], "results": results]))
        LiteratureURLProtocol.register(id) { request in
            inspect(request)
            if let error { throw error }
            return (HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!, data)
        }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [LiteratureURLProtocol.self]
        config.httpAdditionalHeaders = ["X-Literature-Fixture": id]
        let session = URLSession(configuration: config)
        sessions.append(session)
        return LiteratureSearchClient(session: session)
    }
}

private final class LiteratureURLProtocol: URLProtocol {
    typealias Handler = (URLRequest) throws -> (HTTPURLResponse, Data)
    private static let lock = NSLock()
    private static var handlers: [String: Handler] = [:]

    static func register(_ id: String, handler: @escaping Handler) {
        lock.lock(); defer { lock.unlock() }
        handlers[id] = handler
    }

    static func remove(_ id: String) {
        lock.lock(); defer { lock.unlock() }
        handlers.removeValue(forKey: id)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lock.lock()
        let handler = Self.handlers[request.value(forHTTPHeaderField: "X-Literature-Fixture") ?? ""]
        Self.lock.unlock()
        guard let handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }

    override func stopLoading() {}
}
