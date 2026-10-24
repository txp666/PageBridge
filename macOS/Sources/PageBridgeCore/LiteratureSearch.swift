import Foundation

public struct LiteratureWork: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let authors: [String]
    public let year: Int?
    public let publication: String?
    public let doi: String?
    public let landingURL: URL?
    public let pdfURL: URL?

    public init(id: String, title: String, authors: [String], year: Int?, publication: String?,
                doi: String?, landingURL: URL?, pdfURL: URL?) {
        self.id = id
        self.title = title
        self.authors = authors
        self.year = year
        self.publication = publication
        self.doi = doi
        self.landingURL = landingURL
        self.pdfURL = pdfURL
    }
}

public struct LiteratureSearchPage: Equatable, Sendable {
    public let works: [LiteratureWork]
    public let hasMore: Bool

    public init(works: [LiteratureWork], hasMore: Bool) {
        self.works = works
        self.hasMore = hasMore
    }
}

/// Searches public OpenAlex metadata. PDF links must be explicitly marked open access
/// by their location; a publisher landing page is never treated as a downloadable PDF.
public struct LiteratureSearchClient {
    private let session: URLSession
    private static let pageSize = 20
    private static let maximumPage = 500 // OpenAlex basic paging is limited to 10,000 results.
    private static let maximumResponseBytes = 4 * 1_024 * 1_024

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func search(_ query: String, page: Int = 1, openAccessOnly: Bool = false) async throws -> LiteratureSearchPage {
        try Task.checkCancellation()
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { throw SearchError.message("请输入文献标题、关键词或 DOI。") }
        guard query.count <= 1_024 else { throw SearchError.message("检索内容过长，请使用简短的标题、关键词或 DOI。") }
        guard (1...Self.maximumPage).contains(page) else { throw SearchError.message("已达到检索页数上限，请缩小检索范围。") }

        var components = URLComponents(string: "https://api.openalex.org/works")!
        var items = [URLQueryItem(name: "per_page", value: String(Self.pageSize)),
                     URLQueryItem(name: "page", value: String(page)),
                     URLQueryItem(name: "select", value: "id,doi,display_name,publication_year,authorships,primary_location,best_oa_location,locations,open_access")]
        var filters: [String] = []
        if let doi = Self.normalizedDOI(query) {
            // Comma and pipe have structural meaning in OpenAlex filters. Do not let
            // an identifier add another filter or broaden an exact DOI lookup.
            guard !doi.contains(","), !doi.contains("|") else {
                throw SearchError.message("这个 DOI 含有检索服务不支持的字符，请改用文献标题搜索。")
            }
            filters.append("doi:https://doi.org/\(doi)")
        } else {
            items.append(URLQueryItem(name: "search", value: query))
        }
        if openAccessOnly { filters.append("is_oa:true") }
        if !filters.isEmpty { items.append(URLQueryItem(name: "filter", value: filters.joined(separator: ","))) }
        components.queryItems = items
        // Some API gateways decode query strings as form data, where '+' means
        // space. Preserve literal plus signs in DOI suffixes and search phrases.
        components.percentEncodedQuery = components.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        guard let url = components.url else { throw SearchError.message("无法创建检索请求，请检查输入内容。") }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 25)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("PageBridge-LiteratureSearch/1.0", forHTTPHeaderField: "User-Agent")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch let error as URLError where error.code == .timedOut {
            throw SearchError.message("文献检索超时，请稍后重试。")
        } catch {
            try Task.checkCancellation()
            throw SearchError.message("无法连接文献检索服务，请检查网络后重试。")
        }
        try Task.checkCancellation()
        guard let http = response as? HTTPURLResponse else {
            throw SearchError.message("文献检索服务返回了无效响应，请稍后重试。")
        }
        switch http.statusCode {
        case 200..<300: break
        case 429: throw SearchError.message("文献检索服务请求过于频繁或今日额度已用完，请稍后重试。")
        case 401, 403: throw SearchError.message("文献检索服务暂时拒绝访问，请稍后重试。")
        default: throw SearchError.message("文献检索服务暂时不可用（\(http.statusCode)），请稍后重试。")
        }
        guard data.count <= Self.maximumResponseBytes else {
            throw SearchError.message("检索结果过大，请缩小检索范围后重试。")
        }
        let result: SearchResponse
        do { result = try JSONDecoder().decode(SearchResponse.self, from: data) }
        catch { throw SearchError.message("无法读取文献检索结果，请稍后重试。") }
        var seen = Set<String>()
        let works = result.results.prefix(Self.pageSize).compactMap { item -> LiteratureWork? in
            guard let dto = item.work, let work = Self.work(from: dto), seen.insert(work.doi ?? work.id).inserted else { return nil }
            return work
        }
        // Count unfiltered metadata rows so a skipped malformed row does not hide
        // remaining pages. Never allow navigation beyond OpenAlex's basic limit.
        let totalCount = result.meta?.count
        let hasMore = page < Self.maximumPage && !result.results.isEmpty &&
            (totalCount.map { page * Self.pageSize < $0 } ?? (result.results.count >= Self.pageSize))
        return LiteratureSearchPage(works: works, hasMore: hasMore)
    }

    private static func work(from dto: WorkDTO) -> LiteratureWork? {
        guard let title = clean(dto.display_name, limit: 800) else { return nil }
        let doi = dto.doi.flatMap(normalizedDOI)
        guard let id = clean(dto.id, limit: 512) ?? doi else { return nil }
        let locations = [dto.best_oa_location, dto.primary_location].compactMap { $0 } + Array((dto.locations ?? []).prefix(100))
        let pdf = locations.lazy.filter { $0.is_oa == true }.compactMap { publicURL($0.pdf_url) }.first
        let landing = publicURL(dto.primary_location?.landing_page_url)
            ?? locations.lazy.compactMap { publicURL($0.landing_page_url) }.first
            ?? doi.flatMap { doiURL($0) }
        var seenAuthors = Set<String>()
        var authors: [String] = []
        // Keep stateful deduplication single-pass. Lazy collection operations can
        // re-evaluate their predicates while calculating indices and counts.
        for authorship in dto.authorships ?? [] {
            guard let name = clean(authorship.author?.display_name, limit: 120),
                  seenAuthors.insert(name).inserted else { continue }
            authors.append(name)
            if authors.count == 8 { break }
        }
        let year = dto.publication_year.flatMap { (1...9_999).contains($0) ? $0 : nil }
        let publication = clean(dto.primary_location?.source?.display_name, limit: 240)
            ?? locations.lazy.compactMap { clean($0.source?.display_name, limit: 240) }.first
        return LiteratureWork(id: id, title: title, authors: authors, year: year, publication: publication,
                              doi: doi, landingURL: landing, pdfURL: pdf)
    }

    private static func clean(_ value: String?, limit: Int) -> String? {
        guard let value else { return nil }
        let text = value.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }.joined(separator: " ")
        return text.isEmpty ? nil : String(text.prefix(limit))
    }

    private static func normalizedDOI(_ input: String) -> String? {
        var value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.lowercased().hasPrefix("doi:") {
            value = String(value.dropFirst(4)).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if let url = URLComponents(string: value), let scheme = url.scheme?.lowercased(),
           ["http", "https"].contains(scheme), let host = url.host?.lowercased(),
           ["doi.org", "dx.doi.org", "www.doi.org"].contains(host), url.user == nil, url.password == nil {
            value = String(url.path.drop(while: { $0 == "/" }))
        }
        guard value.count <= 512,
              value.range(of: "^10\\.[0-9]{4,9}/[^\\s]+$", options: .regularExpression) != nil,
              !value.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { return nil }
        return value.lowercased()
    }

    private static func doiURL(_ doi: String) -> URL? {
        var url = URLComponents(string: "https://doi.org")!
        url.path = "/" + doi
        return url.url
    }

    /// Metadata may contain stale HTTP URLs or intranet addresses. Only expose
    /// HTTPS URLs with public DNS hostnames, and never forward embedded credentials.
    private static func publicURL(_ value: String?) -> URL? {
        guard let value, value.count <= 4_096,
              let parts = URLComponents(string: value), parts.scheme?.lowercased() == "https",
              parts.user == nil, parts.password == nil, parts.port == nil || parts.port == 443,
              let originalHost = parts.host?.lowercased() else { return nil }
        let host = originalHost.trimmingCharacters(in: CharacterSet(charactersIn: "."))
        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        let reserved = ["localhost", "local", "internal", "lan", "home", "invalid", "test", "onion"]
        guard labels.count >= 2, !labels.contains(where: { $0.isEmpty }), !host.contains(":"),
              !host.contains("%"), labels.last!.contains(where: { $0.isLetter }),
              !reserved.contains(String(labels.last!)), !host.hasSuffix(".localhost") else { return nil }
        return parts.url
    }
}

private enum SearchError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

private struct SearchResponse: Decodable {
    var meta: SearchMeta?
    var results: [LossyWork]
}
private struct SearchMeta: Decodable { var count: Int? }
private struct LossyWork: Decodable {
    var work: WorkDTO?
    init(from decoder: Decoder) throws { work = try? WorkDTO(from: decoder) }
}
private struct WorkDTO: Decodable {
    var id: String?
    var doi: String?
    var display_name: String?
    var publication_year: Int?
    var authorships: [AuthorshipDTO]?
    var primary_location: LocationDTO?
    var best_oa_location: LocationDTO?
    var locations: [LocationDTO]?
}
private struct AuthorshipDTO: Decodable { var author: AuthorDTO? }
private struct AuthorDTO: Decodable { var display_name: String? }
private struct SourceDTO: Decodable { var display_name: String? }
private struct LocationDTO: Decodable {
    var is_oa: Bool?
    var landing_page_url: String?
    var pdf_url: String?
    var source: SourceDTO?
}
