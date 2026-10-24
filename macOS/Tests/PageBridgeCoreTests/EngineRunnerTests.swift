import XCTest
@testable import PageBridgeCore

final class EngineRunnerTests: XCTestCase {
    private var directory: URL!
    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("pagebridge-protocol-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try XCTSkipUnless(FileManager.default.isExecutableFile(atPath: "/usr/bin/python3"))
    }
    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: directory) }
    private func runner(body: String) throws -> EngineRunner {
        let script = directory.appendingPathComponent("worker.py")
        let header = """
        import sys,json,os,time
        try:
            os.setsid()
        except PermissionError:
            pass
        r=json.loads(sys.stdin.readline())
        def emit(kind,seq,**kw):
            print(json.dumps(dict(protocolVersion=1,jobId=r['jobId'],attemptId=r['attemptId'],sequence=seq,type=kind,**kw)),flush=True)

        """
        try (header + body).write(to: script, atomically: true, encoding: .utf8)
        return EngineRunner(location: EngineLocation(python: URL(fileURLWithPath: "/usr/bin/python3"), worker: script))
    }
    func testOnlyMatchingTerminalResultIsAccepted() async throws {
        let engine = try runner(body: "emit('ready',0)\nemit('result',1,message='verified')\n")
        let result = try await engine.run(WorkerRequest(action: "health"))
        XCTAssertEqual(result.message, "verified")
    }
    func testImportsAndChildProcessesDoNotModifySignedRuntime() async throws {
        try "VALUE = 42\n".write(to: directory.appendingPathComponent("bundle_module.py"), atomically: true, encoding: .utf8)
        let engine = try runner(body: "import bundle_module,subprocess\nassert sys.dont_write_bytecode\nsubprocess.run([sys.executable,'-c','import sys,bundle_module; assert sys.dont_write_bytecode'],check=True)\nemit('ready',0)\nemit('result',1,message='readonly')\n")
        let result = try await engine.run(WorkerRequest(action: "health"))
        XCTAssertEqual(result.message, "readonly")
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent("__pycache__").path), "Imports must preserve the signed app resource seal")
    }
    func testMalformedDuplicateAndMissingResultsAreRejected() async throws {
        for body in ["print('not-json',flush=True)\n", "emit('ready',0)\nemit('result',0)\n", "emit('ready',0)\n", "emit('result',0)\nemit('result',1)\n", "r['jobId']='wrong'\nemit('result',0)\n"] {
            let engine = try runner(body: body)
            do { _ = try await engine.run(WorkerRequest(action: "health")); XCTFail("Invalid protocol accepted") }
            catch { XCTAssertTrue(error is BridgeError) }
        }
    }
    func testCancellationWinsOverLateResultAndReapsProcessGroup() async throws {
        let started = Date()
        let engine = try runner(body: "import signal,subprocess\nsubprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\nsignal.signal(signal.SIGTERM,lambda *_: (emit('result',1),sys.exit(0)))\nemit('ready',0)\ntime.sleep(30)\n")
        do {
            _ = try await engine.run(WorkerRequest(action: "health")) { event in if event.type == "ready" { engine.cancel() } }
            XCTFail("Cancelled task accepted a result")
        } catch { guard case BridgeError.cancelled = error else { return XCTFail("Expected cancellation, got \(error)") } }
        XCTAssertLessThan(Date().timeIntervalSince(started), 5, "Progress must arrive before EOF so the running task can be cancelled promptly")
    }
    func testCancellationKillsChildAfterLeaderHasExited() async throws {
        let engine = try runner(body: "import subprocess\nchild=subprocess.Popen([sys.executable,'-c',\"import signal,time,sys; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',file=sys.stderr,flush=True); time.sleep(30)\"],stderr=subprocess.PIPE)\nchild.stderr.readline()\nemit('ready',0)\ntime.sleep(30)\n")
        let started = Date()
        do {
            _ = try await engine.run(WorkerRequest(action: "health")) { event in if event.type == "ready" { engine.cancel() } }
            XCTFail("Cancellation accepted a result")
        } catch { guard case BridgeError.cancelled = error else { return XCTFail("Expected cancellation, got \(error)") } }
        XCTAssertLessThan(Date().timeIntervalSince(started), 6, "Must kill the surviving group even after its leader exits")
    }
}
