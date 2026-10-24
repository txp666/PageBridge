"""Offline signing-boundary regressions; these seeds are public test fixtures."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def module(filename):
    spec = importlib.util.spec_from_file_location(filename, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


create = module("create-appcast.py")
verify = module("verify-release.py")


class ReleaseSignatureTests(unittest.TestCase):
    def setUp(self):
        self.seed = bytes(range(32))
        self.private = Ed25519PrivateKey.from_private_bytes(self.seed)
        self.public = self.private.public_key()
        self.public_base64 = base64.b64encode(self.public.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        self.content = b'<?xml version="1.0"?><rss><channel><item/></channel></rss>\n'
        self.signature = base64.b64encode(self.private.sign(self.content))
        self.signed = self.content + b'<!-- sparkle-signatures:\nedSignature: ' + self.signature + b'\nlength: ' + str(len(self.content)).encode() + b'\n-->\n'

    def test_new_seed_matches_public_key(self):
        create.validate_signing_key(base64.b64encode(self.seed).decode(), self.public_base64)

    def test_new_seed_rejects_wrong_public_key(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            create.validate_signing_key(base64.b64encode(bytes(reversed(self.seed))).decode(), self.public_base64)

    def test_key_diagnostic_never_echoes_invalid_secret(self):
        secret = "private-sensitive-malformed-fixture"
        with self.assertRaises(ValueError) as caught:
            create.validate_signing_key(secret, self.public_base64)
        self.assertNotIn(secret, str(caught.exception))

    def test_rejects_unsupported_key_lengths(self):
        for length in (0, 31, 33, 64, 95, 97):
            with self.subTest(length=length), self.assertRaises(ValueError):
                create.validate_signing_key(base64.b64encode(bytes(length)).decode(), self.public_base64)

    def test_valid_signed_feed(self):
        self.assertEqual(verify.signed_feed(self.signed, self.public).tag, "rss")

    def test_tampered_feed_rejected(self):
        with self.assertRaises(InvalidSignature):
            verify.signed_feed(self.signed.replace(b'<item/>', b'<fake/>'), self.public)

    def test_unsigned_feed_rejected(self):
        with self.assertRaisesRegex(ValueError, "inline signature"):
            verify.signed_feed(self.content, self.public)

    def test_wrong_signature_key_rejected(self):
        other = Ed25519PrivateKey.from_private_bytes(bytes(reversed(self.seed))).public_key()
        with self.assertRaises(InvalidSignature):
            verify.signed_feed(self.signed, other)

    def test_tampered_signed_length_rejected(self):
        with self.assertRaisesRegex(ValueError, "signed length"):
            verify.signed_feed(self.signed.replace(b'length: ', b'length: 1'), self.public)

    def test_content_after_signature_rejected(self):
        with self.assertRaisesRegex(ValueError, "Malformed"):
            verify.signed_feed(self.signed + b'<rss/>', self.public)

    def test_transient_network_error_retries(self):
        operation = Mock(side_effect=[urllib.error.URLError('temporary fixture'), b'ok'])
        with patch.object(verify.time, 'sleep') as sleep:
            self.assertEqual(verify.network_attempt(operation), b'ok')
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_integrity_failure_is_not_retried(self):
        operation = Mock(side_effect=ValueError('checksum differs'))
        with patch.object(verify.time, 'sleep') as sleep, self.assertRaisesRegex(ValueError, 'checksum'):
            verify.network_attempt(operation)
        self.assertEqual(operation.call_count, 1)
        sleep.assert_not_called()

    def test_missing_previous_release_is_not_retried(self):
        operation = Mock(side_effect=urllib.error.HTTPError('https://example.invalid', 404, 'missing', {}, None))
        with patch.object(verify.time, 'sleep') as sleep, self.assertRaises(urllib.error.HTTPError):
            verify.network_attempt(operation)
        self.assertEqual(operation.call_count, 1)
        sleep.assert_not_called()

    def test_publishing_cdn_missing_asset_retries(self):
        operation = Mock(side_effect=[urllib.error.HTTPError('https://example.invalid', 404, 'missing', {}, None), b'ok'])
        with patch.object(verify.time, 'sleep'):
            self.assertEqual(verify.network_attempt(operation, retry_missing=True), b'ok')
        self.assertEqual(operation.call_count, 2)

    def test_latest_feed_retries_only_correctly_signed_previous_bytes(self):
        other_content = b'<rss><channel/></rss>\n'
        signature = base64.b64encode(self.private.sign(other_content))
        old = other_content + b'<!-- sparkle-signatures:\nedSignature: ' + signature + b'\nlength: ' + str(len(other_content)).encode() + b'\n-->\n'
        with patch.object(verify, 'fetch', side_effect=[old, self.signed]) as fetch, patch.object(verify.time, 'sleep'):
            verify.verify_latest_feed('https://example.invalid', self.signed, self.public)
        self.assertEqual(fetch.call_count, 2)

    def test_latest_feed_bad_signature_fails_without_retry(self):
        with patch.object(verify, 'fetch', return_value=self.signed.replace(b'<item/>', b'<fake/>')) as fetch, \
                patch.object(verify.time, 'sleep') as sleep, self.assertRaises(InvalidSignature):
            verify.verify_latest_feed('https://example.invalid', self.signed, self.public)
        self.assertEqual(fetch.call_count, 1)
        sleep.assert_not_called()

    def signing_cli_fixture(self, root):
        dist, tools = root / 'dist', root / 'tools'
        dist.mkdir(); tools.mkdir()
        for name in ('generate_appcast', 'sign_update'):
            (tools / name).write_text('offline mocked tool; never executed')
        config = dict(repository='example/PageBridge', publicEDKey=self.public_base64,
                      feedURL='https://github.com/example/PageBridge/releases/latest/download/appcast.xml', sparkleVersion='2.10.0')
        (root / 'VERSION.json').write_text(json.dumps(dict(version='1.2.3', build='123')))
        (root / 'update-config.json').write_text(json.dumps(config))
        (root / 'CHANGELOG.md').write_text('## 1.2.3 / build 123\n\nOffline fixture.\n')
        info = dict(CFBundleShortVersionString='1.2.3', CFBundleVersion='123', SUPublicEDKey=self.public_base64,
                    SUFeedURL=config['feedURL'], SURequireSignedFeed=True)
        with zipfile.ZipFile(dist / 'PageBridge-1.2.3-macOS-arm64.zip', 'w') as zipped:
            zipped.writestr('PageBridge.app/Contents/Info.plist', plistlib.dumps(info))
            zipped.writestr('PageBridge.app/Contents/Frameworks/Sparkle.framework/Versions/B/Resources/Info.plist',
                            plistlib.dumps(dict(CFBundleShortVersionString='2.10.0')))
        key = root / 'input-fixture.key'
        key.write_text(base64.b64encode(self.seed).decode())
        key.chmod(0o600)
        return dist, tools, key

    def test_signing_cli_uses_protected_temporary_key_and_cleans_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, tools, key = self.signing_cli_fixture(root)
            seen = []
            def run(command, **options):
                ephemeral = Path(command[command.index('--ed-key-file') + 1])
                seen.append(ephemeral)
                self.assertNotEqual(ephemeral, key)
                self.assertEqual(ephemeral.stat().st_mode & 0o777, 0o600)
                self.assertEqual(ephemeral.read_text(), key.read_text())
                self.assertNotIn(key.read_text(), command)
                self.assertNotIn('SPARKLE_PRIVATE_KEY', options['env'])
                if Path(command[0]).name == 'generate_appcast':
                    feed = Path(command[-1]) / 'appcast.xml'
                    feed.write_text('<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><enclosure sparkle:edSignature="fixture"/></item></channel></rss>')
                return subprocess.CompletedProcess(command, 0, stdout='')
            arguments = ['create-appcast.py', '--dist', str(dist), '--tools', str(tools), '--key-file', str(key)]
            with patch.object(create, 'ROOT', root), patch('sys.argv', arguments), \
                    patch.object(create.subprocess, 'run', side_effect=run), \
                    patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY': 'must-not-reach-tool'}), contextlib.redirect_stdout(io.StringIO()):
                create.main()
            self.assertEqual(len(seen), 3)
            self.assertTrue((dist / 'appcast.xml').is_file())
            self.assertTrue(all(not path.exists() for path in seen))
            self.assertTrue(key.is_file())

    def test_signing_cli_redacts_failure_and_cleans_temporary_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, tools, key = self.signing_cli_fixture(root)
            seen = []
            def run(command, **options):
                ephemeral = Path(command[command.index('--ed-key-file') + 1])
                seen.append(ephemeral)
                self.assertEqual(ephemeral.stat().st_mode & 0o777, 0o600)
                return subprocess.CompletedProcess(command, 1, stdout='failure fixture ' + key.read_text())
            arguments = ['create-appcast.py', '--dist', str(dist), '--tools', str(tools), '--key-file', str(key)]
            with patch.object(create, 'ROOT', root), patch('sys.argv', arguments), \
                    patch.object(create.subprocess, 'run', side_effect=run), self.assertRaises(RuntimeError) as caught:
                create.main()
            self.assertNotIn(key.read_text(), str(caught.exception))
            self.assertIn('[redacted]', str(caught.exception))
            self.assertTrue(all(not path.exists() for path in seen))
            self.assertFalse((dist / 'appcast.xml').exists())

    def test_github_token_only_used_for_api_metadata(self):
        response = io.BytesIO(b'{}')
        response.geturl = lambda: 'https://api.github.com/repos/example/PageBridge/releases/latest'
        opener = Mock(); opener.open.return_value = response
        with patch.dict(os.environ, {'GH_TOKEN': 'public-test-token'}), \
                patch.object(verify.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(verify.fetch_github_api(response.geturl()), b'{}')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer public-test-token')

    def test_api_metadata_can_work_without_token(self):
        response = io.BytesIO(b'{}')
        response.geturl = lambda: 'https://api.github.com/repos/example/PageBridge/releases/latest'
        opener = Mock(); opener.open.return_value = response
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(verify.urllib.request, 'build_opener', return_value=opener):
            verify.fetch_github_api(response.geturl())
        self.assertIsNone(opener.open.call_args.args[0].get_header('Authorization'))

    def test_metadata_rejects_non_api_origins_before_network(self):
        for url in ('https://github.com/example', 'https://api.github.com.attacker.invalid/',
                    'http://api.github.com/repos/example', 'https://token@api.github.com/repos/example'):
            with self.subTest(url=url), patch.object(verify.urllib.request, 'build_opener') as opener, self.assertRaises(ValueError):
                verify.fetch_github_api(url)
            opener.assert_not_called()

    def test_metadata_cross_origin_redirect_rejected(self):
        request = urllib.request.Request('https://api.github.com/repos/example', headers={'Authorization': 'Bearer public-test-token'})
        handler = verify.GitHubAPIRedirectHandler()
        for url in ('https://release-assets.githubusercontent.com/file', 'https://github.com/example', 'http://api.github.com/example'):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'left api.github.com'):
                handler.redirect_request(request, None, 302, 'Found', {}, url)

    def test_metadata_same_origin_redirect_preserves_token(self):
        request = urllib.request.Request('https://api.github.com/repos/example', headers={'Authorization': 'Bearer public-test-token'})
        redirected = verify.GitHubAPIRedirectHandler().redirect_request(request, None, 302, 'Found', {}, 'https://api.github.com/repos/renamed')
        self.assertEqual(redirected.get_header('Authorization'), 'Bearer public-test-token')

    def test_public_feed_and_asset_remain_anonymous_with_token_in_environment(self):
        for kind in ('feed', 'asset'):
            response = io.BytesIO(b'fixture')
            response.geturl = lambda: 'https://release-assets.githubusercontent.com/public-fixture'
            with self.subTest(kind=kind), patch.dict(os.environ, {'GH_TOKEN': 'public-test-token'}), \
                    patch.object(verify.urllib.request, 'urlopen', return_value=response) as urlopen:
                if kind == 'feed':
                    verify.fetch('https://github.com/example/PageBridge/releases/latest/download/appcast.xml')
                else:
                    verify.verify_public_asset('https://github.com/example/PageBridge/releases/download/v1.2.3/file',
                        dict(name='fixture.dat', bytes=7, sha256=hashlib.sha256(b'fixture').hexdigest()))
                request = urlopen.call_args.args[0]
                self.assertIsNone(request.get_header('Authorization'))
                self.assertIsNone(request.get_header('Cookie'))

    def test_source_verification_uses_explicit_tag_root_not_new_automation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, automation = root / 'source', root / 'automation'
            files = {'VERSION.json': b'{"version":"1.2.3"}', 'update-config.json': b'{"fixture":true}',
                     '.github/workflows/release.yml': b'name: original tag workflow\n'}
            for directory in (source, automation):
                for name, data in files.items():
                    path = directory / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            (automation / '.github/workflows/release.yml').write_text('name: improved recovery workflow\n')
            archive = root / 'source.zip'
            manifest = {'version':'1.2.3', 'files':{name:dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()) for name,data in files.items()}}
            with zipfile.ZipFile(archive, 'w') as zipped:
                for name, data in files.items():zipped.writestr('PageBridge-1.2.3-source/'+name,data)
                zipped.writestr('PageBridge-1.2.3-source/SOURCE_MANIFEST.json',json.dumps(manifest))
            self.assertEqual(verify.verify_source(archive, '1.2.3', source), 3)
            with self.assertRaisesRegex(ValueError, 'Source release configuration differs'):
                verify.verify_source(archive, '1.2.3', automation)


if __name__ == "__main__":
    unittest.main()
