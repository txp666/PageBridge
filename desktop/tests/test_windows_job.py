"""Win32 lifecycle contract checks without requiring a Windows host."""
import ctypes
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pagebridge.core import AppError
from pagebridge.worker import WindowsJob


class BasicLimits(ctypes.Structure):
    _fields_ = [('LimitFlags', ctypes.c_uint32), ('ActiveProcessLimit', ctypes.c_uint32)]


class Limits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', BasicLimits)]


class Kernel:
    def __init__(self):
        self.events = []; self.error = 0; self.queries = 0
        self.query_failure = False; self.growth = False; self.timeout = False
        self.denied = False; self.gone = set(); self.reused = set(); self.opened = []

    def SetInformationJobObject(self, job, info, pointer, size):
        limits = pointer._obj.BasicLimitInformation
        assert limits.LimitFlags == 0x2008 and limits.ActiveProcessLimit == 1
        self.events.append('deny-new-children'); return True

    def QueryInformationJobObject(self, job, info, pointer, size, result):
        assert info == 3  # Accounting ActiveProcesses must never prove exit.
        self.events.append('query-pids'); self.queries += 1
        if self.query_failure:
            self.error = 5; return False
        value = pointer._obj
        if self.growth and self.queries == 1:
            value.assigned = 40; value.count = 32; self.error = 234; return False
        value.assigned = value.count = 3
        value.ids[:3] = [100, 101, 102]
        return True

    def OpenProcess(self, access, inherit, pid):
        assert access == 0x101000 and not inherit
        self.events.append('open-'+str(pid))
        if pid in self.gone: self.error = 87; return None
        if self.denied: self.error = 5; return None
        self.opened.append(pid); return pid

    def IsProcessInJob(self, process, job, belongs):
        belongs._obj.value = process not in self.reused; return True

    def TerminateJobObject(self, handle, status):
        self.events.append('terminate-job'); return True

    def WaitForSingleObject(self, handle, milliseconds):
        self.events.append('wait-'+str(handle))
        assert milliseconds > 0  # Required actual signal wait, not another zero-time observation.
        return 258 if self.timeout else 0

    def CloseHandle(self, handle): self.events.append('close-'+str(handle)); return True


class WindowsJobContractTests(unittest.TestCase):
    def job(self):
        job = WindowsJob.__new__(WindowsJob)
        job.handle = 999; job.api = Kernel(); job.limits_type = Limits
        job._member_handles = []; job._termination_started = False; job._termination_error = None
        job._last_error = lambda: job.api.error
        return job

    def test_timer_captures_before_termination_and_close_waits_real_handles(self):
        job = self.job(); api = job.api
        job.terminate()  # Timer triggers before worker parent exits.
        self.assertEqual(job._member_handles, [100, 101, 102])
        job.close()  # PID accounting is allowed to be zero by now.
        self.assertEqual(api.queries, 1)
        self.assertLess(api.events.index('open-102'), api.events.index('terminate-job'))
        self.assertLess(api.events.index('wait-102'), api.events.index('close-999'))
        self.assertIsNone(job.handle); self.assertEqual(job._member_handles, [])

    def test_growing_pid_list_exited_pid_and_reused_pid(self):
        job = self.job(); api = job.api
        api.growth = True; api.gone = {100}; api.reused = {102}
        job.close()
        self.assertEqual(api.queries, 2)
        self.assertIn('wait-101', api.events)
        self.assertNotIn('wait-102', api.events)
        self.assertIn('close-102', api.events)

    def test_query_failure_terminates_but_never_reports_confirmed_exit(self):
        job = self.job(); api = job.api; api.query_failure = True
        with self.assertRaisesRegex(AppError, '未确认取消完成'): job.close()
        self.assertIn('terminate-job', api.events); self.assertIn('close-999', api.events)
        self.assertIsNone(job.handle)

    def test_access_denied_is_not_mistaken_for_an_exited_pid(self):
        job = self.job(); job.api.denied = True
        with self.assertRaisesRegex(AppError, '未确认取消完成'): job.close()
        self.assertIn('terminate-job', job.api.events)

    def test_wait_timeout_closes_handles_and_is_reported_as_failure(self):
        job = self.job(); api = job.api; api.timeout = True
        with self.assertRaisesRegex(AppError, '尚未确认退出'): job.close()
        self.assertEqual([e for e in api.events if e.startswith('wait-')], ['wait-100','wait-101','wait-102'])
        self.assertTrue(all('close-'+str(pid) in api.events for pid in [100,101,102,999]))
        self.assertIsNone(job.handle)


if __name__ == '__main__': unittest.main()
