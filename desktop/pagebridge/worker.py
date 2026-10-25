"""Framework-independent NDJSON worker client with process-tree cancellation."""
from __future__ import annotations

import copy
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .core import AppError


class WindowsJob:
    """Children remain in the job, including when their worker parent exits."""
    def __init__(self, process):
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMITS(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMITS), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        class ACCOUNTING(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [(name, wintypes.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]

        self.accounting_type = ACCOUNTING
        self.limits_type = EXTENDED_LIMITS
        self._member_handles = []
        self._termination_started = False
        self._termination_error = None
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.QueryInformationJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self.api.IsProcessInJob.restype = wintypes.BOOL
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self._last_error = ctypes.get_last_error
        self.handle = self.api.CreateJobObjectW(None, None)
        try:
            limits = EXTENDED_LIMITS()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not self.handle or not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise AppError("无法创建 Windows 翻译进程组。")
            # The worker has not received stdin yet, so it cannot spawn an engine
            # child before assignment. No suspended-thread/PID enumeration race.
            if not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
                raise AppError("无法隔离 Windows 翻译子进程，任务未启动。")
        except BaseException:
            try:
                self.close()
            except AppError:
                pass
            raise

    def _capture_members(self):
        # Keep synchronization handles before TerminateJobObject: Windows can
        # remove members from its accounting/PID list before their kernel
        # process objects become signaled. PIDs alone cannot prove completion.
        capacity = 32
        while capacity <= 16384:
            class PROCESS_IDS(ctypes.Structure):
                _fields_ = [("assigned", ctypes.c_uint32), ("count", ctypes.c_uint32),
                            ("ids", ctypes.c_size_t * capacity)]
            members = PROCESS_IDS()
            success = self.api.QueryInformationJobObject(self.handle, 3, ctypes.byref(members), ctypes.sizeof(members), None)
            if (success or self._last_error() == 234) and (members.assigned > capacity or members.count < members.assigned):
                capacity = max(capacity * 2, members.assigned)
                continue
            if not success or members.count > capacity:
                raise AppError("无法确认 Windows 翻译子进程列表，未确认取消完成。")
            for pid in members.ids[:members.count]:
                handle = self.api.OpenProcess(0x101000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
                if not handle:
                    if self._last_error() == 87:  # PID exited and no longer exists
                        continue
                    raise AppError("无法等待 Windows 翻译子进程，未确认取消完成。")
                belongs = wintypes.BOOL()
                if not self.api.IsProcessInJob(handle, self.handle, ctypes.byref(belongs)):
                    self.api.CloseHandle(handle)
                    raise AppError("无法核对 Windows 翻译进程组，未确认取消完成。")
                if belongs.value:
                    self._member_handles.append(handle)
                else:  # An exited PID was reused by an unrelated process.
                    self.api.CloseHandle(handle)
            return
        raise AppError("Windows 翻译子进程数量超出验证范围，未确认取消完成。")

    def terminate(self):
        if self.handle:
            if not self._termination_started:
                self._termination_started = True
                try:
                    # Deny new child admission while collecting the final job
                    # members. Existing members are unaffected by this limit.
                    limits = self.limits_type()
                    limits.BasicLimitInformation.LimitFlags = 0x2008  # KILL_ON_CLOSE | ACTIVE_PROCESS
                    limits.BasicLimitInformation.ActiveProcessLimit = 1
                    if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                        raise AppError("无法停止 Windows 翻译子进程创建，未确认取消完成。")
                    self._capture_members()
                except AppError as error:
                    self._termination_error = str(error)
            if not self.api.TerminateJobObject(self.handle, 130):
                self._termination_error = "无法终止 Windows 翻译进程组，请重试。"

    def close(self):
        if self.handle:
            self.terminate()
            deadline = time.monotonic() + 5
            for handle in self._member_handles:
                # Wait on the actual process, not job accounting or a sleep.
                remaining = max(0, round((deadline - time.monotonic()) * 1000))
                if self.api.WaitForSingleObject(handle, remaining) != 0:
                    self._termination_error = "Windows 翻译子进程尚未确认退出，请稍后重试。"
                self.api.CloseHandle(handle)
            self._member_handles.clear()
            self.api.CloseHandle(self.handle)
            self.handle = None
            if self._termination_error:
                raise AppError(self._termination_error)


def find_python(engine_root):
    root = Path(engine_root).resolve()
    candidates = [root / "runtime/python.exe", root / ".venv/Scripts/python.exe"] if os.name == "nt" else [root / "runtime/bin/python3", root / ".venv/bin/python"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise AppError("未找到私有翻译引擎，请先运行对应平台的引擎安装脚本。")


def python_launch_executable(executable, env):
    """Avoid the Windows venv launcher spawning before Job Object assignment.

    CPython's own multiprocessing launcher uses __PYVENV_LAUNCHER__ to run the
    base executable directly while retaining the venv's imports and sys.prefix.
    Only do this for our current interpreter; an unrelated runtime must keep its
    own environment. Packaged base runtimes already have no redirector.
    """
    env.pop("__PYVENV_LAUNCHER__", None)
    base = getattr(sys, "_base_executable", None)
    same_path = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    if sys.platform == "win32" and base and same_path(executable, sys.executable) and not same_path(base, sys.executable):
        env["__PYVENV_LAUNCHER__"] = str(executable)
        return base
    return str(executable)


class WorkerTask:
    """Callbacks run on the background thread, never a GUI dispatcher thread."""
    def __init__(self, engine_root, request, on_event=None, on_done=None, python_executable=None):
        self.engine_root = Path(engine_root).resolve()
        self._request = copy.deepcopy(request)
        self.on_event = on_event
        self.on_done = on_done
        self.python = Path(python_executable).resolve() if python_executable else find_python(self.engine_root)
        self.result = None
        self.error = None
        self.cancelled = False
        self._lock = threading.RLock()
        self._process = None
        self._job = None
        self._thread = None
        self._cancel_timer = None
        self._finished = False

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self._thread is not None:
                raise AppError("翻译任务不能重复启动。")
            self._thread = threading.Thread(target=self._execute, name="PageBridge-worker", daemon=True)
            self._thread.start()
        return self

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)
        return not self.running

    def cancel(self):
        with self._lock:
            if self._finished:
                return
            self.cancelled = True
            self._send_cancel()
            if self._cancel_timer is None and self.running:
                self._cancel_timer = threading.Timer(10, self._kill_tree)
                self._cancel_timer.daemon = True
                self._cancel_timer.start()

    def _send_cancel(self):
        child = self._process
        if child and child.poll() is None and child.stdin:
            command = {key: self._request.get(key) for key in ("protocolVersion", "jobId", "attemptId")}
            command["action"] = "cancel"
            try:
                child.stdin.write((json.dumps(command) + "\n").encode("utf-8"))
                child.stdin.flush()
            except (OSError, ValueError):
                pass

    def _kill_tree(self):
        with self._lock:
            child = self._process
            if not child:
                return
            if self._job:
                self._job.terminate()
            elif os.name != "nt":
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif child.poll() is None:
                child.kill()

    def _execute(self):
        child = None
        try:
            payload = json.dumps(self._request, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
            if len(payload) > 65536:
                raise AppError("翻译请求过大。")
            if self._request.get("protocolVersion") != 1 or not all(isinstance(self._request.get(k), str) and self._request[k] for k in ("jobId", "attemptId")):
                raise AppError("翻译任务身份无效。")
            with self._lock:
                if self.cancelled:
                    raise AppError("翻译已取消。")
            env = {k: v for k, v in os.environ.items() if not (k.endswith("API_KEY") or k.endswith("API_TOKEN") or k in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"})}
            env.update(PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
            executable = python_launch_executable(self.python, env)
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            child = subprocess.Popen([executable, "-B", "-u", str(self.engine_root / "worker.py")], cwd=self.engine_root,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, **kwargs)
            with self._lock:
                self._process = child
                if os.name == "nt":
                    self._job = WindowsJob(child)
                child.stdin.write(payload)
                child.stdin.flush()
                if self.cancelled:
                    self._send_cancel()
            del payload
            last_sequence = -1
            terminal = None
            buffer = b""
            while True:
                chunk = child.stdout.read1(65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 1_048_576:
                    raise AppError("引擎协议消息过大。")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError()
                        sequence = event.get("sequence")
                        if (event.get("protocolVersion") != 1 or event.get("jobId") != self._request["jobId"]
                                or event.get("attemptId") != self._request["attemptId"] or type(sequence) is not int
                                or sequence <= last_sequence or terminal is not None
                                or event.get("type") not in {"ready", "stage", "progress", "result", "error"}):
                            raise ValueError()
                        if last_sequence < 0 and event["type"] not in {"ready", "error"}:
                            raise ValueError()
                        progress = event.get("progress")
                        if progress is not None and (isinstance(progress, bool) or not isinstance(progress, (int, float)) or not math.isfinite(progress) or not 0 <= progress <= 1):
                            raise ValueError()
                    except (ValueError, TypeError, KeyError, UnicodeError):
                        raise AppError("引擎消息格式、身份或顺序无效，结果未提交。") from None
                    last_sequence = sequence
                    if event["type"] in {"result", "error"}:
                        terminal = event
                    if self.on_event:
                        self.on_event(copy.deepcopy(event))
            code = child.wait()
            if buffer:
                raise AppError("引擎消息未完整结束。")
            if self.cancelled:
                raise AppError("翻译已取消。")
            if terminal and terminal["type"] == "error":
                raise AppError(str(terminal.get("message") or "翻译引擎返回错误。")[:2000])
            if code != 0 or not terminal or terminal["type"] != "result":
                raise AppError("翻译引擎意外退出，结果未提交。")
            self.result = terminal
        except BaseException as error:
            self.error = str(error) if isinstance(error, AppError) else "无法启动或读取翻译引擎，请检查私有运行环境。"
            self._kill_tree()
            if child and child.poll() is None:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
        finally:
            with self._lock:
                if self._cancel_timer:
                    self._cancel_timer.cancel()
                if self._job:
                    try:
                        self._job.close()
                    except AppError as error:
                        self.result = None
                        self.error = str(error)
                        self.cancelled = False  # Failure to confirm exit is not a completed cancellation.
                    self._job = None
                elif child and os.name != "nt":
                    # Engine children must not outlive the task even after an
                    # apparently successful parent exit.
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self._process = None
                if isinstance(self._request.get("service"), dict):
                    self._request["service"].pop("apiKey", None)
                # Freeze the terminal outcome before releasing the lock. A
                # late control line must not replace a cleanup failure with a
                # misleading "cancelled" result or mutate a successful task.
                self._finished = True
            if child:
                for pipe in (child.stdin, child.stdout):
                    if pipe:
                        try:
                            pipe.close()
                        except (OSError, ValueError):
                            # A failed cancel write can leave buffered stdin
                            # data; closing must not suppress the done callback.
                            pass
            if self.on_done:
                self.on_done(self.result, self.error, self.cancelled)
