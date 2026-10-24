import Foundation
import Security

public enum KeychainStore {
    private static let serviceName = "com.pagebridge.translation-service"

    public static func save(key: String, serviceID: UUID) throws {
        if key.isEmpty { try delete(serviceID: serviceID); return }
        let value = Data(key.utf8)
        let query = baseQuery(serviceID)
        let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: value] as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = value
            item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            let addStatus = SecItemAdd(item as CFDictionary, nil)
            guard addStatus == errSecSuccess else { throw KeychainError(status: addStatus) }
        } else if status != errSecSuccess {
            throw KeychainError(status: status)
        }
    }

    public static func read(serviceID: UUID) throws -> String? {
        var query = baseQuery(serviceID)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw KeychainError(status: status) }
        guard let data = item as? Data, let key = String(data: data, encoding: .utf8) else {
            throw KeychainError(status: errSecDecode)
        }
        return key
    }

    public static func delete(serviceID: UUID) throws {
        let status = SecItemDelete(baseQuery(serviceID) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError(status: status) }
    }

    private static func baseQuery(_ id: UUID) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: serviceName,
         kSecAttrAccount as String: id.uuidString,
         kSecAttrSynchronizable as String: false]
    }
}

public struct KeychainError: LocalizedError {
    public let status: OSStatus
    public var errorDescription: String? {
        "无法访问 macOS 钥匙串（状态 \(status)）。请确认钥匙串已解锁并允许 PageBridge 访问。"
    }
}
