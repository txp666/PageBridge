import Foundation
import SwiftUI
import PageBridgeCore

enum LiteratureFullTextSource: String, CaseIterable {
    case sciHub, openAccess

    var title: String { self == .sciHub ? "Sci-Hub" : "公开 PDF" }
    func canOpen(_ work: LiteratureWork) -> Bool {
        self == .sciHub ? work.doi != nil : work.pdfURL != nil
    }
}

@MainActor
final class LiteratureSearchModel: ObservableObject {
    @Published var query = "" {
        didSet { if query != oldValue { resetSearch() } }
    }
    @Published var openAccessOnly = false {
        didSet { if openAccessOnly != oldValue { resetSearch() } }
    }
    @Published private(set) var works: [LiteratureWork] = []
    @Published private(set) var isSearching = false
    @Published private(set) var hasSearched = false
    @Published private(set) var hasMore = false
    @Published private(set) var downloadingID: String?
    @Published private(set) var downloadPhase = ""
    @Published private(set) var errorMessage: String?
    @Published private(set) var fallbackURL: URL?
    @Published private(set) var failedWork: LiteratureWork?
    private let client = LiteratureSearchClient()
    private let downloader = LiteraturePDFDownloader()
    private let sciHub = SciHubResolver()
    private var searchTask: Task<Void, Never>?
    private var downloadTask: Task<Void, Never>?
    private var searchRevision = UUID()
    private var downloadRevision = UUID()
    private var loadedPage = 0

    var isDownloading: Bool { downloadingID != nil }
    var canSearch: Bool { !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isDownloading }
    var directDOIWork: LiteratureWork? {
        guard let doi = SciHubResolver.normalizedDOI(query) else { return nil }
        var landing = URLComponents(string: "https://doi.org")!
        landing.path = "/" + doi
        return LiteratureWork(id: "doi:\(doi)", title: doi, authors: [], year: nil,
                              publication: nil, doi: doi, landingURL: landing.url, pdfURL: nil)
    }

    func search() {
        guard canSearch else { return }
        cancelSearch()
        works = []; loadedPage = 0; hasMore = false; hasSearched = false
        fetch(page: 1)
    }

    func loadMore() {
        guard hasMore, !isSearching, !isDownloading else { return }
        fetch(page: loadedPage + 1)
    }

    private func fetch(page: Int) {
        let revision = UUID(); searchRevision = revision
        let requestedQuery = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let requestedFilter = openAccessOnly
        isSearching = true; errorMessage = nil; fallbackURL = nil; failedWork = nil
        searchTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.searchRevision == revision { self.isSearching = false; self.searchTask = nil }
            }
            do {
                let result = try await self.client.search(requestedQuery, page: page, openAccessOnly: requestedFilter)
                try Task.checkCancellation()
                guard self.searchRevision == revision else { return }
                var ids = Set(self.works.map(\.id))
                self.works.append(contentsOf: result.works.filter { ids.insert($0.id).inserted })
                self.loadedPage = page; self.hasMore = result.hasMore; self.hasSearched = true
            } catch {
                guard !Task.isCancelled, self.searchRevision == revision else { return }
                self.errorMessage = error.localizedDescription
            }
        }
    }

    func open(_ work: LiteratureWork, source: LiteratureFullTextSource, sciHubBaseURL: String,
              resolvedURL: URL? = nil,
              importPDF: @escaping (URL) throws -> Void, completion: @escaping () -> Void) {
        guard source.canOpen(work), !isDownloading else { return }
        cancelSearch()
        let revision = UUID(); downloadRevision = revision
        downloadingID = work.id; errorMessage = nil; fallbackURL = nil; failedWork = nil
        downloadPhase = source == .sciHub && resolvedURL == nil ? "正在通过 Sci-Hub 查找全文…" : "正在下载并校验 PDF…"
        downloadTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.downloadRevision == revision {
                    self.downloadingID = nil; self.downloadTask = nil; self.downloadPhase = ""
                }
            }
            do {
                let url: URL
                if let resolvedURL {
                    url = resolvedURL
                } else if source == .sciHub, let doi = work.doi {
                    url = try await self.sciHub.resolve(doi: doi, baseURL: sciHubBaseURL)
                    try Task.checkCancellation()
                    guard self.downloadRevision == revision else { return }
                    self.downloadPhase = "正在下载并校验 PDF…"
                } else if let pdf = work.pdfURL {
                    url = pdf
                } else { return }
                let downloaded = try await self.downloader.download(from: url, title: work.title)
                defer { downloaded.cleanup() }
                try Task.checkCancellation()
                guard self.downloadRevision == revision else { return }
                try importPDF(downloaded.url)
                completion()
            } catch {
                guard !Task.isCancelled, self.downloadRevision == revision else { return }
                self.errorMessage = error.localizedDescription
                self.failedWork = work
                if source == .sciHub, let doi = work.doi {
                    self.fallbackURL = try? SciHubResolver.articleURL(doi: doi, baseURL: sciHubBaseURL)
                } else { self.fallbackURL = work.landingURL }
            }
        }
    }

    func cancelDownload() {
        downloadRevision = UUID(); downloadTask?.cancel(); downloadTask = nil; downloadingID = nil; downloadPhase = ""
    }

    func cancelSearch() {
        searchRevision = UUID(); searchTask?.cancel(); searchTask = nil; isSearching = false
    }

    func cancelAll() { cancelSearch(); cancelDownload() }

    func clearDownloadError() { errorMessage = nil; fallbackURL = nil; failedWork = nil }

    private func resetSearch() {
        cancelSearch(); works = []; hasMore = false; hasSearched = false; loadedPage = 0
        errorMessage = nil; fallbackURL = nil; failedWork = nil
    }
}
