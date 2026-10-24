import Foundation
import PDFKit
import Darwin

/// A validated PDF owned by a single download. Import it before calling cleanup().
public struct DownloadedLiteraturePDF: Sendable {
    public let url: URL
    private let directory: URL

    fileprivate init(url: URL, directory: URL) {
        self.url = url
        self.directory = directory
    }

    public func cleanup() {
        try? FileManager.default.removeItem(at: directory)
    }
}

/// Downloads an openly accessible PDF with an isolated, anonymous session.
/// Cookies, configured API credentials and system HTTP credentials are never used.
public final class LiteraturePDFDownloader: @unchecked Sendable {
    public static let maximumDownloadBytes: Int64 = 100 * 1024 * 1024
    private let configuration: URLSessionConfiguration
    private let temporaryDirectory: URL
    private let maximumBytes: Int64
    private let addressResolver: (String) throws -> [String]

    public convenience init(configuration: URLSessionConfiguration = .ephemeral,
                            temporaryDirectory: URL = FileManager.default.temporaryDirectory,
                            maximumBytes: Int64 = LiteraturePDFDownloader.maximumDownloadBytes) {
        self.init(configuration: configuration, temporaryDirectory: temporaryDirectory,
                  maximumBytes: maximumBytes, addressResolver: LiteraturePDFURLPolicy.resolve)
    }

    // Resolver injection keeps fixed-response tests independent of public DNS.
    init(configuration: URLSessionConfiguration, temporaryDirectory: URL,
         maximumBytes: Int64 = LiteraturePDFDownloader.maximumDownloadBytes,
         addressResolver: @escaping (String) throws -> [String]) {
        let isolated = configuration.copy() as! URLSessionConfiguration
        isolated.httpCookieStorage = nil
        isolated.httpShouldSetCookies = false
        isolated.urlCredentialStorage = nil
        isolated.urlCache = nil
        isolated.httpAdditionalHeaders = [:]
        isolated.requestCachePolicy = .reloadIgnoringLocalCacheData
        isolated.timeoutIntervalForRequest = 30
        isolated.timeoutIntervalForResource = 180
        self.configuration = isolated
        self.temporaryDirectory = temporaryDirectory
        self.maximumBytes = max(1, min(maximumBytes, Self.maximumDownloadBytes))
        self.addressResolver = addressResolver
    }

    public func download(from url: URL, title: String) async throws -> DownloadedLiteraturePDF {
        try Task.checkCancellation()
        let request = try LiteraturePDFURLPolicy.request(for: url)
        let job = LiteraturePDFDownloadJob(configuration: configuration, temporaryDirectory: temporaryDirectory,
                                           maximumBytes: maximumBytes, addressResolver: addressResolver)
        let downloaded = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                job.start(request: request, title: title, continuation: continuation)
            }
        } onCancel: {
            job.cancel()
        }
        if Task.isCancelled {
            downloaded.cleanup()
            throw CancellationError()
        }
        return downloaded
    }
}

/// The same URL and address policy is applied to the first request and every redirect.
/// This is a preflight filter, not complete network isolation: URLSession resolves
/// again when connecting, and TUN proxies can synthesize DNS answers.
enum LiteraturePDFURLPolicy {
    static func request(for url: URL) throws -> URLRequest {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme?.lowercased() == "https",
              components.user == nil, components.password == nil,
              let rawHost = components.host, !rawHost.isEmpty,
              components.port == nil || components.port == 443 else {
            throw BridgeError.message("只能下载不含账号密码的公开 HTTPS 文献地址。")
        }
        let host = normalizedHost(rawHost)
        if isIPAddress(host) {
            guard isPublicAddress(host) else { throw unsafeHostError }
        } else {
            let labels = host.split(separator: ".", omittingEmptySubsequences: false)
            let forbiddenSuffixes = ["localhost", "local", "internal", "lan", "home", "test", "invalid", "onion"]
            guard labels.count >= 2,
                  !forbiddenSuffixes.contains(String(labels.last ?? "")),
                  labels.allSatisfy({ label in
                      !label.isEmpty && label.count <= 63 && label.first != "-" && label.last != "-" &&
                      label.utf8.allSatisfy { (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 }
                  }),
                  labels.last?.contains(where: { $0.isLetter }) == true else { throw unsafeHostError }
        }
        var sanitized = components
        sanitized.fragment = nil
        guard let destination = sanitized.url else { throw unsafeHostError }
        var request = URLRequest(url: destination, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
        request.httpMethod = "GET"
        request.httpShouldHandleCookies = false
        request.setValue("application/pdf, application/octet-stream;q=0.8", forHTTPHeaderField: "Accept")
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        request.setValue("PageBridge/1.0 (public document reader)", forHTTPHeaderField: "User-Agent")
        return request
    }

    static func validateAddresses(for url: URL, resolver: (String) throws -> [String]) throws {
        _ = try request(for: url)
        guard let host = url.host else { throw unsafeHostError }
        let normalized = normalizedHost(host)
        let literalIP = isIPAddress(normalized)
        let addresses = literalIP ? [normalized] : try resolver(normalized)
        // Some local TUN proxies map public hostnames into 198.18.0.0/15.
        // Accept this range only as a DNS result, never as an explicit IP URL.
        // The request retains its original hostname and normal TLS verification.
        guard !addresses.isEmpty, addresses.allSatisfy({ address in
            isPublicAddress(address) || (!literalIP && isSyntheticProxyAddress(address))
        }) else { throw unsafeHostError }
    }

    static func resolve(_ host: String) throws -> [String] {
        var hints = addrinfo()
        hints.ai_family = AF_UNSPEC
        hints.ai_socktype = SOCK_STREAM
        var result: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, nil, &hints, &result) == 0, let first = result else {
            throw BridgeError.message("无法解析文献下载地址，请检查网络或打开来源页面。")
        }
        defer { freeaddrinfo(first) }
        var addresses = Set<String>()
        var cursor: UnsafeMutablePointer<addrinfo>? = first
        while let entry = cursor {
            var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            if getnameinfo(entry.pointee.ai_addr, entry.pointee.ai_addrlen, &buffer,
                           socklen_t(buffer.count), nil, 0, NI_NUMERICHOST) == 0 {
                addresses.insert(String(cString: buffer))
            }
            cursor = entry.pointee.ai_next
        }
        return Array(addresses)
    }

    static func isPublicAddress(_ address: String) -> Bool {
        let value = normalizedHost(address)
        var ipv4 = in_addr()
        if inet_pton(AF_INET, value, &ipv4) == 1 {
            let bytes = withUnsafeBytes(of: &ipv4) { Array($0) }
            let a = Int(bytes[0]), b = Int(bytes[1]), c = Int(bytes[2])
            return !(a == 0 || a == 10 || a == 127 || a >= 224 ||
                (a == 100 && (64...127).contains(b)) || (a == 169 && b == 254) ||
                (a == 172 && (16...31).contains(b)) ||
                (a == 192 && (b == 168 || (b == 0 && (c == 0 || c == 2)) || (b == 88 && c == 99))) ||
                (a == 198 && ((b == 18 || b == 19) || (b == 51 && c == 100))) ||
                (a == 203 && b == 0 && c == 113))
        }
        var ipv6 = in6_addr()
        guard inet_pton(AF_INET6, value, &ipv6) == 1 else { return false }
        let bytes = withUnsafeBytes(of: &ipv6) { Array($0) }
        // Only global unicast. Exclude tunnelling and documentation allocations;
        // this also rejects mapped IPv4, loopback, NAT64, link-local and ULA.
        guard bytes[0] & 0xe0 == 0x20 else { return false }
        if bytes[0] == 0x20 && bytes[1] == 0x02 { return false }
        if bytes[0] == 0x20 && bytes[1] == 0x01 {
            if bytes[2] == 0 && bytes[3] == 0 { return false }
            if bytes[2] == 0x0d && bytes[3] == 0xb8 { return false }
            if bytes[2] == 0 && (bytes[3] & 0xf0 == 0x10 || bytes[3] & 0xf0 == 0x20) { return false }
        }
        return true
    }

    static func filename(for title: String) -> String {
        let invalid = CharacterSet.controlCharacters.union(CharacterSet(charactersIn: "/\\:?%*|\"<>"))
        var name = title.components(separatedBy: invalid).joined(separator: " ")
        name = name.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        name = name.trimmingCharacters(in: CharacterSet(charactersIn: ". "))
        if name.lowercased().hasSuffix(".pdf") { name.removeLast(4) }
        name = String(name.prefix(100)).trimmingCharacters(in: CharacterSet(charactersIn: ". "))
        // Bound UTF-8 too: a short emoji title can otherwise exceed APFS NAME_MAX.
        while name.utf8.count > 220 { name.removeLast() }
        return (name.isEmpty ? "文献" : name) + ".pdf"
    }

    private static func normalizedHost(_ host: String) -> String {
        host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "[]."))
    }

    private static func isIPAddress(_ host: String) -> Bool {
        var ipv4 = in_addr(), ipv6 = in6_addr()
        return inet_pton(AF_INET, host, &ipv4) == 1 || inet_pton(AF_INET6, host, &ipv6) == 1
    }

    private static func isSyntheticProxyAddress(_ address: String) -> Bool {
        var ipv4 = in_addr()
        guard inet_pton(AF_INET, address, &ipv4) == 1 else { return false }
        let bytes = withUnsafeBytes(of: &ipv4) { Array($0) }
        return bytes[0] == 198 && (bytes[1] == 18 || bytes[1] == 19)
    }

    private static var unsafeHostError: BridgeError {
        .message("文献下载地址指向本机、私有网络或不受支持的主机，已停止下载。")
    }
}

private final class LiteraturePDFDownloadJob: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let queue = DispatchQueue(label: "PageBridge.LiteraturePDFDownload", qos: .utility)
    private let cancellationLock = NSLock()
    private var cancellationRequested = false
    private let configuration: URLSessionConfiguration
    private let temporaryDirectory: URL
    private let maximumBytes: Int64
    private let addressResolver: (String) throws -> [String]
    private var continuation: CheckedContinuation<DownloadedLiteraturePDF, Error>?
    private var session: URLSession?
    private var directory: URL?
    private var fileURL: URL?
    private var file: FileHandle?
    private var receivedBytes: Int64 = 0
    private var declaredBytes: Int64?
    private var receivedResponse = false
    private var redirects = 0
    private var pendingRedirect: ((URLRequest?) -> Void)?
    private var finished = false

    init(configuration: URLSessionConfiguration, temporaryDirectory: URL, maximumBytes: Int64,
         addressResolver: @escaping (String) throws -> [String]) {
        self.configuration = configuration
        self.temporaryDirectory = temporaryDirectory
        self.maximumBytes = maximumBytes
        self.addressResolver = addressResolver
    }

    func start(request: URLRequest, title: String, continuation: CheckedContinuation<DownloadedLiteraturePDF, Error>) {
        queue.async {
            self.continuation = continuation
            if self.isCancelled { self.finish(.failure(CancellationError())); return }
            do {
                let directory = self.temporaryDirectory.appendingPathComponent("PageBridge-literature-\(UUID().uuidString)", isDirectory: true)
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                        attributes: [.posixPermissions: 0o700])
                self.directory = directory
                let fileURL = directory.appendingPathComponent(LiteraturePDFURLPolicy.filename(for: title))
                guard FileManager.default.createFile(atPath: fileURL.path, contents: nil,
                                                      attributes: [.posixPermissions: 0o600]) else {
                    throw BridgeError.message("无法创建文献下载临时文件，请检查磁盘空间。")
                }
                self.fileURL = fileURL
                self.file = try FileHandle(forWritingTo: fileURL)
                self.checkDestination(request.url!) { result in
                    guard !self.finished else { return }
                    switch result {
                    case .failure(let error): self.finish(.failure(error))
                    case .success:
                        if self.isCancelled { self.finish(.failure(CancellationError())); return }
                        let delegateQueue = OperationQueue()
                        delegateQueue.maxConcurrentOperationCount = 1
                        delegateQueue.underlyingQueue = self.queue
                        let session = URLSession(configuration: self.configuration, delegate: self, delegateQueue: delegateQueue)
                        self.session = session
                        session.dataTask(with: request).resume()
                    }
                }
            } catch { self.finish(.failure(error)) }
        }
    }

    func cancel() {
        cancellationLock.lock()
        cancellationRequested = true
        cancellationLock.unlock()
        queue.async {
            if self.continuation != nil { self.finish(.failure(CancellationError())) }
        }
    }

    private var isCancelled: Bool {
        cancellationLock.lock()
        defer { cancellationLock.unlock() }
        return cancellationRequested
    }

    // DNS is off the delegate queue, so cancellation can finish immediately even
    // while the system resolver is waiting. No request starts until it passes.
    private func checkDestination(_ url: URL, completion: @escaping (Result<Void, Error>) -> Void) {
        DispatchQueue.global(qos: .utility).async {
            let result = Result { try LiteraturePDFURLPolicy.validateAddresses(for: url, resolver: self.addressResolver) }
            self.queue.async { completion(result) }
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard !finished, !isCancelled else { completionHandler(.cancel); return }
        do {
            guard let response = response as? HTTPURLResponse, let url = response.url else {
                throw BridgeError.message("文献服务器返回了无法识别的响应。")
            }
            _ = try LiteraturePDFURLPolicy.request(for: url)
            guard response.statusCode == 200 else {
                switch response.statusCode {
                case 401, 403: throw BridgeError.message("该来源需要登录或访问授权，请打开来源页面查看。")
                case 404: throw BridgeError.message("该来源的 PDF 已失效，请尝试其他来源。")
                case 429: throw BridgeError.message("该来源请求过于频繁，请稍后重试。")
                case 206: throw BridgeError.message("该来源只返回了部分 PDF，未导入不完整文件。")
                default: throw BridgeError.message("文献下载失败（HTTP \(response.statusCode)），请稍后重试或打开来源页面。")
                }
            }
            let mime = response.mimeType?.lowercased() ?? ""
            guard mime != "text/html", mime != "application/xhtml+xml" else {
                throw BridgeError.message("该链接返回了网页、登录页或验证码，并非可直接下载的 PDF。请打开来源页面。")
            }
            guard response.expectedContentLength <= maximumBytes else { throw sizeError }
            let encoding = response.value(forHTTPHeaderField: "Content-Encoding")?.lowercased()
            if response.expectedContentLength >= 0, encoding == nil || encoding == "identity" {
                declaredBytes = response.expectedContentLength
            }
            receivedResponse = true
            completionHandler(.allow)
        } catch {
            completionHandler(.cancel)
            finish(.failure(error))
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard !finished else { return }
        if isCancelled { finish(.failure(CancellationError())); return }
        guard Int64(data.count) <= maximumBytes - receivedBytes else { finish(.failure(sizeError)); return }
        do {
            try file?.write(contentsOf: data)
            receivedBytes += Int64(data.count)
        } catch { finish(.failure(BridgeError.message("无法保存下载文件，请检查磁盘剩余空间。"))) }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard !finished, !isCancelled else { completionHandler(nil); return }
        do {
            redirects += 1
            guard redirects <= 5, let url = request.url else {
                throw BridgeError.message("文献来源重定向次数过多，请打开来源页面。")
            }
            let sanitized = try LiteraturePDFURLPolicy.request(for: url)
            pendingRedirect = completionHandler
            checkDestination(url) { result in
                guard let callback = self.pendingRedirect else { return }
                self.pendingRedirect = nil
                guard !self.finished, !self.isCancelled else { callback(nil); return }
                switch result {
                case .success: callback(sanitized)
                case .failure(let error): callback(nil); self.finish(.failure(error))
                }
            }
        } catch { completionHandler(nil); finish(.failure(error)) }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust {
            completionHandler(.performDefaultHandling, nil)
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            finish(.failure(BridgeError.message("该来源需要登录或访问授权，请打开来源页面查看。")))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard !finished else { return }
        if isCancelled { finish(.failure(CancellationError())); return }
        if let error {
            let code = (error as NSError).code
            let description: String
            if code == NSURLErrorTimedOut { description = "文献下载超时，请重试或打开来源页面。" }
            else if [NSURLErrorServerCertificateUntrusted, NSURLErrorSecureConnectionFailed,
                     NSURLErrorServerCertificateHasBadDate, NSURLErrorServerCertificateHasUnknownRoot,
                     NSURLErrorServerCertificateNotYetValid].contains(code) {
                description = "文献来源的 HTTPS 安全验证失败，请检查网络或使用其他来源。"
            } else { description = "文献下载中断，请检查网络后重试。" }
            finish(.failure(BridgeError.message(description)))
            return
        }
        do {
            try file?.close()
            file = nil
            guard receivedResponse, receivedBytes > 0, declaredBytes == nil || declaredBytes == receivedBytes,
                  let fileURL, let directory else {
                throw BridgeError.message("下载的 PDF 为空或不完整，请重试或使用其他来源。")
            }
            try validatePDF(at: fileURL)
            if isCancelled { throw CancellationError() }
            finish(.success(DownloadedLiteraturePDF(url: fileURL, directory: directory)))
        } catch { finish(.failure(error)) }
    }

    private func validatePDF(at url: URL) throws {
        let reader = try FileHandle(forReadingFrom: url)
        defer { try? reader.close() }
        let header = try reader.read(upToCount: 5)
        guard header == Data("%PDF-".utf8) else {
            throw BridgeError.message("下载内容不是 PDF，可能是登录页或验证码。请打开来源页面。")
        }
        try reader.seek(toOffset: UInt64(max(0, receivedBytes - 2_048)))
        let tail = try reader.readToEnd() ?? Data()
        guard tail.range(of: Data("%%EOF".utf8)) != nil, let pdf = PDFDocument(url: url) else {
            throw BridgeError.message("下载的 PDF 已损坏或不完整，未导入文档库。")
        }
        guard !pdf.isLocked, !pdf.isEncrypted else {
            throw BridgeError.message("该 PDF 已加密，请先在预览中解密并保存副本后导入。")
        }
        guard pdf.pageCount > 0 else { throw BridgeError.message("下载的 PDF 没有可阅读的页面。") }
        for index in 0..<pdf.pageCount {
            if isCancelled { throw CancellationError() }
            guard let page = pdf.page(at: index) else {
                throw BridgeError.message("下载的 PDF 存在无法读取的页面，未导入文档库。")
            }
            let bounds = page.bounds(for: .mediaBox)
            guard bounds.width.isFinite, bounds.height.isFinite, bounds.width > 0, bounds.height > 0 else {
                throw BridgeError.message("下载的 PDF 页面尺寸无效，未导入文档库。")
            }
        }
    }

    private var sizeError: BridgeError {
        .message("该 PDF 超过下载大小限制（最高 100 MB）。请在浏览器下载后手动导入。")
    }

    private func finish(_ result: Result<DownloadedLiteraturePDF, Error>) {
        guard !finished else { return }
        finished = true
        let redirect = pendingRedirect
        pendingRedirect = nil
        redirect?(nil)
        try? file?.close()
        file = nil
        session?.invalidateAndCancel()
        session = nil
        if case .failure = result, let directory { try? FileManager.default.removeItem(at: directory) }
        let pendingContinuation = continuation
        self.continuation = nil
        pendingContinuation?.resume(with: result)
    }
}
