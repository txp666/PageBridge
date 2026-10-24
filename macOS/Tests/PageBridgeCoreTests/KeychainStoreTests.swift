import XCTest
@testable import PageBridgeCore

final class KeychainStoreTests: XCTestCase {
    func testSyntheticKeychainRoundTripWhenExplicitlyEnabled() throws {
        guard ProcessInfo.processInfo.environment["PAGEBRIDGE_TEST_KEYCHAIN"] == "1" else {
            throw XCTSkip("Set PAGEBRIDGE_TEST_KEYCHAIN=1 to verify a disposable synthetic Keychain item.")
        }
        let serviceID = UUID()
        let initial = "synthetic-test-value"
        let updated = "synthetic-test-value-updated"
        defer { try? KeychainStore.delete(serviceID: serviceID) }
        XCTAssertNil(try KeychainStore.read(serviceID: serviceID))
        try KeychainStore.save(key: initial, serviceID: serviceID)
        XCTAssertTrue(try KeychainStore.read(serviceID: serviceID) == initial, "Keychain did not return the synthetic value.")
        try KeychainStore.save(key: updated, serviceID: serviceID)
        XCTAssertTrue(try KeychainStore.read(serviceID: serviceID) == updated, "Keychain did not return the updated synthetic value.")
        try KeychainStore.delete(serviceID: serviceID)
        XCTAssertNil(try KeychainStore.read(serviceID: serviceID))
    }
}
