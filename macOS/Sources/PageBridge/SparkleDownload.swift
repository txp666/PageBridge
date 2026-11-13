import Darwin
import Foundation
import PageBridgeCore

/// Sparkle owns the progress UI, cancellation, archive verification and installation.
/// Its download task reads one ordered stream from our bundled range downloader.
final class SparkleDownload {
    private let process = Process()

    func start(source: URL, size: UInt64) throws -> URL {
        let listener = socket(AF_INET, SOCK_STREAM, 0)
        guard listener >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        let input = FileHandle(fileDescriptor: listener, closeOnDealloc: true)
        defer { try? input.close() }

        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let bound = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { socketAddress in
                bind(listener, socketAddress, length) == 0 && listen(listener, 1) == 0 &&
                    getsockname(listener, socketAddress, &length) == 0
            }
        }
        guard bound else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EADDRNOTAVAIL) }

        let engine = try EngineLocation.find()
        let resources = engine.worker.deletingLastPathComponent()
        let helper = resources.appendingPathComponent("sparkle_download.py")
        let modules = engine.pythonHome != nil ? resources :
            resources.deletingLastPathComponent().appendingPathComponent("desktop/pagebridge")
        let token = UUID().uuidString
        process.executableURL = engine.python
        var environment = ProcessInfo.processInfo.environment
        environment = environment.filter { !$0.key.hasPrefix("PYTHON") }
        environment["PYTHONPATH"] = modules.path
        if let home = engine.pythonHome { environment["PYTHONHOME"] = home.path }
        process.environment = environment
        process.arguments = ["-B", "-s", helper.path, source.absoluteString, String(size), token,
                             String(ProcessInfo.processInfo.processIdentifier)]
        process.standardInput = input
        try process.run()
        return URL(string: "http://127.0.0.1:\(UInt16(bigEndian: address.sin_port))")!
            .appendingPathComponent(token).appendingPathComponent(source.lastPathComponent)
    }

    func cancel() {
        if process.isRunning { process.terminate() }
    }
}
