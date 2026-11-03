import XCTest
@testable import PageBridgeCore

final class UpdateInstallationGateTests: XCTestCase {
    func testRunningTranslationDefersInstallationUntilCurrentStateIsIdle() {
        var gate = UpdateInstallationGate()
        var calls = 0
        XCTAssertFalse(gate.postpone(whileBusy: false) { calls += 1 })
        XCTAssertTrue(gate.postpone(whileBusy: true) { calls += 1 })
        // The caller supplies its current state, not the earlier published event.
        gate.takePendingInstallation(whileBusy: true)?()
        XCTAssertEqual(calls, 0)
        gate.takePendingInstallation(whileBusy: false)?()
        XCTAssertEqual(calls, 1)
        XCTAssertNil(gate.takePendingInstallation(whileBusy: false))
    }

    func testAbortedUpdateCannotResumeAfterTranslationEnds() {
        var gate = UpdateInstallationGate()
        var calls = 0
        _ = gate.postpone(whileBusy: true) { calls += 1 }
        gate.cancel()
        XCTAssertNil(gate.takePendingInstallation(whileBusy: false))
        XCTAssertEqual(calls, 0)
    }
}
