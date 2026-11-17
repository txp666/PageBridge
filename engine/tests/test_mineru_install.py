import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import structured_reading as reading


TUNA = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
PYPI = "https://pypi.org/simple"
ALIYUN = "https://mirrors.aliyun.com/pypi/simple"
FORBIDDEN = """No solution found when resolving dependencies:
  Because accelerate was not found in the package registry and you require
  accelerate==1.15.0, your requirements are unsatisfiable.
  hint: An index URL returned a 403
  Forbidden error.
"""


class MineruInstallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.attempt = 0

    async def prepare(self, outcomes, env=None):
        self.attempt += 1
        self.runtime = self.root / str(self.attempt)
        python = reading.runtime_python(self.runtime)
        python.parent.mkdir(parents=True)
        python.touch()
        self.indexes, checks = [], 0
        outcomes = iter(outcomes)

        async def process(command, **kwargs):
            nonlocal checks
            if "--check" in command:
                checks += 1
                self.assertFalse((self.runtime / "ready.json").exists())
                return 1 if checks == 1 else 0
            self.indexes.append(command[command.index("--default-index") + 1])
            output, result = next(outcomes)
            kwargs["log"].write(output.encode("utf-8"))
            if isinstance(result, BaseException):
                raise result
            return result

        with (patch.object(reading, "runtime_directory", return_value=self.runtime),
              patch.object(reading, "runtime_environment", return_value=env or {}),
              patch.object(reading, "uv_executable", return_value="fixture-uv"),
              patch.object(reading, "models_available", return_value=True),
              patch.object(reading, "run_process", side_effect=process)):
            return await reading.ensure_runtime(Mock())

    async def test_403_and_timeout_fall_back_before_marking_runtime_ready(self):
        await self.prepare([(FORBIDDEN, 1), ("", TimeoutError()), ("installed\n", 0)])
        self.assertEqual(self.indexes, [TUNA, PYPI, ALIYUN])
        self.assertTrue((self.runtime / "ready.json").is_file())
        log = (self.runtime / "setup.log").read_text(encoding="utf-8")
        self.assertIn(FORBIDDEN, log)
        self.assertIn("installed", log)

    async def test_failed_sources_report_current_cause_not_previous_403(self):
        cases = [
            (FORBIDDEN, 1, "安装源访问异常"),
            ("No solution found: package==403 and package==500 conflict.", 1, "依赖版本冲突"),
            ("Failed to download and build: missing Rust compiler", 1, "尚未确定具体原因"),
            ("", TimeoutError(), "依赖安装超时"),
        ]
        for output, result, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(reading.ReadingError, message):
                await self.prepare([(FORBIDDEN, 1), (output, result), (output, result)])
            self.assertEqual(self.indexes, [TUNA, PYPI, ALIYUN])
            self.assertFalse((self.runtime / "ready.json").exists())

    async def test_disk_and_permission_errors_stop_without_changing_sources(self):
        codes = (112, 5) if sys.platform == "win32" else (28, 13)
        for code, message in zip(codes, ("磁盘空间不足", "目录权限")):
            with self.subTest(code=code), self.assertRaisesRegex(reading.ReadingError, message):
                await self.prepare([(f"安装失败 (os error {code})", 1)])
            self.assertEqual(self.indexes, [TUNA])
            self.assertFalse((self.runtime / "ready.json").exists())

    async def test_custom_index_precedes_fallbacks_without_duplicate_attempt(self):
        await self.prepare([(FORBIDDEN, 1), (FORBIDDEN, 1), ("", 0)], {"PAGEBRIDGE_PYPI_INDEX": PYPI + "/"})
        self.assertEqual(self.indexes, [PYPI + "/", TUNA, ALIYUN])

    async def test_cancellation_does_not_start_another_install(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.prepare([("", asyncio.CancelledError())])
        self.assertEqual(self.indexes, [TUNA])
        self.assertFalse((self.runtime / "ready.json").exists())


if __name__ == "__main__":
    unittest.main()
