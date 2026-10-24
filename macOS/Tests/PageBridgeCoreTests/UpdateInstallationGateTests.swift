import XCTest
@testable import PageBridgeCore

final class UpdateInstallationGateTests: XCTestCase {
    func testIdleInstallationRemainsWithSparkle() {
        var gate = UpdateInstallationGate()
        var resumed = false
        XCTAssertFalse(gate.postpone(whileBusy: false) { resumed = true })
        XCTAssertFalse(gate.hasPendingInstallation)
        XCTAssertNil(gate.takePendingInstallation(whileBusy: false))
        XCTAssertFalse(resumed)
    }

    func testRunningTranslationDefersInstallationUntilCurrentStateIsIdle() {
        var gate = UpdateInstallationGate()
        var calls = 0
        XCTAssertTrue(gate.postpone(whileBusy: true) { calls += 1 })
        XCTAssertNil(gate.takePendingInstallation(whileBusy: true))
        XCTAssertTrue(gate.hasPendingInstallation)
        XCTAssertEqual(calls, 0)
        gate.takePendingInstallation(whileBusy: false)?()
        XCTAssertEqual(calls, 1)
        XCTAssertFalse(gate.hasPendingInstallation)
        XCTAssertNil(gate.takePendingInstallation(whileBusy: false))
    }

    func testNewBusyStatePreventsAStaleIdleEventFromResuming() {
        var gate = UpdateInstallationGate()
        var calls = 0
        _ = gate.postpone(whileBusy: true) { calls += 1 }
        // The caller supplies its current state, not the earlier published event.
        gate.takePendingInstallation(whileBusy: true)?()
        XCTAssertEqual(calls, 0)
        gate.takePendingInstallation(whileBusy: false)?()
        XCTAssertEqual(calls, 1)
    }

    func testAbortedUpdateCannotResumeAfterTranslationEnds() {
        var gate = UpdateInstallationGate()
        var calls = 0
        _ = gate.postpone(whileBusy: true) { calls += 1 }
        gate.cancel()
        gate.takePendingInstallation(whileBusy: false)?()
        XCTAssertEqual(calls, 0)
        XCTAssertFalse(gate.hasPendingInstallation)
    }
}
