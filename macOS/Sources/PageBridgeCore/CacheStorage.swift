import Foundation
import Darwin

public enum CacheCategory: String, CaseIterable, Identifiable, Sendable {
    case currentReadings, readingHistory, taskFiles, pendingReadings, searchIndex, minerU, minerUDownloads

    public var id: Self { self }
    public var canClear: Bool {
        switch self {
        case .readingHistory, .taskFiles, .searchIndex, .minerUDownloads: true
        case .currentReadings, .pendingReadings, .minerU: false
        }
    }
}

public struct CacheUsage: Sendable {
    public let locations: [URL]
    public let bytes: Int64
}

public struct CacheCleanup: Sendable {
    public let removed: Int
    public let failed: Int
}

public struct CacheStorage: Sendable {
    public let root: URL
    private let minerURuntime: URL?

    public init(root: URL, minerURuntime: URL? = nil) {
        self.root = root.resolvingSymlinksInPath().standardizedFileURL
        self.minerURuntime = minerURuntime
    }

    private func minerURuntimes() throws -> [URL] {
        if let minerURuntime { return [minerURuntime.resolvingSymlinksInPath()] }
        let environment = ProcessInfo.processInfo.environment
        if let override = environment["PAGEBRIDGE_MINERU_RUNTIME"], !override.isEmpty {
            let path = (override as NSString).expandingTildeInPath
            let base = path.hasPrefix("/") ? nil : try EngineLocation.find().worker.deletingLastPathComponent()
            return [URL(fileURLWithPath: path, isDirectory: true, relativeTo: base).resolvingSymlinksInPath()]
        }
        let home = environment["HOME"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.homeDirectoryForCurrentUser
        let parent = home.appendingPathComponent("Library/Application Support/PageBridge/mineru", isDirectory: true)
        return try children(of: parent.resolvingSymlinksInPath()).filter(isDirectory)
    }

    public func usage() throws -> [CacheCategory: CacheUsage] {
        let repository = try DocumentRepository(root: root)
        return try locations(in: repository.loadDocuments()).mapValues { urls in
            CacheUsage(locations: urls, bytes: urls.reduce(0) { $0 + allocatedBytes(at: $1) })
        }
    }

    public func clear(_ category: CacheCategory) throws -> CacheCleanup {
        guard category.canClear else { throw BridgeError.message("请在文档库管理此类文件。") }
        let repository = try DocumentRepository(root: root)
        let records = try repository.loadDocuments()
        guard !records.contains(where: { $0.status == .running || $0.status == .cancelling }) else {
            throw BridgeError.message("请在当前任务结束后清理缓存。")
        }
        let targets = try locations(in: records)[category] ?? []
        if category == .minerUDownloads {
            var removed = 0, failed = 0
            for (runtime, caches) in Dictionary(grouping: targets, by: { $0.deletingLastPathComponent() }) {
                let result = clearDownloads(caches, in: runtime)
                removed += result.removed; failed += result.failed
            }
            return CacheCleanup(removed: removed, failed: failed)
        }
        return remove(targets, recycle: category == .readingHistory)
    }

    private func clearDownloads(_ caches: [URL], in runtime: URL) -> CacheCleanup {
        // Use the same lock as the engine because another window may be installing.
        let lock = open(runtime.appendingPathComponent("install.lock").path,
                        O_WRONLY | O_CREAT | O_NOFOLLOW | O_NONBLOCK, S_IRUSR | S_IWUSR)
        guard lock >= 0 else { return CacheCleanup(removed: 0, failed: caches.count) }
        defer { _ = close(lock) }
        guard flock(lock, LOCK_EX | LOCK_NB) == 0 else { return CacheCleanup(removed: 0, failed: caches.count) }
        return remove(caches, recycle: false)
    }

    private func remove(_ targets: [URL], recycle: Bool) -> CacheCleanup {
        var removed = 0, failed = 0
        for target in targets {
            do {
                if recycle { try FileManager.default.trashItem(at: target, resultingItemURL: nil) }
                else { try FileManager.default.removeItem(at: target) }
                removed += 1
            } catch { failed += 1 }
        }
        return CacheCleanup(removed: removed, failed: failed)
    }

    private func locations(in records: [DocumentRecord]) throws -> [CacheCategory: [URL]] {
        let documents = root.appendingPathComponent("documents", isDirectory: true)
        let documentsPath = documents.resolvingSymlinksInPath().path
        let current = records.compactMap { $0.readingURL?.resolvingSymlinksInPath().standardizedFileURL.path }
        var locations: [CacheCategory: [URL]] = [:]
        var seenFolders = Set<URL>()
        for record in isDirectory(documents) ? records : [] {
            let folder = record.originalURL.deletingLastPathComponent().resolvingSymlinksInPath().standardizedFileURL
            guard folder.deletingLastPathComponent().resolvingSymlinksInPath().path == documentsPath,
                  seenFolders.insert(folder).inserted else { continue }
            let searchIndex = folder.appendingPathComponent("search-text.json")
            if isFile(searchIndex) { locations[.searchIndex, default: []].append(searchIndex) }
            for result in try children(of: folder.appendingPathComponent("results")) where isManagedDirectory(result) {
                let resultPath = result.resolvingSymlinksInPath().path
                let isCurrent = current.contains { $0.hasPrefix(resultPath + "/") }
                if isCurrent || isFile(result.appendingPathComponent("reading.json")) {
                    locations[isCurrent ? .currentReadings : .readingHistory, default: []].append(result)
                }
            }
            for attempt in try children(of: folder.appendingPathComponent("attempts")) where isManagedDirectory(attempt) {
                let saved = folder.appendingPathComponent("results/\(attempt.lastPathComponent)/reading.json")
                locations[protectAttempt(attempt, saved: saved) ? .pendingReadings : .taskFiles, default: []].append(attempt)
            }
        }
        let downloadNames: Set<String> = ["uv-cache", "ms-cache", "hf-cache"]
        for runtime in try minerURuntimes() {
            for item in try children(of: runtime) {
                let category: CacheCategory = downloadNames.contains(item.lastPathComponent) ? .minerUDownloads : .minerU
                locations[category, default: []].append(item)
            }
        }
        return locations
    }

    private static let sizeKeys: Set<URLResourceKey> = [
        .isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey, .totalFileAllocatedSizeKey
    ]

    private func children(of folder: URL) throws -> [URL] {
        guard isDirectory(folder) else { return [] }
        return try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            .filter { (try? $0.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == false }
    }

    private func isDirectory(_ url: URL) -> Bool {
        guard let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]) else { return false }
        return values.isDirectory == true && values.isSymbolicLink == false
    }

    private func isManagedDirectory(_ url: URL) -> Bool {
        UUID(uuidString: url.lastPathComponent) != nil && isDirectory(url)
    }

    private func isFile(_ url: URL) -> Bool {
        guard let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey]) else { return false }
        return values.isRegularFile == true && values.isSymbolicLink == false
    }

    private func files(under folder: URL) -> FileManager.DirectoryEnumerator? {
        FileManager.default.enumerator(at: folder, includingPropertiesForKeys: Array(Self.sizeKeys))
    }

    private func protectAttempt(_ attempt: URL, saved: URL) -> Bool {
        var unreadable = false
        guard let entries = FileManager.default.enumerator(at: attempt, includingPropertiesForKeys: nil, errorHandler: { _, _ in
            unreadable = true
            return false
        }) else { return true }
        for case let url as URL in entries where url.lastPathComponent == "reading.json" {
            if !matchesSavedReading(url, saved: saved) { return true }
        }
        return unreadable
    }

    private func matchesSavedReading(_ staged: URL, saved: URL) -> Bool {
        let folder = saved.deletingLastPathComponent()
        guard isDirectory(folder), isDirectory(folder.deletingLastPathComponent()) else { return false }
        func identical(_ left: URL, _ right: URL) -> Bool {
            isFile(left) && isFile(right) && FileManager.default.contentsEqual(atPath: left.path, andPath: right.path)
        }
        guard identical(staged, saved),
              let files = try? FileManager.default.contentsOfDirectory(at: staged.deletingLastPathComponent(),
                                                                        includingPropertiesForKeys: nil) else { return false }
        return files.filter { $0.lastPathComponent.hasPrefix("background-") && $0.pathExtension == "png" }
            .allSatisfy { identical($0, folder.appendingPathComponent($0.lastPathComponent)) }
    }

    private func allocatedBytes(at url: URL) -> Int64 {
        func fileBytes(_ file: URL) -> Int64 {
            guard let values = try? file.resourceValues(forKeys: Self.sizeKeys),
                  values.isRegularFile == true, values.isSymbolicLink == false else { return 0 }
            return Int64(values.totalFileAllocatedSize ?? values.fileSize ?? 0)
        }
        guard isDirectory(url), let entries = files(under: url) else { return fileBytes(url) }
        return entries.reduce(0) { total, item in total + ((item as? URL).map(fileBytes) ?? 0) }
    }
}
