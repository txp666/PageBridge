import Foundation

/// One terminology list. The text keeps the user's own lines; `engine/glossary.py` parses it.
public struct Glossary: Identifiable, Equatable, Sendable {
    public static let maximumCharacters = 100_000
    public static let maximumNameLength = 60

    public var id: String
    public var name: String
    public var text: String
    public var isBuiltIn: Bool
    /// nil means the glossary suits any source or target language.
    public var sourceLanguage: String?
    public var targetLanguage: String?

    public init(id: String, name: String, text: String, isBuiltIn: Bool, sourceLanguage: String? = nil, targetLanguage: String? = nil) {
        self.id = id; self.name = name; self.text = text; self.isBuiltIn = isBuiltIn
        self.sourceLanguage = sourceLanguage; self.targetLanguage = targetLanguage
    }

    public var entryCount: Int { Self.entryCount(text) }

    public func applies(source: String, target: String) -> Bool {
        (sourceLanguage == nil || sourceLanguage == source) && (targetLanguage == nil || targetLanguage == target)
    }

    /// Counts lines the way the engine reads them: blank lines and `#` comments are not entries.
    public static func entryCount(_ text: String) -> Int {
        text.split(whereSeparator: \.isNewline).filter {
            let line = $0.trimmingCharacters(in: .whitespaces)
            return !line.isEmpty && !line.hasPrefix("#")
        }.count
    }

    public struct Conflict: Equatable, Sendable {
        public var source: String
        public var lines: [Int]
    }

    /// Terms written more than once with different renderings; the last line wins in the engine.
    public static func conflicts(_ text: String) -> [Conflict] {
        var seen: [String: (source: String, lines: [Int], targets: Set<String>)] = [:], order: [String] = []
        for (index, entry) in GlossaryFile.numberedEntries(text) {
            let key = entry.0.lowercased()
            if seen[key] == nil { seen[key] = (entry.0, [], []); order.append(key) }
            seen[key]!.lines.append(index); seen[key]!.targets.insert(entry.1)
        }
        return order.compactMap { key in
            let item = seen[key]!
            return item.targets.count > 1 ? Conflict(source: item.source, lines: item.lines) : nil
        }
    }
}

/// Built-in glossaries ship read-only with the engine; the user's own live in `glossaries.json`.
public struct GlossaryLibrary: Sendable {
    public static let maximumGlossaries = 200
    public static let builtInPrefix = "builtin:"

    public private(set) var builtIn: [Glossary] = []
    public private(set) var user: [Glossary] = []
    public var lastUsedIDs: [String] = []

    public init() {}

    public var all: [Glossary] { user + builtIn }
    public func glossary(_ id: String?) -> Glossary? {
        guard let id else { return nil }
        return all.first { $0.id == id }
    }

    /// The selected glossaries as one engine text: built-ins first, so the user's own entries win.
    public func combinedText(_ ids: [String]) -> String {
        let chosen = Set(ids)
        return (builtIn + user).filter { chosen.contains($0.id) }.map(\.text).joined(separator: "\n")
    }

    /// Source-language terms whose rendering appears in a question, so a Chinese question
    /// about an English document still finds the English pages.
    public func expansionTerms(for question: String, limit: Int = 20) -> [String] {
        let lowered = question.lowercased()
        var terms: [String] = []
        for glossary in user + builtIn {
            for (source, target) in GlossaryFile.entries(glossary.text) where !target.isEmpty && target.count >= 2 && lowered.contains(target.lowercased()) {
                if !terms.contains(source.lowercased()) { terms.append(source.lowercased()) }
                if terms.count >= limit { return terms }
            }
        }
        return terms
    }

    private struct Stored: Codable {
        struct Item: Codable {
            var id: String; var name: String; var text: String
            var sourceLanguage: String?; var targetLanguage: String?
        }
        var version: Int
        var lastUsed: [String]?
        var glossaries: [Item]
    }
    private struct Index: Decodable {
        struct Item: Decodable { var id: String; var name: String; var file: String; var sourceLanguage: String?; var targetLanguage: String? }
        var version: Int
        var glossaries: [Item]
    }

    public static func load(root: URL, builtInDirectory: URL?) throws -> GlossaryLibrary {
        var library = GlossaryLibrary()
        if let directory = builtInDirectory,
           let data = try? Data(contentsOf: directory.appendingPathComponent("index.json")),
           let index = try? JSONDecoder().decode(Index.self, from: data), index.version == 1 {
            library.builtIn = index.glossaries.compactMap { item in
                guard !item.file.contains("/"), let text = try? String(contentsOf: directory.appendingPathComponent(item.file), encoding: .utf8) else { return nil }
                return Glossary(id: builtInPrefix + item.id, name: item.name, text: text, isBuiltIn: true,
                                sourceLanguage: item.sourceLanguage, targetLanguage: item.targetLanguage)
            }
        }
        let file = root.appendingPathComponent("glossaries.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return library }
        guard let stored = try? JSONDecoder().decode(Stored.self, from: Data(contentsOf: file)), stored.version == 1 else {
            throw BridgeError.message("术语表文件损坏或版本未知，原文件已保留。")
        }
        library.user = stored.glossaries.map {
            Glossary(id: $0.id, name: $0.name, text: $0.text, isBuiltIn: false, sourceLanguage: $0.sourceLanguage, targetLanguage: $0.targetLanguage)
        }
        library.lastUsedIDs = stored.lastUsed ?? []
        return library
    }

    public func save(root: URL) throws {
        let stored = Stored(version: 1, lastUsed: lastUsedIDs,
                            glossaries: user.map { .init(id: $0.id, name: $0.name, text: $0.text, sourceLanguage: $0.sourceLanguage, targetLanguage: $0.targetLanguage) })
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(stored).write(to: root.appendingPathComponent("glossaries.json"), options: .atomic)
    }

    @discardableResult
    public mutating func create(name: String, text: String = "", sourceLanguage: String? = nil, targetLanguage: String? = nil) throws -> Glossary {
        guard user.count < Self.maximumGlossaries else { throw BridgeError.message("最多保存 \(Self.maximumGlossaries) 个术语表。") }
        let glossary = Glossary(id: UUID().uuidString, name: try Self.checkedName(name), text: try Self.checkedText(text), isBuiltIn: false,
                                sourceLanguage: sourceLanguage, targetLanguage: targetLanguage)
        user.append(glossary)
        return glossary
    }

    @discardableResult
    public mutating func duplicate(_ id: String) throws -> Glossary {
        guard let source = glossary(id) else { throw BridgeError.message("术语表已不存在。") }
        return try create(name: String((source.name + " 副本").prefix(Glossary.maximumNameLength)), text: source.text,
                          sourceLanguage: source.sourceLanguage, targetLanguage: source.targetLanguage)
    }

    public mutating func update(_ id: String, name: String, text: String, sourceLanguage: String?, targetLanguage: String?) throws {
        guard let index = user.firstIndex(where: { $0.id == id }) else { throw BridgeError.message("内置术语表不能修改，请先复制。") }
        user[index].name = try Self.checkedName(name)
        user[index].text = try Self.checkedText(text)
        user[index].sourceLanguage = sourceLanguage
        user[index].targetLanguage = targetLanguage
    }

    /// Adds one line to an own glossary; a later line for the same term replaces earlier ones in the engine.
    public mutating func addEntry(_ id: String, source: String, target: String) throws {
        guard let index = user.firstIndex(where: { $0.id == id }) else { throw BridgeError.message("内置术语表不能修改，请先复制。") }
        let line = GlossaryFile.line(source: source, target: target)
        let text = user[index].text
        user[index].text = try Self.checkedText(text + (text.isEmpty || text.hasSuffix("\n") ? "" : "\n") + line + "\n")
    }

    public mutating func delete(_ id: String) {
        user.removeAll { $0.id == id }
        lastUsedIDs.removeAll { $0 == id }
    }

    static func checkedName(_ name: String) throws -> String {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "未命名术语表" : String(trimmed.prefix(Glossary.maximumNameLength))
    }
    static func checkedText(_ text: String) throws -> String {
        guard text.count <= Glossary.maximumCharacters else { throw BridgeError.message("术语表超过 100,000 字符，请删减后再保存。") }
        return text
    }
}

/// Plain-text and CSV files for sharing glossaries.
public enum GlossaryFile {
    static let header = "# PageBridge 术语表："
    static let languageHeader = "# PageBridge 语言："
    static let separator = try! NSRegularExpression(pattern: #"\t|\s*=>\s*|\s*→\s*|\s*=\s*"#)

    public struct Contents: Sendable {
        public var name: String
        public var text: String
        public var sourceLanguage: String?
        public var targetLanguage: String?
    }

    /// One glossary line; a source containing a separator character uses a tab.
    public static func line(source: String, target: String) -> String {
        let source = source.trimmingCharacters(in: .whitespacesAndNewlines), target = target.trimmingCharacters(in: .whitespacesAndNewlines)
        if target.isEmpty { return source }
        return source.contains("=") || source.contains("→") ? source + "\t" + target : source + " = " + target
    }

    /// Reads a .txt/.tsv (lines as written) or .csv (first column source, second target) file.
    public static func read(_ url: URL) throws -> Contents {
        let data = try Data(contentsOf: url)
        guard data.count <= 2_000_000 else { throw BridgeError.message("\(url.lastPathComponent) 太大，术语表文件最多 2 MB。") }
        guard var text = decode(data) else { throw BridgeError.message("无法读取 \(url.lastPathComponent)：请保存为 UTF-8 或 GB18030 文本。") }
        var contents = Contents(name: url.deletingPathExtension().lastPathComponent, text: "")
        if url.pathExtension.lowercased() == "csv" {
            var rows = csvRows(text)
            if let first = rows.first?.first?.trimmingCharacters(in: .whitespaces).lowercased(), ["原文", "source", "term"].contains(first) { rows.removeFirst() }
            text = rows.compactMap { row -> String? in
                let source = row.first?.trimmingCharacters(in: .whitespaces) ?? ""
                guard !source.isEmpty else { return nil }
                return line(source: source, target: row.count > 1 ? row[1] : "")
            }.joined(separator: "\n") + "\n"
        } else {
            // A file exported by PageBridge names itself and its languages in its first lines.
            while let end = text.firstIndex(where: \.isNewline), text.hasPrefix(header) || text.hasPrefix(languageHeader) {
                let first = String(text[..<end])
                if first.hasPrefix(header) {
                    contents.name = String(first.dropFirst(header.count)).trimmingCharacters(in: .whitespaces)
                } else {
                    let parts = first.dropFirst(languageHeader.count).components(separatedBy: "→").map { $0.trimmingCharacters(in: .whitespaces) }
                    if parts.count == 2 {
                        contents.sourceLanguage = parts[0] == "*" || parts[0].isEmpty ? nil : parts[0]
                        contents.targetLanguage = parts[1] == "*" || parts[1].isEmpty ? nil : parts[1]
                    }
                }
                text = String(text[text.index(after: end)...])
            }
        }
        contents.name = try GlossaryLibrary.checkedName(contents.name)
        contents.text = try GlossaryLibrary.checkedText(text)
        return contents
    }

    public static func write(_ glossary: Glossary, to url: URL) throws {
        let content: String
        if url.pathExtension.lowercased() == "csv" {
            // The BOM lets Excel open the Chinese text as UTF-8.
            content = "\u{FEFF}原文,译文\r\n" + entries(glossary.text).map { csvField($0.0) + "," + csvField($0.1) }.joined(separator: "\r\n") + "\r\n"
        } else {
            var head = header + glossary.name + "\n"
            if glossary.sourceLanguage != nil || glossary.targetLanguage != nil {
                head += languageHeader + (glossary.sourceLanguage ?? "*") + " → " + (glossary.targetLanguage ?? "*") + "\n"
            }
            content = head + glossary.text + (glossary.text.hasSuffix("\n") ? "" : "\n")
        }
        try Data(content.utf8).write(to: url, options: .atomic)
    }

    static func decode(_ data: Data) -> String? {
        var bytes = data
        if bytes.starts(with: [0xEF, 0xBB, 0xBF]) { bytes = bytes.dropFirst(3) }
        if let text = String(data: bytes, encoding: .utf8) { return text }
        let gb18030 = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(CFStringEncodings.GB_18030_2000.rawValue)))
        return String(data: bytes, encoding: gb18030)
    }

    static func entries(_ text: String) -> [(String, String)] { numberedEntries(text).map(\.1) }

    /// Entries with their 1-based line numbers, parsed the way `engine/glossary.py` does.
    static func numberedEntries(_ text: String) -> [(Int, (String, String))] {
        text.components(separatedBy: .newlines).enumerated().compactMap { index, raw in
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#") else { return nil }
            let range = NSRange(line.startIndex..., in: line)
            guard let match = separator.firstMatch(in: line, range: range), let cut = Range(match.range, in: line) else { return (index + 1, (line, "")) }
            return (index + 1, (String(line[..<cut.lowerBound]).trimmingCharacters(in: .whitespaces), String(line[cut.upperBound...]).trimmingCharacters(in: .whitespaces)))
        }
    }

    static func csvField(_ value: String) -> String {
        value.contains(where: { $0 == "," || $0 == "\"" || $0.isNewline }) ? "\"" + value.replacingOccurrences(of: "\"", with: "\"\"") + "\"" : value
    }

    static func csvRows(_ text: String) -> [[String]] {
        var rows: [[String]] = [], row: [String] = [], field = "", quoted = false
        let characters = Array(text)
        var index = 0
        while index < characters.count {
            let character = characters[index]
            if quoted {
                if character == "\"" {
                    if index + 1 < characters.count, characters[index + 1] == "\"" { field.append("\""); index += 1 } else { quoted = false }
                } else { field.append(character) }
            } else if character == "\"" && field.isEmpty {
                quoted = true
            } else if character == "," {
                row.append(field); field = ""
            } else if character.isNewline {
                row.append(field); rows.append(row); row = []; field = ""
            } else {
                field.append(character)
            }
            index += 1
        }
        if !field.isEmpty || !row.isEmpty { row.append(field); rows.append(row) }
        return rows
    }
}
