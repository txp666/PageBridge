import Foundation

/// Keeps a user-approved update from interrupting a running translation.
/// The caller rechecks current activity before taking the continuation.
public struct UpdateInstallationGate {
    private var continuation: (() -> Void)?

    public init() {}
    public var hasPendingInstallation: Bool { continuation != nil }

    public mutating func postpone(whileBusy: Bool, resume: @escaping () -> Void) -> Bool {
        guard whileBusy else { return false }
        continuation = resume
        return true
    }

    public mutating func takePendingInstallation(whileBusy: Bool) -> (() -> Void)? {
        guard !whileBusy else { return nil }
        let pending = continuation
        continuation = nil
        return pending
    }

    public mutating func cancel() { continuation = nil }
}
