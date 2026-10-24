import Foundation
import Darwin

public struct EngineService: Codable, Sendable {
    public var kind: String
    public var provider: String?
    public var baseURL: String
    public var model: String
    public var apiKey: String
    public var timeoutSeconds: Double
    public var concurrency: Int
    public init(configuration: ServiceConfiguration, apiKey: String) {
        kind = configuration.kind.rawValue; provider = configuration.provider.rawValue
        baseURL = configuration.baseURL
        model = configuration.model; self.apiKey = apiKey
        timeoutSeconds = configuration.timeoutSeconds; concurrency = configuration.concurrency
    }
}

public struct WorkerRequest: Codable, Sendable {
    public var protocolVersion = 1
    public var jobId: String
    public var attemptId: String
    public var action: String
    public var inputPath: String?
    public var outputDirectory: String?
    public var sourceLanguage: String?
    public var targetLanguage: String?
    public var pages: String?
    public var service: EngineService?
    public init(action: String, jobID: UUID = UUID(), attemptID: UUID = UUID(), inputPath: String? = nil, outputDirectory: String? = nil, sourceLanguage: String = "en", targetLanguage: String = "zh", pages: String? = nil, service: EngineService? = nil) {
        self.jobId = jobID.uuidString; self.attemptId = attemptID.uuidString; self.action = action
        self.inputPath = inputPath; self.outputDirectory = outputDirectory
        self.sourceLanguage = sourceLanguage; self.targetLanguage = targetLanguage
        self.pages = pages; self.service = service
    }
}

public struct WorkerEvent: Codable, Sendable {
    public var protocolVersion: Int
    public var jobId: String
    public var attemptId: String
    public var sequence: Int
    public var type: String
    public var stage: String?
    public var progress: Double?
    public var message: String?
    public var translatedPath: String?
    public var bilingualPath: String?
    public var engineVersion: String?
}

public struct EngineLocation: Sendable {
    public let python: URL
    public let worker: URL
    public let pythonHome: URL?
    public init(python: URL, worker: URL, pythonHome: URL? = nil) {
        self.python = python; self.worker = worker; self.pythonHome = pythonHome
    }
    public static func find(bundle: Bundle = .main) throws -> EngineLocation {
        let fm = FileManager.default
        if let resources = bundle.resourceURL {
            let engine = resources.appendingPathComponent("engine")
            let runtime = engine.appendingPathComponent("runtime")
            let python = runtime.appendingPathComponent("bin/python3")
            let worker = engine.appendingPathComponent("worker.py")
            if fm.isExecutableFile(atPath: python.path), fm.fileExists(atPath: worker.path) {
                return EngineLocation(python: python, worker: worker, pythonHome: runtime)
            }
        }
        var roots = [URL(fileURLWithPath: fm.currentDirectoryPath)]
        if let executable = bundle.executableURL { roots.append(executable.deletingLastPathComponent()) }
        if let path = ProcessInfo.processInfo.environment["PAGEBRIDGE_ENGINE_ROOT"] { roots.insert(URL(fileURLWithPath: path).deletingLastPathComponent(), at: 0) }
        for start in roots {
            var root = start
            for _ in 0..<7 {
                let engine = root.appendingPathComponent("engine")
                let python = engine.appendingPathComponent(".venv/bin/python")
                let worker = engine.appendingPathComponent("worker.py")
                if fm.isExecutableFile(atPath: python.path), fm.fileExists(atPath: worker.path) {
                    return EngineLocation(python: python, worker: worker)
                }
                root.deleteLastPathComponent()
            }
        }
        throw BridgeError.message("未找到翻译引擎。请运行项目 scripts/build-app.sh 准备完整应用。")
    }
}

public enum BridgeError: LocalizedError {
    case message(String), cancelled
    public var errorDescription: String? {
        switch self { case .message(let value): value; case .cancelled: "翻译已取消" }
    }
}

/// One runner owns one process. Events are parsed away from the main actor.
/// Credentials are delivered exclusively over stdin and never included in argv.
public final class EngineRunner: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var processGroupID: pid_t?
    private var cancelled = false
    private let location: EngineLocation
    public init(location: EngineLocation) { self.location = location }

    public func run(_ request: WorkerRequest, onEvent: @escaping @Sendable (WorkerEvent) -> Void = { _ in }) async throws -> WorkerEvent {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do { continuation.resume(returning: try self.execute(request, onEvent: onEvent)) }
                catch { continuation.resume(throwing: error) }
            }
        }
    }

    public func cancel() {
        lock.lock(); cancelled = true; let current = process; lock.unlock()
        guard let current else { return }
        signal(current, SIGTERM)
        DispatchQueue.global().asyncAfter(deadline: .now() + 3) { [weak self, weak current] in
            guard let self, let current else { return }
            self.lock.lock(); let stillCurrent = self.process === current; self.lock.unlock()
            if stillCurrent { self.signal(current, SIGKILL) }
        }
    }

    private func signal(_ process: Process, _ signal: Int32) {
        let pid = process.processIdentifier
        lock.lock()
        guard self.process === process else { lock.unlock(); return }
        if processGroupID == nil, process.isRunning, getpgid(pid) == pid { processGroupID = pid }
        let group = processGroupID
        lock.unlock()
        // Retain the confirmed group while its pipe is open, even when its
        // leader exits. A surviving child may still own stdout or a request.
        if let group, group > 1, group != getpgrp() { _ = Darwin.kill(-group, signal) }
        else if process.isRunning { _ = Darwin.kill(pid, signal) }
    }

    private func execute(_ request: WorkerRequest, onEvent: @escaping @Sendable (WorkerEvent) -> Void) throws -> WorkerEvent {
        let child = Process()
        let input = Pipe(), output = Pipe(), errors = Pipe()
        child.executableURL = location.python
        // The runtime lives inside the signed app. Imports must never mutate it.
        child.arguments = ["-B", "-u", location.worker.path]
        child.currentDirectoryURL = location.worker.deletingLastPathComponent()
        var env = ProcessInfo.processInfo.environment
        // Do not inherit provider credentials, Python paths or the host's venv.
        for key in Array(env.keys) where key.hasSuffix("API_KEY") || key.hasSuffix("API_TOKEN") || key == "PYTHONPATH" || key == "PYTHONHOME" || key == "VIRTUAL_ENV" { env.removeValue(forKey: key) }
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1" // Also inherited by Python subprocesses.
        if let home = location.pythonHome { env["PYTHONHOME"] = home.path }
        child.environment = env
        child.standardInput = input; child.standardOutput = output; child.standardError = errors
        // Upstream stderr can contain document text. Drain it without retaining it.
        errors.fileHandleForReading.readabilityHandler = { handle in _ = handle.availableData }
        lock.lock()
        guard process == nil, !cancelled else { lock.unlock(); throw BridgeError.cancelled }
        process = child
        lock.unlock()
        defer {
            errors.fileHandleForReading.readabilityHandler = nil
            try? errors.fileHandleForReading.close(); try? output.fileHandleForReading.close()
            lock.lock(); process = nil; processGroupID = nil; lock.unlock()
        }
        try child.run()
        lock.lock()
        if getpgid(child.processIdentifier) == child.processIdentifier { processGroupID = child.processIdentifier }
        lock.unlock()
        lock.lock(); let alreadyCancelled = cancelled; lock.unlock()
        if alreadyCancelled { signal(child, SIGTERM) }
        do {
            var payload = try JSONEncoder().encode(request); payload.append(10)
            try input.fileHandleForWriting.write(contentsOf: payload)
            try input.fileHandleForWriting.close()
        } catch { signal(child, SIGKILL); child.waitUntilExit(); throw error }

        var buffer = Data(), lastSequence = -1
        var terminal: WorkerEvent?
        do {
            while true {
                // read(upToCount:) can wait for a full buffer on a pipe, delaying
                // short progress events until exit. availableData returns as
                // soon as bytes arrive, so cancellation and progress stay live.
                let chunk = output.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                guard buffer.count < 1_048_576 else { throw BridgeError.message("引擎协议消息过大。") }
                while let newline = buffer.firstIndex(of: 10) {
                    let line = buffer[..<newline]; buffer.removeSubrange(...newline)
                    guard !line.isEmpty else { continue }
                    let event: WorkerEvent
                    do { event = try JSONDecoder().decode(WorkerEvent.self, from: Data(line)) }
                    catch { throw BridgeError.message("引擎返回了无法识别的消息。") }
                    guard event.protocolVersion == 1, event.jobId == request.jobId, event.attemptId == request.attemptId,
                          event.sequence > lastSequence, ["ready", "stage", "progress", "result", "error"].contains(event.type), terminal == nil else {
                        throw BridgeError.message("引擎事件身份、顺序或状态不匹配。")
                    }
                    guard lastSequence >= 0 || event.type == "ready" || event.type == "error" else {
                        throw BridgeError.message("引擎未完成协议握手。")
                    }
                    if let value = event.progress, !value.isFinite || !(0...1).contains(value) { throw BridgeError.message("引擎进度数据无效。") }
                    lastSequence = event.sequence
                    if event.type == "result" || event.type == "error" { terminal = event }
                    onEvent(event)
                }
            }
            child.waitUntilExit()
        } catch { signal(child, SIGKILL); child.waitUntilExit(); throw error }
        lock.lock(); let wasCancelled = cancelled; lock.unlock()
        if wasCancelled { signal(child, SIGKILL); throw BridgeError.cancelled }
        guard buffer.isEmpty else { throw BridgeError.message("引擎消息未完整结束。") }
        if let terminal, terminal.type == "error" { throw BridgeError.message(terminal.message ?? "翻译引擎返回错误。") }
        guard child.terminationStatus == 0, let terminal, terminal.type == "result" else {
            throw BridgeError.message("翻译引擎意外退出（\(child.terminationStatus)），未提交结果。")
        }
        return terminal
    }
}
