import CryptoKit
import Foundation
import LocalAuthentication
import Security

/// The API key is encrypted to a Secure Enclave key that requires the enrolled fingerprints.
public struct DeepSeekCredentialStore: Sendable {
    private let directory: URL
    private static let ciphersuite = HPKE.Ciphersuite.P256_SHA256_AES_GCM_256

    private struct Envelope: Codable {
        let privateKey: Data
        let encapsulatedKey: Data
        let ciphertext: Data
    }

    public init(root: URL) {
        directory = root.appendingPathComponent("credentials", isDirectory: true)
    }

    public static var hasTouchID: Bool {
        let context = LAContext()
        var reason: NSError?
        let available = context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &reason)
        return SecureEnclave.isAvailable && context.biometryType == .touchID
            && (available || reason?.code == LAError.biometryLockout.rawValue)
    }

    public func contains(_ serviceID: UUID) -> Bool {
        FileManager.default.fileExists(atPath: file(for: serviceID).path)
    }

    public func encryptedKey(for serviceID: UUID) throws -> Data? {
        do { return try Data(contentsOf: file(for: serviceID)) }
        catch let error as CocoaError where error.code == .fileReadNoSuchFile { return nil }
    }

    public func replaceEncryptedKey(_ data: Data?, for serviceID: UUID) throws {
        let destination = file(for: serviceID)
        if let data {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                     attributes: [.posixPermissions: 0o700])
            try data.write(to: destination, options: .atomic)
        } else if contains(serviceID) {
            try FileManager.default.removeItem(at: destination)
        }
    }

    public func seal(_ key: String, for service: ServiceConfiguration) throws -> Data {
        let context = try Self.touchIDContext()
        context.interactionNotAllowed = true
        guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            [.privateKeyUsage, .biometryCurrentSet], nil) else { throw DeepSeekCredentialError.unavailable }
        let enclaveKey = try SecureEnclave.P256.KeyAgreement.PrivateKey(accessControl: access, authenticationContext: context)
        var sender = try HPKE.Sender(recipientKey: enclaveKey.publicKey, ciphersuite: Self.ciphersuite, info: contextInfo(service))
        let ciphertext = try sender.seal(Data(key.utf8))
        let envelope = Envelope(privateKey: enclaveKey.dataRepresentation, encapsulatedKey: sender.encapsulatedKey, ciphertext: ciphertext)
        return try JSONEncoder().encode(envelope)
    }

    public func unlock(for service: ServiceConfiguration) async throws -> String {
        guard let data = try encryptedKey(for: service.id) else { throw DeepSeekCredentialError.setupRequired }
        guard let envelope = try? JSONDecoder().decode(Envelope.self, from: data) else { throw DeepSeekCredentialError.unreadable }
        let context = try Self.touchIDContext()
        defer { context.invalidate() }
        do {
            guard try await context.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics,
                                                    localizedReason: "读取已保存的 DeepSeek API Key") else {
                throw DeepSeekCredentialError.notAuthorized
            }
        } catch {
            throw DeepSeekCredentialError.notAuthorized
        }
        try Task.checkCancellation()
        context.interactionNotAllowed = true
        do {
            let enclaveKey = try SecureEnclave.P256.KeyAgreement.PrivateKey(dataRepresentation: envelope.privateKey,
                                                                            authenticationContext: context)
            var recipient = try HPKE.Recipient(privateKey: enclaveKey, ciphersuite: Self.ciphersuite,
                                               info: contextInfo(service), encapsulatedKey: envelope.encapsulatedKey)
            let plaintext = try recipient.open(envelope.ciphertext)
            guard let key = String(data: plaintext, encoding: .utf8), !key.isEmpty else { throw DeepSeekCredentialError.unreadable }
            return key
        } catch {
            throw DeepSeekCredentialError.unreadable
        }
    }

    private func file(for serviceID: UUID) -> URL {
        directory.appendingPathComponent("deepseek-\(serviceID.uuidString).json")
    }

    private func contextInfo(_ service: ServiceConfiguration) -> Data {
        Data("PageBridge/DeepSeek/v1/\(service.id.uuidString)/\(service.normalized().baseURL)".utf8)
    }

    private static func touchIDContext() throws -> LAContext {
        let context = LAContext()
        context.localizedFallbackTitle = ""
        guard SecureEnclave.isAvailable,
              context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil),
              context.biometryType == .touchID else { throw DeepSeekCredentialError.unavailable }
        return context
    }
}

public enum DeepSeekCredentialError: LocalizedError {
    case setupRequired, unavailable, notAuthorized, unreadable

    public var errorDescription: String? {
        switch self {
        case .setupRequired: "请在翻译服务设置中重新填写一次 DeepSeek API Key，保存后即可用 Touch ID 解锁。"
        case .unavailable: "Touch ID 当前不可用。请确认指纹已设置，并连接支持 Touch ID 的键盘。"
        case .notAuthorized: "Touch ID 验证未完成，请重试。"
        case .unreadable: "已保护的 DeepSeek API Key 无法解锁。请重新填写并保存密钥。"
        }
    }
}
