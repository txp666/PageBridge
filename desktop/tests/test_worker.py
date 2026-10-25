import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pagebridge.worker import WorkerTask, python_launch_executable
from pagebridge.core import AppError


PREAMBLE = '''import sys,json,time,os
r=json.loads(sys.stdin.buffer.readline())
sequence=0
def emit(kind, **extra):
 global sequence
 sequence+=1
 value=dict(protocolVersion=1,jobId=r['jobId'],attemptId=r['attemptId'],sequence=sequence,type=kind)
 value.update(extra)
 print(json.dumps(value,ensure_ascii=False),flush=True)
'''


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.request = dict(protocolVersion=1, jobId="job-1", attemptId="attempt-1", action="health")

    def tearDown(self):
        self.temp.cleanup()

    def task(self, code, **kwargs):
        (self.root / "worker.py").write_text(PREAMBLE + code, encoding="utf-8")
        return WorkerTask(self.root, self.request, python_executable=sys.executable, **kwargs).start()

    def test_success_and_unicode_event(self):
        events = []
        task = self.task("emit('ready')\nemit('progress',progress=0.5,message='中文')\nemit('result')\n", on_event=events.append)
        self.assertTrue(task.join(8))
        self.assertIsNone(task.error)
        self.assertEqual(task.result["type"], "result")
        self.assertEqual(events[1]["message"], "中文")

    def test_windows_venv_launch_avoids_redirector_child(self):
        venv = str(self.root / "venv-python.exe")
        base = str(self.root / "base-python.exe")
        env = {"__PYVENV_LAUNCHER__": "stale"}
        with patch("pagebridge.worker.sys.platform", "win32"), patch("pagebridge.worker.sys.executable", venv), patch("pagebridge.worker.sys._base_executable", base):
            self.assertEqual(python_launch_executable(venv, env), base)
            self.assertEqual(env["__PYVENV_LAUNCHER__"], venv)
            other = str(self.root / "packaged-python.exe")
            self.assertEqual(python_launch_executable(other, env), other)
            self.assertNotIn("__PYVENV_LAUNCHER__", env)

    def test_native_runtime_clears_inherited_venv_launcher(self):
        env = {"__PYVENV_LAUNCHER__": "stale"}
        with patch("pagebridge.worker.sys._base_executable", sys.executable):
            self.assertEqual(python_launch_executable(sys.executable, env), sys.executable)
        self.assertNotIn("__PYVENV_LAUNCHER__", env)

    def test_result_without_handshake_rejected(self):
        task = self.task("emit('result')\n")
        task.join(8)
        self.assertIsNone(task.result)
        self.assertIn("消息", task.error)

    def test_identity_sequence_progress_and_duplicate_terminal(self):
        cases = ["emit('ready',jobId='other')", "emit('ready');emit('progress',sequence=1)",
                 "emit('ready');emit('progress',progress=float('nan'))", "emit('ready');emit('result');emit('result')"]
        for code in cases:
            with self.subTest(code=code):
                task = self.task(code + "\n")
                self.assertTrue(task.join(8))
                self.assertIsNone(task.result)
                self.assertIsNotNone(task.error)

    def test_partial_message_and_nonzero_after_result(self):
        for code in ["emit('ready');sys.stdout.write('{');sys.stdout.flush()", "emit('ready');emit('result');sys.exit(3)"]:
            task = self.task(code + "\n")
            self.assertTrue(task.join(8))
            self.assertIsNone(task.result)
            self.assertIsNotNone(task.error)

    def test_cancel_protocol_and_callback(self):
        ready = threading.Event()
        done = []
        task = self.task("emit('ready')\nc=json.loads(sys.stdin.buffer.readline())\nassert c['action']=='cancel' and c['attemptId']==r['attemptId']\nemit('error',stage='cancelled',message='翻译已取消。')\nsys.exit(130)\n",
                         on_event=lambda e: ready.set(), on_done=lambda *args: done.append(args))
        self.assertTrue(ready.wait(5))
        task.cancel()
        self.assertTrue(task.join(8))
        self.assertTrue(task.cancelled)
        self.assertIsNone(task.result)
        self.assertTrue(done[0][2])

    def test_closed_worker_stdin_does_not_suppress_cancel_completion(self):
        ready = threading.Event()
        done = []
        task = self.task("os.close(0)\nemit('ready')\ntime.sleep(0.3)\nsys.exit(130)\n",
                         on_event=lambda event: ready.set(), on_done=lambda *args: done.append(args))
        self.assertTrue(ready.wait(5))
        task.cancel()
        self.assertTrue(task.join(8))
        self.assertEqual(len(done), 1)
        self.assertTrue(done[0][2])
        self.assertIsNone(done[0][0])

    def test_late_cancel_cannot_mutate_successful_terminal_result(self):
        task = self.task("emit('ready');emit('result')\n")
        self.assertTrue(task.join(8))
        result = task.result
        task.cancel()
        self.assertFalse(task.cancelled)
        self.assertIs(task.result, result)
        self.assertIsNone(task.error)

    def test_late_cancel_cannot_hide_job_cleanup_failure(self):
        class Job:
            def terminate(self): pass
            def close(self): raise AppError('未确认子进程退出')
        done = []
        (self.root / 'worker.py').write_text(PREAMBLE + "emit('ready');emit('result')\n", encoding='utf-8')
        task = WorkerTask(self.root, self.request, python_executable=sys.executable)
        def event(value):
            # On Windows preserve and close the real job safely before injecting
            # the bounded failure stub. The child has already sent its result.
            if value['type'] == 'result':
                if task._job: task._job.close()
                task._job = Job()
        def completed(*outcome):
            task.cancel()  # Models a late control line at the completion edge.
            done.append(outcome)
        task.on_event = event; task.on_done = completed
        task.start(); self.assertTrue(task.join(8))
        self.assertEqual(len(done), 1)
        self.assertIsNone(task.result)
        self.assertIn('未确认', task.error)
        self.assertFalse(task.cancelled)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
    def test_windows_job_cancel_waits_for_actual_worker_and_descendants(self):
        import ctypes
        from ctypes import wintypes
        ready = threading.Event()
        events = []

        def event(value):
            events.append(value)
            ready.set()

        task = self.task("import subprocess\nchild=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nemit('ready',workerPid=os.getpid(),childPid=child.pid)\ntime.sleep(120)\n", on_event=event)
        handle = None
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            self.assertTrue(ready.wait(10))
            # A venv redirector would report a different worker PID and could
            # start outside the job before AssignProcessToJobObject completes.
            self.assertEqual(events[0]["workerPid"], task._process.pid)
            handle = api.OpenProcess(0x100001, False, events[0]["childPid"])
            self.assertTrue(handle)
            task.cancel()
            self.assertTrue(task.join(15))
            self.assertTrue(task.cancelled)
            self.assertIsNone(task.result)
            self.assertEqual(api.WaitForSingleObject(handle, 0), 0)
        finally:
            if task.running:
                task.cancel()
                task._kill_tree()
                task.join(5)
            if handle:
                if api.WaitForSingleObject(handle, 0) != 0:
                    api.TerminateProcess(handle, 130)
                    api.WaitForSingleObject(handle, 5000)
                api.CloseHandle(handle)

    def test_keys_are_only_stdin_and_scrubbed_after_completion(self):
        self.request["service"] = {"apiKey": "fake-test-key"}
        task = self.task("assert 'fake-test-key' not in ' '.join(sys.argv)\nassert r['service']['apiKey']=='fake-test-key'\nemit('ready');emit('result')\n")
        self.assertTrue(task.join(8))
        self.assertIsNone(task.error)
        self.assertNotIn("apiKey", task._request["service"])

    def test_oversized_message_is_rejected(self):
        task = self.task("sys.stdout.write('x'*1048577);sys.stdout.flush()\n")
        self.assertTrue(task.join(8))
        self.assertIsNone(task.result)
        self.assertIn("过大", task.error)


if __name__ == "__main__":
    unittest.main()
