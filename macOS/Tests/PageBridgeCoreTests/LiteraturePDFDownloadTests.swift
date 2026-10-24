import XCTest
import Foundation
import PDFKit
import CoreGraphics
@testable import PageBridgeCore

final class LiteraturePDFDownloadTests: XCTestCase {
    private var directory: URL!
    private var registeredURLs: [URL] = []

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("LiteraturePDFDownloadTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
    }

    override func tearDownWithError() throws {
        for url in registeredURLs { LiteraturePDFURLProtocol.unregister(url) }
        try? FileManager.default.removeItem(at: directory)
    }

    func testValidPDFStreamsToSeparateManagedFilesAndCleanupIsScoped() async throws {
        let data = try pdfFixture()
        let url = stub { $0.respond(body: data, headers: ["Content-Type": "application/pdf"]) }
        let downloader = downloader()
        let first = try await downloader.download(from: url, title: "../../A: title/研究.pdf")
        let second = try await downloader.download(from: url, title: "../../A: title/研究.pdf")
        defer { first.cleanup(); second.cleanup() }
        XCTAssertEqual(first.url.pathExtension, "pdf")
        XCTAssertEqual(first.url.lastPathComponent, "A title 研究.pdf")
        XCTAssertNotEqual(first.url.deletingLastPathComponent(), second.url.deletingLastPathComponent())
        XCTAssertEqual(first.url.deletingLastPathComponent().deletingLastPathComponent().standardizedFileURL, directory.standardizedFileURL)
        XCTAssertEqual(try Data(contentsOf: first.url), data)
        XCTAssertEqual(PDFDocument(url: first.url)?.pageCount, 2)
        first.cleanup()
        first.cleanup()
        XCTAssertFalse(FileManager.default.fileExists(atPath: first.url.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: second.url.path))
        second.cleanup()
        try assertNoTemporaryFiles()
    }

    func testOctetStreamRequiresActualReadablePDF() async throws {
        let data = try pdfFixture()
        let url = stub { $0.respond(body: data, headers: ["Content-Type": "application/octet-stream"]) }
        let pdf = try await downloader().download(from: url, title: "public document")
        XCTAssertEqual(PDFDocument(url: pdf.url)?.pageCount, 2)
        pdf.cleanup()
        try assertNoTemporaryFiles()
    }

    func testHTMLAndMislabeledLoginPagesAreRejectedAndCleanedUp() async throws {
        for contentType in ["text/html", "application/pdf", "application/octet-stream"] {
            let url = stub { $0.respond(body: Data("<html>Please log in or solve the CAPTCHA</html>".utf8),
                                       headers: ["Content-Type": contentType]) }
            await expectFailure(url, containing: contentType == "text/html" ? "并非" : "不是 PDF")
        }
    }

    func testHTTPFailuresIncludingPartialContentNeverImportPDF() async throws {
        let data = try pdfFixture()
        for (code, message) in [(206, "部分 PDF"), (401, "访问授权"), (403, "访问授权"),
                                (404, "已失效"), (429, "过于频繁"), (500, "HTTP 500")] {
            let url = stub { $0.respond(status: code, body: data, headers: ["Content-Type": "application/pdf"]) }
            await expectFailure(url, containing: message)
        }
    }

    func testContentLengthLimitIsCheckedBeforeBody() async throws {
        let url = stub { $0.respond(body: Data(), headers: ["Content-Type": "application/pdf", "Content-Length": "1001"]) }
        await expectFailure(url, using: downloader(maximumBytes: 1_000), containing: "大小限制")
    }

    func testActualStreamLimitAppliesWithoutContentLength() async throws {
        let url = stub {
            $0.respond(chunks: [Data("%PDF-1.7\n".utf8), Data(repeating: 65, count: 200)],
                       headers: ["Content-Type": "application/pdf"], automaticLength: false)
        }
        await expectFailure(url, using: downloader(maximumBytes: 100), containing: "大小限制")
    }

    func testDeclaredLengthMismatchRejectsAnOtherwiseReadablePDF() async throws {
        let data = try pdfFixture()
        let url = stub { $0.respond(body: data, headers: ["Content-Type": "application/pdf", "Content-Length": "\(data.count + 50)"]) }
        await expectFailure(url, containing: "不完整")
    }

    func testTruncationAndForgedPDFSignaturesAreRejected() async throws {
        let data = try pdfFixture()
        let eof = try XCTUnwrap(data.range(of: Data("%%EOF".utf8), options: .backwards))
        let truncated = Data(data[..<eof.lowerBound])
        for body in [truncated, Data("%PDF-1.7\nNot a real PDF\n%%EOF\n".utf8)] {
            let url = stub { $0.respond(body: body, headers: ["Content-Type": "application/pdf"]) }
            await expectFailure(url, containing: "已损坏或不完整")
        }
    }

    func testEncryptedPDFIsRejected() async throws {
        let document = try XCTUnwrap(PDFDocument(data: pdfFixture()))
        let encrypted = try XCTUnwrap(document.dataRepresentation(options: [
            PDFDocumentWriteOption.ownerPasswordOption: "owner-test-only",
            PDFDocumentWriteOption.userPasswordOption: "reader-test-only"
        ]))
        XCTAssertTrue(try XCTUnwrap(PDFDocument(data: encrypted)).isEncrypted)
        let url = stub { $0.respond(body: encrypted, headers: ["Content-Type": "application/pdf"]) }
        await expectFailure(url, containing: "已加密")
    }

    func testCancellationStopsTheTaskAndRemovesPartialFile() async throws {
        let started = expectation(description: "received initial body")
        let stopped = expectation(description: "URL loading stopped")
        let url = stub {
            $0.onStop = { stopped.fulfill() }
            $0.respond(chunks: [Data("%PDF-1.7\n".utf8)], headers: ["Content-Type": "application/pdf"],
                       automaticLength: false, finish: false)
            started.fulfill()
        }
        let downloader = downloader()
        let task = Task { try await downloader.download(from: url, title: "cancel me") }
        await fulfillment(of: [started], timeout: 3)
        task.cancel()
        do {
            let downloaded = try await task.value
            downloaded.cleanup()
            XCTFail("Expected cancellation")
        } catch is CancellationError {
            // Cancellation must not be reported as a download/network failure.
        } catch { XCTFail("Unexpected error: \(error)") }
        await fulfillment(of: [stopped], timeout: 3)
        try assertNoTemporaryFiles()
    }

    func testPrivateDNSAnswerNeverStartsNetworkLoading() async throws {
        let url = stub { _ in XCTFail("Private destination must not reach URL loading") }
        for privateAddress in ["192.168.1.2", "10.1.2.3", "172.20.1.1", "127.0.0.1", "169.254.169.254", "::1", "fc00::1"] {
            await expectFailure(url, using: downloader(resolver: { _ in ["198.18.0.17", privateAddress] }), containing: "私有网络")
        }
    }

    func testFakeIPDNSAllowsDownloadWhilePreservingHTTPSHostname() async throws {
        let data = try pdfFixture()
        let url = stub { transport in
            XCTAssertEqual(transport.request.url?.host, "papers.example.org")
            XCTAssertEqual(transport.request.url?.scheme, "https")
            transport.respond(body: data, headers: ["Content-Type": "application/pdf"])
        }
        let downloader = downloader(resolver: { host in
            XCTAssertEqual(host, "papers.example.org")
            return ["198.18.0.17", "198.19.255.254"]
        })
        let pdf = try await downloader.download(from: url, title: "TUN proxy PDF")
        XCTAssertEqual(PDFDocument(url: pdf.url)?.pageCount, 2)
        pdf.cleanup()
        try assertNoTemporaryFiles()
    }

    func testFakeIPRangeIsAllowedOnlyForDomainDNSNotLiteralURLs() throws {
        let domainURL = try XCTUnwrap(URL(string: "https://journals.plos.org/article/file"))
        for address in ["198.18.0.0", "198.18.0.17", "198.19.255.255"] {
            XCTAssertNoThrow(try LiteraturePDFURLPolicy.validateAddresses(for: domainURL, resolver: { _ in [address] }))
            let literalURL = try XCTUnwrap(URL(string: "https://\(address)/paper.pdf"))
            XCTAssertThrowsError(try LiteraturePDFURLPolicy.request(for: literalURL))
            XCTAssertThrowsError(try LiteraturePDFURLPolicy.validateAddresses(for: literalURL, resolver: { _ in
                XCTFail("Literal addresses must not invoke DNS")
                return ["93.184.215.14"]
            }))
        }
    }

    func testTLSFailureIsNotRetriedWithInsecureTransport() async throws {
        let url = stub { request in
            XCTAssertEqual(request.request.url?.scheme, "https")
            request.client?.urlProtocol(request, didFailWithError: URLError(.serverCertificateUntrusted))
        }
        await expectFailure(url, containing: "HTTPS 安全验证失败")
    }

    func testSessionConfigurationDoesNotLeakAPIHeadersOrCookies() async throws {
        let data = try pdfFixture()
        let url = stub { transport in
            XCTAssertNil(transport.request.value(forHTTPHeaderField: "Authorization"))
            XCTAssertNil(transport.request.value(forHTTPHeaderField: "Cookie"))
            XCTAssertNil(transport.request.value(forHTTPHeaderField: "X-Api-Key"))
            XCTAssertFalse(transport.request.httpShouldHandleCookies)
            XCTAssertEqual(transport.request.httpMethod, "GET")
            transport.respond(body: data, headers: ["Content-Type": "application/pdf"])
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [LiteraturePDFURLProtocol.self]
        configuration.httpAdditionalHeaders = ["Authorization": "Bearer test-secret", "Cookie": "secret=1", "X-Api-Key": "test-key"]
        let downloader = LiteraturePDFDownloader(configuration: configuration, temporaryDirectory: directory,
                                                  addressResolver: { _ in ["93.184.215.14"] })
        let pdf = try await downloader.download(from: url, title: "anonymous")
        pdf.cleanup()
        try assertNoTemporaryFiles()
    }

    func testInitialAndRedirectURLPolicyRejectsUnsafeDestinations() throws {
        let unsafe = [
            "http://papers.example.org/a.pdf", "file:///tmp/a.pdf", "https://user:password@papers.example.org/a.pdf",
            "https://papers.example.org:8443/a.pdf", "https://localhost/a.pdf", "https://printer.local/a.pdf",
            "https://127.0.0.1/a.pdf", "https://10.1.2.3/a.pdf", "https://192.168.0.1/a.pdf",
            "https://172.20.1.1/a.pdf", "https://169.254.169.254/a.pdf", "https://100.64.0.1/a.pdf",
            "https://[::1]/a.pdf", "https://[fc00::1]/a.pdf", "https://[fe80::1]/a.pdf",
            "https://[::ffff:127.0.0.1]/a.pdf", "https://2130706433/a.pdf", "https://0x7f000001/a.pdf"
        ]
        for value in unsafe {
            XCTAssertThrowsError(try LiteraturePDFURLPolicy.request(for: XCTUnwrap(URL(string: value))), value)
        }
        XCTAssertNoThrow(try LiteraturePDFURLPolicy.request(for: XCTUnwrap(URL(string: "https://journals.plos.org/article/file?id=10.1371/journal.pcbi.1003833&type=print"))))
        XCTAssertNoThrow(try LiteraturePDFURLPolicy.validateAddresses(for: XCTUnwrap(URL(string: "https://papers.example.org/a.pdf")),
                                                                      resolver: { _ in ["93.184.215.14", "2606:4700:4700::1111"] }))
        XCTAssertThrowsError(try LiteraturePDFURLPolicy.validateAddresses(for: XCTUnwrap(URL(string: "https://papers.example.org/a.pdf")),
                                                                         resolver: { _ in ["::ffff:192.168.1.1"] }))
    }

    func testRedirectRequestIsRebuiltWithoutInheritedCredentials() throws {
        var proposed = URLRequest(url: try XCTUnwrap(URL(string: "https://cdn.example.org/paper.pdf#section")))
        proposed.setValue("Bearer must-not-follow", forHTTPHeaderField: "Authorization")
        proposed.setValue("login=must-not-follow", forHTTPHeaderField: "Cookie")
        proposed.httpMethod = "POST"
        proposed.httpBody = Data("private".utf8)
        let sanitized = try LiteraturePDFURLPolicy.request(for: XCTUnwrap(proposed.url))
        XCTAssertEqual(sanitized.httpMethod, "GET")
        XCTAssertNil(sanitized.httpBody)
        XCTAssertNil(sanitized.url?.fragment)
        XCTAssertNil(sanitized.value(forHTTPHeaderField: "Authorization"))
        XCTAssertNil(sanitized.value(forHTTPHeaderField: "Cookie"))
    }

    func testFilenameSanitizingCannotEscapeDirectoryAndBoundsUTF8() {
        XCTAssertEqual(LiteraturePDFURLPolicy.filename(for: "../../A: title/研究.pdf"), "A title 研究.pdf")
        XCTAssertEqual(LiteraturePDFURLPolicy.filename(for: "\0/\\.. :\n"), "文献.pdf")
        XCTAssertEqual(LiteraturePDFURLPolicy.filename(for: "report.PDF"), "report.pdf")
        let long = LiteraturePDFURLPolicy.filename(for: String(repeating: "👨‍👩‍👧‍👦", count: 150))
        XCTAssertLessThanOrEqual(long.utf8.count, 224)
        XCTAssertTrue(long.hasSuffix(".pdf"))
    }

    private func downloader(maximumBytes: Int64 = LiteraturePDFDownloader.maximumDownloadBytes,
                            resolver: @escaping (String) throws -> [String] = { _ in ["93.184.215.14"] }) -> LiteraturePDFDownloader {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [LiteraturePDFURLProtocol.self]
        return LiteraturePDFDownloader(configuration: configuration, temporaryDirectory: directory,
                                       maximumBytes: maximumBytes, addressResolver: resolver)
    }

    private func stub(_ handler: @escaping (LiteraturePDFURLProtocol) -> Void) -> URL {
        let url = URL(string: "https://papers.example.org/\(UUID().uuidString).pdf")!
        registeredURLs.append(url)
        LiteraturePDFURLProtocol.register(url, handler: handler)
        return url
    }

    private func expectFailure(_ url: URL, using configuredDownloader: LiteraturePDFDownloader? = nil,
                               containing message: String, file: StaticString = #filePath, line: UInt = #line) async {
        do {
            let downloaded = try await (configuredDownloader ?? downloader()).download(from: url, title: "failure fixture")
            downloaded.cleanup()
            XCTFail("Expected download to fail", file: file, line: line)
        } catch {
            XCTAssertTrue(error.localizedDescription.contains(message), error.localizedDescription, file: file, line: line)
        }
        do { try assertNoTemporaryFiles(file: file, line: line) }
        catch { XCTFail("Could not inspect temporary directory: \(error)", file: file, line: line) }
    }

    private func assertNoTemporaryFiles(file: StaticString = #filePath, line: UInt = #line) throws {
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path), [], file: file, line: line)
    }

    private func pdfFixture() throws -> Data {
        let output = NSMutableData()
        let consumer = try XCTUnwrap(CGDataConsumer(data: output))
        var box = CGRect(x: 0, y: 0, width: 200, height: 300)
        let context = try XCTUnwrap(CGContext(consumer: consumer, mediaBox: &box, nil))
        for _ in 0..<2 {
            context.beginPDFPage(nil)
            context.setFillColor(CGColor(gray: 0.2, alpha: 1))
            context.fill(CGRect(x: 20, y: 20, width: 100, height: 100))
            context.endPDFPage()
        }
        context.closePDF()
        return output as Data
    }
}

private final class LiteraturePDFURLProtocol: URLProtocol {
    private static let lock = NSLock()
    private static var handlers: [URL: (LiteraturePDFURLProtocol) -> Void] = [:]
    var onStop: (() -> Void)?

    static func register(_ url: URL, handler: @escaping (LiteraturePDFURLProtocol) -> Void) {
        lock.lock(); defer { lock.unlock() }
        handlers[url] = handler
    }

    static func unregister(_ url: URL) {
        lock.lock(); defer { lock.unlock() }
        handlers.removeValue(forKey: url)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lock.lock()
        let handler = request.url.flatMap { Self.handlers[$0] }
        Self.lock.unlock()
        guard let handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.unsupportedURL))
            return
        }
        handler(self)
    }

    override func stopLoading() { onStop?() }

    func respond(status: Int = 200, body: Data, headers: [String: String] = [:]) {
        respond(status: status, chunks: [body], headers: headers)
    }

    func respond(status: Int = 200, chunks: [Data], headers: [String: String] = [:],
                 automaticLength: Bool = true, finish: Bool = true) {
        var headers = headers
        if automaticLength, headers["Content-Length"] == nil {
            headers["Content-Length"] = String(chunks.reduce(0) { $0 + $1.count })
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        for data in chunks { client?.urlProtocol(self, didLoad: data) }
        if finish { client?.urlProtocolDidFinishLoading(self) }
    }
}
