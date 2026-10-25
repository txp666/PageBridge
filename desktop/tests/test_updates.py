"""Updater trust boundary, interrupted downloads and pre-install revalidation."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pagebridge import updates


class Response(io.BytesIO):
    def __init__(self, content, length=None):
        super().__init__(content)
        self.headers = {} if length is None else {"Content-Length": str(length)}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Public fixture seed, unrelated to the application signing key.
        self.key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        self.public = base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        self.binary = b"MZ-public-test-installer-fixture" * 40000
        self.payload = dict(schema=1, platform="windows-x64", version="0.4.1", build=14,
                            minWindowsBuild=19041, url="https://github.com/txp666/PageBridge/releases/download/v0.4.1/PageBridge-0.4.1-windows-x64-setup.exe",
                            sha256=hashlib.sha256(self.binary).hexdigest(), bytes=len(self.binary),
                            releaseNotesURL="https://github.com/txp666/PageBridge/releases/tag/v0.4.1")
        (self.root / "VERSION.json").write_text(json.dumps(dict(version="0.4.0", build="13")))
        (self.root / "update-config.json").write_text(json.dumps(dict(repository=updates.REPOSITORY, publicEDKey=self.public)))

    def signed(self, payload=None, message=None):
        payload = self.payload if payload is None else payload
        signature = self.key.sign(updates.signature_message(payload) if message is None else message)
        return json.dumps(dict(payload=payload, signature=base64.b64encode(signature).decode())).encode()

    def manager(self, binary=None, manifest=None, windows_build=22631, length=None):
        raw = self.signed() if manifest is None else manifest
        binary = self.binary if binary is None else binary
        def opener(url):
            return Response(raw if url == updates.FEED_URL else binary, None if url == updates.FEED_URL else length)
        manager = updates.UpdateManager(self.root / "data", self.root, opener=opener, windows_build=windows_build)
        self.addCleanup(lambda: manager.thread and manager.thread.join(5))
        return manager

    def finish(self, manager):
        manager.thread.join(5)
        self.assertFalse(manager.thread.is_alive())
        return manager.status()

    def available(self, manager):
        manager.check()
        self.assertEqual(self.finish(manager)["state"], "available")

    def test_valid_signature_and_preinstall(self):
        self.assertEqual(updates.verify_manifest(self.signed(), self.public), self.payload)
        manager = self.manager(length=len(self.binary))
        self.available(manager)
        manager.download()
        self.assertEqual(self.finish(manager)["state"], "ready")
        result = manager.prepare_install()
        self.assertEqual(Path(result["path"]).read_bytes(), self.binary)
        self.assertEqual(result["sha256"], self.payload["sha256"])
        self.assertFalse(list((manager.root / "updates").glob("*.part")))

    def test_signature_tampering_wrong_key_and_domain(self):
        altered = json.loads(self.signed())
        altered["payload"]["bytes"] += 1
        cases = [json.dumps(altered).encode(), self.signed(message=json.dumps(self.payload).encode())]
        for raw in cases:
            with self.subTest(raw=raw[:40]), self.assertRaises(updates.UpdateError):
                updates.verify_manifest(raw, self.public)
        wrong_key = base64.b64encode(bytes(32)).decode()
        with self.assertRaises(updates.UpdateError):
            updates.verify_manifest(self.signed(), wrong_key)

    def test_strict_fields_numeric_types_and_urls(self):
        cases = [("schema", True), ("build", True), ("build", 0), ("build", 65536),
                 ("bytes", 0), ("bytes", updates.MAX_INSTALLER_BYTES + 1),
                 ("bytes", 12.0), ("version", "00.4.1"), ("version", "65536.0.0"),
                 ("version", "0.4.1-beta"), ("sha256", "A" * 64),
                 ("url", "https://evil.test/setup.exe"), ("platform", "macOS-arm64"),
                 ("releaseNotesURL", "javascript:alert(1)"), ("minWindowsBuild", 10)]
        for field, value in cases:
            payload = dict(self.payload, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(updates.UpdateError):
                updates.verify_manifest(self.signed(payload), self.public)
        for payload in (dict(self.payload, extra=1), {k: v for k, v in self.payload.items() if k != "bytes"}):
            with self.assertRaises(updates.UpdateError):
                updates.verify_manifest(self.signed(payload), self.public)
        envelope = json.loads(self.signed())
        envelope["key"] = self.public
        with self.assertRaises(updates.UpdateError):
            updates.verify_manifest(json.dumps(envelope).encode(), self.public)

    def test_duplicate_json_oversize_and_malformed_signature(self):
        raw = self.signed().replace(b'"schema": 1', b'"schema": 1, "schema": 1')
        for value in (raw, b"x" * (updates.MAX_MANIFEST_BYTES + 1), b'{"payload": NaN}', b"{}", b"\xff"):
            with self.assertRaises(updates.UpdateError):
                updates.verify_manifest(value, self.public)
        envelope = json.loads(self.signed())
        for value in (None, True, "bad-key", base64.b64encode(bytes(63)).decode()):
            envelope["signature"] = value
            with self.assertRaises(updates.UpdateError):
                updates.verify_manifest(json.dumps(envelope).encode(), self.public)

    def test_current_version_and_block_rollback(self):
        for version, build, expected in (("0.4.0", 13, "current"), ("0.3.9", 12, "error"), ("0.3.9", 14, "error"), ("0.4.1", 13, "error")):
            payload = dict(self.payload, version=version, build=build,
                           url=f"https://github.com/txp666/PageBridge/releases/download/v{version}/PageBridge-{version}-windows-x64-setup.exe",
                           releaseNotesURL=f"https://github.com/txp666/PageBridge/releases/tag/v{version}")
            manager = self.manager(manifest=self.signed(payload))
            manager.check()
            self.assertEqual(self.finish(manager)["state"], expected)
            with self.assertRaises(updates.UpdateError):
                manager.download()

    def test_unsupported_windows_never_downloads(self):
        manager = self.manager(windows_build=18363)
        manager.check()
        self.assertEqual(self.finish(manager)["state"], "unsupported")
        with self.assertRaises(updates.UpdateError):
            manager.download()

    def test_download_bad_hash_truncation_excess_and_length(self):
        for binary, length in ((b"x" * len(self.binary), None), (self.binary[:-1], None),
                               (self.binary + b"x", None), (self.binary, len(self.binary) + 1)):
            manager = self.manager(binary=binary, length=length)
            self.available(manager)
            manager.download()
            self.assertEqual(self.finish(manager)["state"], "error")
            self.assertEqual(list((manager.root / "updates").iterdir()), [])
            with self.assertRaises(updates.UpdateError):
                manager.prepare_install()

    def test_changed_download_rejected_before_install(self):
        manager = self.manager()
        self.available(manager)
        manager.download()
        self.finish(manager)
        manager.target.write_bytes(b"x" * len(self.binary))
        with self.assertRaises(updates.UpdateError):
            manager.prepare_install()
        self.assertEqual(manager.status()["state"], "error")

    def test_removed_download_can_be_downloaded_again(self):
        manager = self.manager()
        self.available(manager)
        manager.download()
        self.finish(manager)
        manager.target.unlink()
        with self.assertRaises(updates.UpdateError):
            manager.prepare_install()
        self.assertEqual(manager.status()["state"], "error")
        self.assertIsNone(manager.target)
        manager.download()
        self.assertEqual(self.finish(manager)["state"], "ready")
        self.assertEqual(manager.prepare_install()["bytes"], len(self.binary))

    def test_download_cancel_and_busy_guard_cleanup(self):
        manager = self.manager()
        self.available(manager)
        entered, unblock = threading.Event(), threading.Event()
        class BlockingResponse(Response):
            def read(self, count):
                entered.set()
                unblock.wait(3)
                return super().read(count)
        manager.opener = lambda url: BlockingResponse(self.binary)
        manager.download()
        self.assertTrue(entered.wait(3))
        with self.assertRaises(updates.UpdateError):
            manager.check()
        with self.assertRaises(updates.UpdateError):
            manager.download()
        manager.cancel()
        unblock.set()
        self.assertEqual(self.finish(manager)["state"], "cancelled")
        self.assertEqual(list((manager.root / "updates").iterdir()), [])
        manager.opener = lambda url: Response(self.binary)
        manager.download()
        self.assertEqual(self.finish(manager)["state"], "ready")

    def test_cancel_during_signature_verification_never_becomes_available(self):
        manager = self.manager()
        entered, unblock = threading.Event(), threading.Event()
        original = updates.verify_manifest
        def slow_verifier(*args):
            entered.set()
            unblock.wait(3)
            return original(*args)
        with patch.object(updates, "verify_manifest", side_effect=slow_verifier):
            manager.check()
            self.assertTrue(entered.wait(3))
            manager.cancel()
            unblock.set()
            self.assertEqual(self.finish(manager)["state"], "cancelled")
        self.assertIsNone(manager.raw)

    def test_cancel_acknowledged_before_file_commit_wins(self):
        manager = self.manager()
        self.available(manager)
        entered, unblock = threading.Event(), threading.Event()
        original = updates.os.fsync
        def slow_flush(fd):
            original(fd)
            entered.set()
            unblock.wait(3)
        with patch.object(updates.os, "fsync", side_effect=slow_flush):
            manager.download()
            self.assertTrue(entered.wait(3))
            self.assertEqual(manager.cancel()["state"], "downloading")
            unblock.set()
            self.assertEqual(self.finish(manager)["state"], "cancelled")
        self.assertEqual(list((manager.root / "updates").iterdir()), [])

    def test_untrusted_redirects_and_https_only(self):
        for url in ("http://github.com/x", "https://github.com.evil.test/x", "https://user@github.com/x", "https://github.com:8443/x", "file:///tmp/setup.exe", "https://127.0.0.1/x"):
            self.assertFalse(updates.allowed_url(url))
            with self.assertRaises(updates.UpdateError):
                updates.SafeRedirect().redirect_request(Request(updates.FEED_URL), None, 302, "Found", {}, url)
        self.assertTrue(updates.allowed_url("https://release-assets.githubusercontent.com/github-production-release-asset/x?sig=test"))

    def test_no_target_before_download(self):
        manager = self.manager()
        with self.assertRaises(updates.UpdateError):
            manager.prepare_install()
        self.available(manager)
        with self.assertRaises(updates.UpdateError):
            manager.prepare_install()

    def test_bridge_update_rpc_and_translation_install_guard(self):
        import bridge
        manager = self.manager()
        key = str(manager.root.resolve())
        with patch.dict(updates._MANAGERS, {key: manager}):
            self.assertTrue(updates.ACTIONS.issubset(bridge.LOCAL_ACTIONS))
            status = bridge.dispatch({"action": "update_status"}, manager.root)
            self.assertEqual(status["state"], "idle")
            self.assertEqual(status["currentBuild"], 13)
            with patch.object(bridge, "DocumentStore") as store:
                store.return_value.documents.return_value = [{"status": "running"}]
                with self.assertRaisesRegex(bridge.AppError, "翻译"):
                    bridge.dispatch({"action": "update_prepare_install"}, manager.root)

    @unittest.skipIf(sys.platform == "win32", "Unprivileged Windows symlink creation may be unavailable")
    def test_cache_symlink_is_not_followed(self):
        manager = self.manager()
        self.available(manager)
        manager.root.mkdir()
        elsewhere = self.root / "other"
        elsewhere.mkdir()
        (manager.root / "updates").symlink_to(elsewhere, target_is_directory=True)
        manager.download()
        self.assertEqual(self.finish(manager)["state"], "error")
        self.assertEqual(list(elsewhere.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
