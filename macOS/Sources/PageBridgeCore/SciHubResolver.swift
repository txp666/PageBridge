import Foundation

/// Resolves a DOI page to its advertised PDF without executing scripts or handling challenges.
/// The existing PDF downloader remains responsible for downloading and validating the document.
public struct SciHubResolver {
    private let configuration: URLSessionConfiguration
    private let addressResolver: (String) throws -> [String]
    private static let maximumHTMLBytes = 2 * 1_024 * 1_024

    public init(configuration: URLSessionConfiguration = .ephemeral) {
        self.init(configuration: configuration, addressResolver: LiteraturePDFURLPolicy.resolve)
    }

    init(configuration: URLSessionConfiguration, addressResolver: @escaping (String) throws -> [String]) {
        let isolated = configuration.copy() as! URLSessionConfiguration
        isolated.httpCookieStorage = nil
        isolated.httpShouldSetCookies = false
        isolated.urlCredentialStorage = nil
        isolated.urlCache = nil
        isolated.httpAdditionalHeaders = [:]
        isolated.requestCachePolicy = .reloadIgnoringLocalCacheData
        isolated.timeoutIntervalForRequest = 25
        isolated.timeoutIntervalForResource = 45
        self.configuration = isolated
        self.addressResolver = addressResolver
    }

    public static func normalizedDOI(_ input: String) -> String? {
        var value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.lowercased().hasPrefix("doi:") {
            value = String(value.dropFirst(4)).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if let parts = URLComponents(string: value),
           ["http", "https"].contains(parts.scheme?.lowercased() ?? ""),
           ["doi.org", "dx.doi.org", "www.doi.org"].contains(parts.host?.lowercased() ?? ""),
           parts.user == nil, parts.password == nil {
            value = String(parts.path.drop(while: { $0 == "/" }))
        }
        guard value.count <= 512,
              value.range(of: "^10\\.[0-9]{4,9}/[^\\s]+$", options: .regularExpression) != nil,
              !value.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else { return nil }
        return value.lowercased()
    }

    public static func articleURL(doi: String, baseURL: String) throws -> URL {
        guard let doi = normalizedDOI(doi) else { throw BridgeError.message("请输入有效 DOI，或先搜索并选择含 DOI 的文献。") }
        guard var parts = URLComponents(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.query == nil, parts.fragment == nil, let root = parts.url else {
            throw BridgeError.message("Sci-Hub 地址无效，请填写不含查询参数的 HTTPS 站点地址。")
        }
        _ = try LiteraturePDFURLPolicy.request(for: root)
        // A hostname is required. TLS continues validating that hostname even when a TUN
        // proxy returns a synthetic address; never replace the URL host with a resolved IP.
        guard let host = parts.host, host.contains(where: { $0.isLetter }), !host.contains(":") else {
            throw BridgeError.message("Sci-Hub 地址需要使用 HTTPS 公共域名。")
        }
        parts.path = parts.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        parts.path = "/" + (parts.path.isEmpty ? "" : parts.path + "/") + doi
        guard let url = parts.url else { throw BridgeError.message("无法创建 Sci-Hub 文献地址。") }
        return url
    }

    public func resolve(doi: String, baseURL: String) async throws -> URL {
        try Task.checkCancellation()
        let url = try Self.articleURL(doi: doi, baseURL: baseURL)
        let resolver = addressResolver
        try await Task.detached(priority: .utility) {
            try LiteraturePDFURLPolicy.validateAddresses(for: url, resolver: resolver)
        }.value
        try Task.checkCancellation()
        let delegate = SciHubRedirectDelegate(addressResolver: resolver)
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        do {
            let (bytes, response) = try await session.bytes(for: Self.request(for: url), delegate: delegate)
            guard let http = response as? HTTPURLResponse, let finalURL = http.url else {
                throw BridgeError.message("Sci-Hub 返回了无效响应，请重试或打开网页查看。")
            }
            _ = try LiteraturePDFURLPolicy.request(for: finalURL)
            switch http.statusCode {
            case 200: break
            case 401, 403: throw BridgeError.message("Sci-Hub 需要访问验证，请打开网页查看；应用不会自动处理验证码。")
            case 404: throw BridgeError.message("Sci-Hub 未找到这篇文献，可打开网页查看或选择公开 PDF 来源。")
            case 429: throw BridgeError.message("Sci-Hub 请求过于频繁，请稍后重试。")
            default: throw BridgeError.message("Sci-Hub 暂时不可用（HTTP \(http.statusCode)），请稍后重试或打开网页查看。")
            }
            try Task.checkCancellation()
            if http.mimeType?.lowercased() == "application/pdf" { return finalURL }
            guard http.expectedContentLength <= Int64(Self.maximumHTMLBytes) else {
                throw BridgeError.message("Sci-Hub 页面超过读取上限，请打开网页查看。")
            }
            var data = Data()
            for try await byte in bytes {
                try Task.checkCancellation()
                guard data.count < Self.maximumHTMLBytes else {
                    throw BridgeError.message("Sci-Hub 页面超过读取上限，请打开网页查看。")
                }
                data.append(byte)
            }
            try Task.checkCancellation()
            guard let html = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) else {
                throw BridgeError.message("无法读取 Sci-Hub 页面，请打开网页查看。")
            }
            return try Self.pdfURL(in: html, pageURL: finalURL, expectedDOI: doi)
        } catch is CancellationError { throw CancellationError() }
        catch let error as URLError where error.code == .cancelled { throw CancellationError() }
        catch let error as URLError where error.code == .timedOut {
            throw BridgeError.message("Sci-Hub 连接超时，请检查地址或稍后重试。")
        } catch let error as URLError {
            try Task.checkCancellation()
            throw BridgeError.message("无法连接 Sci-Hub，请检查网络和站点地址（\(error.code.rawValue)）。")
        }
    }

    static func request(for url: URL) throws -> URLRequest {
        var request = try LiteraturePDFURLPolicy.request(for: url)
        request.timeoutInterval = 25
        request.setValue("text/html, application/pdf;q=0.9", forHTTPHeaderField: "Accept")
        return request
    }

    public static func pdfURL(in html: String, pageURL: URL, expectedDOI: String) throws -> URL {
        // Strip comments and executable/style contents before scanning a small set of tags.
        // This is metadata extraction, not a browser, and no source script is evaluated.
        let inert = replacing("<!--.*?-->|<(script|style)\\b[^>]*>.*?</\\1\\s*>", in: html, with: "")
        let tags = matches("<(meta|iframe|embed|object)\\b[^>]*>", in: inert)
        var candidates: [String] = []
        var fallbacks: [String] = []
        for tag in tags {
            let attrs = attributes(tag)
            if tag.lowercased().hasPrefix("<meta") {
                let name = (attrs["name"] ?? attrs["property"] ?? "").lowercased()
                if name == "citation_doi", let found = attrs["content"],
                   normalizedDOI(found) != normalizedDOI(expectedDOI) {
                    throw BridgeError.message("Sci-Hub 返回的 DOI 与所选文献不一致，已停止打开。")
                }
                if name == "citation_pdf_url", let value = attrs["content"] { candidates.append(value) }
            } else if tag.lowercased().hasPrefix("<object") {
                if attrs["type"]?.lowercased() == "application/pdf", let value = attrs["data"] { fallbacks.append(value) }
            } else if let value = attrs["src"] {
                if attrs["type"]?.lowercased() == "application/pdf" || value.lowercased().contains(".pdf") || attrs["id"]?.lowercased() == "pdf" {
                    fallbacks.append(value)
                }
            }
        }
        for value in candidates + fallbacks {
            let reference = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !reference.isEmpty, reference.count <= 4_096,
                  let url = URL(string: reference, relativeTo: pageURL)?.absoluteURL else { continue }
            // Fail closed on unsafe advertised PDF targets instead of trying arbitrary links.
            return try LiteraturePDFURLPolicy.request(for: url).url!
        }
        if inert.range(of: "captcha|altcha-widget|challenge|验证码|verify you are human", options: [.caseInsensitive, .regularExpression]) != nil {
            throw BridgeError.message("Sci-Hub 页面需要验证，请打开网页处理；应用不会自动解验证码。")
        }
        throw BridgeError.message("Sci-Hub 暂未返回可直接打开的 PDF；可能未收录此文献或页面已变化，请打开网页查看。")
    }

    private static func attributes(_ tag: String) -> [String: String] {
        let regex = try! NSRegularExpression(pattern: #"([a-zA-Z_:][a-zA-Z0-9_:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))"#)
        let ns = tag as NSString
        var result: [String: String] = [:]
        for match in regex.matches(in: tag, range: NSRange(location: 0, length: ns.length)) {
            let name = ns.substring(with: match.range(at: 1)).lowercased()
            if let range = (2...4).map({ match.range(at: $0) }).first(where: { $0.location != NSNotFound }) {
                result[name] = decodeEntities(ns.substring(with: range))
            }
        }
        return result
    }

    private static func decodeEntities(_ text: String) -> String {
        let regex = try! NSRegularExpression(pattern: "&(#x[0-9a-fA-F]+|#[0-9]+|amp|quot|apos|lt|gt);", options: .caseInsensitive)
        let ns = text as NSString
        var output = text
        for match in regex.matches(in: text, range: NSRange(location: 0, length: ns.length)).reversed() {
            let name = ns.substring(with: match.range(at: 1)).lowercased()
            var decoded: String?
            if name.hasPrefix("#x"), let code = UInt32(name.dropFirst(2), radix: 16), let scalar = UnicodeScalar(code) { decoded = String(scalar) }
            else if name.hasPrefix("#"), let code = UInt32(name.dropFirst()), let scalar = UnicodeScalar(code) { decoded = String(scalar) }
            else { decoded = ["amp": "&", "quot": "\"", "apos": "'", "lt": "<", "gt": ">"][name] }
            if let decoded, let range = Range(match.range, in: output) { output.replaceSubrange(range, with: decoded) }
        }
        return output
    }

    private static func matches(_ pattern: String, in text: String) -> [String] {
        let regex = try! NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .dotMatchesLineSeparators])
        let ns = text as NSString
        return regex.matches(in: text, range: NSRange(location: 0, length: ns.length)).map { ns.substring(with: $0.range) }
    }

    private static func replacing(_ pattern: String, in text: String, with replacement: String) -> String {
        let regex = try! NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .dotMatchesLineSeparators])
        return regex.stringByReplacingMatches(in: text, range: NSRange(location: 0, length: (text as NSString).length), withTemplate: replacement)
    }
}

private final class SciHubRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let addressResolver: (String) throws -> [String]
    private var redirects = 0
    init(addressResolver: @escaping (String) throws -> [String]) { self.addressResolver = addressResolver }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        redirects += 1
        guard redirects <= 5, let url = request.url else { completionHandler(nil); return }
        let resolver = addressResolver
        DispatchQueue.global(qos: .utility).async {
            do {
                let clean = try SciHubResolver.request(for: url)
                try LiteraturePDFURLPolicy.validateAddresses(for: url, resolver: resolver)
                completionHandler(task.state == .canceling || task.state == .completed ? nil : clean)
            } catch { completionHandler(nil) }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        completionHandler(challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust
                          ? .performDefaultHandling : .cancelAuthenticationChallenge, nil)
    }
}
