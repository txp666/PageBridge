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
package = module("package-release.py")
ci = module("release-ci.py")
windows_update = module("windows-update.py")


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

    def test_zip_and_installer_use_anonymous_full_and_range_downloads(self):
        data = b'a' * 64
        for suffix in ('zip', 'exe'):
            full = io.BytesIO(data); full.geturl = lambda:'https://release-assets.githubusercontent.com/public'
            partial = io.BytesIO(data[:32]); partial.geturl = full.geturl
            partial.status = 206; partial.headers = {'Content-Range':'bytes 0-31/64'}
            expected = dict(name='fixture.'+suffix, bytes=64, sha256=hashlib.sha256(data).hexdigest())
            with self.subTest(suffix=suffix), patch.dict(os.environ, {'GH_TOKEN':'metadata-only-test-token'}), \
                    patch.object(verify.urllib.request, 'urlopen', side_effect=[full, partial]) as request:
                verify.verify_public_asset('https://github.com/example/PageBridge/releases/download/v0.4.0/fixture.'+suffix, expected)
            self.assertTrue(expected['httpRangeVerified'])
            self.assertTrue(expected['publicSHA256Verified'])
            self.assertEqual(request.call_args_list[1].args[0].get_header('Range'), 'bytes=0-31')
            for call in request.call_args_list:
                self.assertIsNone(call.args[0].get_header('Authorization'))
                self.assertIsNone(call.args[0].get_header('Cookie'))

    def test_source_verification_uses_explicit_tag_root_not_new_automation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, automation = root / 'source', root / 'automation'
            files = {'VERSION.json': b'{"version":"0.3.0"}', 'update-config.json': b'{"fixture":true}',
                     '.github/workflows/release.yml': b'name: original tag workflow\n'}
            for directory in (source, automation):
                for name, data in files.items():
                    path = directory / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            (automation / '.github/workflows/release.yml').write_text('name: improved recovery workflow\n')
            archive = root / 'source.zip'
            manifest = {'version':'0.3.0', 'files':{name:dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()) for name,data in files.items()}}
            with zipfile.ZipFile(archive, 'w') as zipped:
                for name, data in files.items():zipped.writestr('PageBridge-0.3.0-source/'+name,data)
                zipped.writestr('PageBridge-0.3.0-source/SOURCE_MANIFEST.json',json.dumps(manifest))
            self.assertEqual(verify.verify_source(archive, '0.3.0', source), 3)
            with self.assertRaisesRegex(ValueError, 'Source release configuration differs'):
                verify.verify_source(archive, '0.3.0', automation)


class WindowsUpdateSigningTests(unittest.TestCase):
    def setUp(self):
        self.seed = bytes(range(32))
        self.secret = base64.b64encode(self.seed).decode()
        self.private = Ed25519PrivateKey.from_private_bytes(self.seed)
        self.public = self.private.public_key()
        self.key = base64.b64encode(self.public.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        self.payload = dict(schema=1, platform='windows-x64', version='0.4.0', build=13, minWindowsBuild=19041,
            url='https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-windows-x64-setup.exe',
            sha256=hashlib.sha256(b'public installer fixture').hexdigest(), bytes=len(b'public installer fixture'),
            releaseNotesURL='https://github.com/txp666/PageBridge/releases/tag/v0.4.0')

    def test_producer_matches_client_and_independent_canonical_signature(self):
        raw = windows_update.sign_payload(self.payload, self.secret, self.key)
        self.assertEqual(windows_update.contract().verify_manifest(raw, self.key), self.payload)
        envelope = json.loads(raw)
        self.assertEqual(set(envelope), {'payload','signature'})
        canonical = b'PageBridge.Windows.Update.v1\n'+json.dumps(self.payload,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')
        self.public.verify(base64.b64decode(envelope['signature']), canonical)
        with self.assertRaises(InvalidSignature):
            self.public.verify(base64.b64decode(envelope['signature']), canonical.split(b'\n',1)[1])

    def test_wrong_signing_key_and_secret_errors_are_redacted(self):
        for secret in ('private-sensitive-malformed-fixture', base64.b64encode(bytes(reversed(self.seed))).decode(),
                       base64.b64encode(bytes(96)).decode()):
            with self.subTest(length=len(secret)), self.assertRaises(ValueError) as error:
                windows_update.sign_payload(self.payload, secret, self.key)
            self.assertNotIn(secret, str(error.exception))

    def test_generator_cannot_sign_an_invalid_client_payload(self):
        for field,value in [('schema',True),('platform','macos-arm64'),('build',True),('build',0),('bytes',2*1024**3+1),
                            ('sha256','A'*64),('version','00.4.0'),('url','https://example.invalid/installer.exe'),('extra','field')]:
            payload = dict(self.payload); payload[field]=value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):
                windows_update.sign_payload(payload,self.secret,self.key)

    def test_changed_installer_or_release_identity_is_rejected(self):
        raw = windows_update.sign_payload(self.payload,self.secret,self.key)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'PageBridge-0.4.0-windows-x64-setup.exe'
            path.write_bytes(b'public installer fixture')
            self.assertEqual(windows_update.verify_installer_binding(raw,self.key,dict(version='0.4.0',build='13'),path),self.payload)
            with self.assertRaisesRegex(ValueError,'version/build'):
                windows_update.verify_installer_binding(raw,self.key,dict(version='0.4.0',build='14'),path)
            path.write_bytes(b'tampered installer bytes')
            with self.assertRaisesRegex(ValueError,'bytes/checksum'):
                windows_update.verify_installer_binding(raw,self.key,dict(version='0.4.0',build='13'),path)

    def fixture(self, root):
        dist=root/'dist';dist.mkdir()
        (root/'VERSION.json').write_text('{"version":"0.4.0","build":"13"}')
        (root/'update-config.json').write_text(json.dumps(dict(repository='txp666/PageBridge',publicEDKey=self.key)))
        (dist/'PageBridge-0.4.0-windows-x64-setup.exe').write_bytes(b'public installer fixture')
        packages=Mock()
        packages.verify_installer.return_value=dict(status='passed',sha256=self.payload['sha256'],bytes=self.payload['bytes'])
        original=windows_update.load_module
        def loader(name,path):return packages if name=='windows_package' else original(name,path)
        return dist,packages,loader

    def test_cli_outputs_only_public_evidence_and_does_not_spawn_with_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);dist,packages,loader=self.fixture(root)
            before={p.relative_to(root) for p in root.rglob('*')}
            stdout=io.StringIO()
            with patch.object(windows_update,'load_module',side_effect=loader),patch.dict(os.environ,{'SPARKLE_PRIVATE_KEY':self.secret}), \
                    patch('sys.argv',['windows-update.py','--dist',str(dist),'--source-root',str(root)]), \
                    patch('subprocess.run') as run,contextlib.redirect_stdout(stdout):
                windows_update.main()
            run.assert_not_called()
            self.assertTrue(json.loads(stdout.getvalue())['clientParserVerified'])
            self.assertNotIn(self.secret,stdout.getvalue())
            self.assertEqual({p.relative_to(root) for p in root.rglob('*')}-before,{Path('dist/windows-update.json')})
            self.assertEqual(windows_update.verify_manifest((dist/'windows-update.json').read_bytes(),self.key),self.payload)

    def test_unverified_installer_is_never_signed_and_existing_manifest_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);dist,packages,loader=self.fixture(root)
            packages.verify_installer.side_effect=ValueError('installer source mismatch')
            with patch.object(windows_update,'load_module',side_effect=loader),patch.object(windows_update,'sign_payload') as sign:
                with self.assertRaisesRegex(ValueError,'installer source'):
                    windows_update.generate(dist,root)
            sign.assert_not_called()
            self.assertFalse((dist/'windows-update.json').exists())
            (dist/'windows-update.json').write_bytes(b'existing immutable manifest')
            with self.assertRaisesRegex(ValueError,'overwrite'):
                windows_update.generate(dist,root)
            self.assertEqual((dist/'windows-update.json').read_bytes(),b'existing immutable manifest')

    def test_latest_windows_feed_retries_valid_stale_signature_but_not_tampering(self):
        raw=windows_update.sign_payload(self.payload,self.secret,self.key)
        old=windows_update.sign_payload(dict(self.payload,build=12),self.secret,self.key)
        with patch.object(verify,'fetch',side_effect=[old,raw]) as fetch,patch.object(verify.time,'sleep'):
            verify.verify_latest_windows_manifest('https://example.invalid/feed',raw,self.key)
        self.assertEqual(fetch.call_count,2)
        tampered=raw.replace(b'"build": 13',b'"build": 14')
        with patch.object(verify,'fetch',return_value=tampered) as fetch,patch.object(verify.time,'sleep') as sleep, self.assertRaises(ValueError):
            verify.verify_latest_windows_manifest('https://example.invalid/feed',raw,self.key)
        self.assertEqual(fetch.call_count,1);sleep.assert_not_called()


class DualPlatformSourceTests(unittest.TestCase):
    def fixture(self, temporary):
        root = Path(temporary) / 'source'
        root.mkdir()
        names = ['LICENSE', 'engine/uv.lock', 'engine/pyproject.toml', 'engine/.python-version',
                 'engine/setup.sh', 'engine/setup-windows.ps1', 'engine/worker.py',
                 'macOS/Package.swift', 'macOS/Package.resolved', 'desktop/bridge.py',
                 'desktop/pagebridge/core.py', 'desktop/pagebridge/updates.py', 'windows/PageBridge.Windows/PageBridge.Windows.csproj',
                 'windows/PageBridge.Windows/packages.lock.json', 'scripts/build-windows.ps1',
                 'scripts/windows-installer.iss', 'scripts/setup-inno.ps1',
                 'update-config.json', '.github/workflows/release.yml']
        files = {name: ('source fixture ' + name).encode() for name in names}
        files['VERSION.json'] = b'{"version":"0.4.0","build":"13"}'
        for name, data in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        return root, files

    def archive(self, destination, files):
        prefix = 'PageBridge-0.4.0-source/'
        manifest = dict(version='0.4.0', build='13', files={name:dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                                                         for name, data in files.items()})
        with zipfile.ZipFile(destination, 'w') as zipped:
            for name, data in files.items():
                zipped.writestr(prefix + name, data)
            zipped.writestr(prefix + 'SOURCE_MANIFEST.json', json.dumps(manifest))
        return destination

    def test_asset_schema_is_explicit_and_preserves_legacy(self):
        self.assertEqual(len(verify.asset_names('0.3.0')), 7)
        self.assertEqual(len(verify.asset_names('0.4.0')), 12)
        self.assertIn('PageBridge-0.4.0-windows-x64.zip', verify.asset_names('0.4.0'))
        with self.assertRaises(ValueError):
            verify.asset_names('../../unsafe')

    def test_both_platform_source_and_locks_are_exported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, files = self.fixture(temporary)
            self.assertEqual({name for name, _ in package.source_files(root)}, set(files))
            archive = self.archive(Path(temporary) / 'source.zip', files)
            self.assertEqual(verify.verify_source(archive, '0.4.0', root), len(files))

    def test_missing_windows_lock_is_not_corresponding_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, files = self.fixture(temporary)
            files.pop('windows/PageBridge.Windows/packages.lock.json')
            archive = self.archive(Path(temporary) / 'source.zip', files)
            with self.assertRaisesRegex(ValueError, 'platform or dependency lock'):
                verify.verify_source(archive, '0.4.0', root)

    def test_installer_build_source_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, files = self.fixture(temporary)
            files.pop('scripts/windows-installer.iss')
            archive = self.archive(Path(temporary) / 'source.zip', files)
            with self.assertRaisesRegex(ValueError, 'platform or dependency lock'):
                verify.verify_source(archive, '0.4.0', root)

    def test_self_consistent_manifest_cannot_hide_source_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, files = self.fixture(temporary)
            files['desktop/pagebridge/core.py'] = b'different program'
            archive = self.archive(Path(temporary) / 'source.zip', files)
            with self.assertRaisesRegex(ValueError, 'differs from tag'):
                verify.verify_source(archive, '0.4.0', root)

    def test_manifest_cannot_omit_a_source_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, files = self.fixture(temporary)
            files.pop('desktop/pagebridge/core.py')
            archive = self.archive(Path(temporary) / 'source.zip', files)
            with self.assertRaisesRegex(ValueError, 'omits or adds'):
                verify.verify_source(archive, '0.4.0', root)

    def test_tracked_windows_build_outputs_are_rejected(self):
        for name in ('windows/PageBridge.Windows/obj/temporary.json', 'windows/PageBridge.Windows/Bin/app.exe',
                     'desktop/__pycache__/core.pyc', 'windows/.vs/session.json'):
            with self.subTest(name=name), patch.object(package.subprocess, 'run', return_value=Mock(stdout=name.encode()+b'\0')):
                with self.assertRaisesRegex(RuntimeError, 'Build/cache'):
                    package.source_files()

    def test_duplicate_and_windows_escape_zip_members_are_rejected(self):
        for names in (['same', 'same'], ['../escape'], ['C:/escape'], ['folder\\escape']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                verify.verify_zip_members(Mock(namelist=lambda:names))


class DraftPublicationTests(unittest.TestCase):
    def test_identity_binds_tag_source_workflow_and_remote(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'VERSION.json').write_text('{"version":"0.4.0","build":"13"}')
            (root/'update-config.json').write_text('{"repository":"txp666/PageBridge"}')
            workflow = root/'.github/workflows/release.yml'
            workflow.parent.mkdir(parents=True); workflow.write_bytes(b'workflow fixture')
            environment = dict(RELEASE_REF='refs/tags/v0.4.0', WORKFLOW_COMMIT='b'*40,
                               GITHUB_REPOSITORY='txp666/PageBridge', GITHUB_EVENT_NAME='push')
            for defect in (None, 'local-tag', 'remote-tag', 'workflow'):
                head, tag = 'a'*40, 'a'*40
                remote = 'a'*40
                if defect == 'local-tag': tag = 'c'*40
                if defect == 'remote-tag': remote = 'c'*40
                shown = b'wrong workflow' if defect == 'workflow' else workflow.read_bytes()
                values = [head, tag, remote+'\trefs/tags/v0.4.0']
                with self.subTest(defect=defect), patch.object(ci,'ROOT',root), patch.dict(os.environ, environment), \
                        patch.object(ci,'output', side_effect=values), patch.object(ci.subprocess,'check_output',return_value=shown), \
                        patch.object(ci,'release_lookup',return_value=(None,None)):
                    if defect:
                        with self.assertRaises(ValueError):ci.identity()
                    else:
                        result = ci.identity()
                        self.assertEqual(result['sourceCommit'], head)
                        self.assertEqual(result['workflowCommit'], environment['WORKFLOW_COMMIT'])

    def test_verification_dependencies_are_hash_pinned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'engine').mkdir()
            (root/'engine/uv.lock').write_text('\n'.join(
                f'[[package]]\nname="{name}"\nversion="1.0.0"\nwheels=[{{hash="sha256:{"a"*64}"}}]\n'
                for name in ('cryptography','cffi','pycparser')))
            lines = ci.requirements(root).splitlines()
            self.assertEqual(len(lines), 3)
            self.assertTrue(all('==1.0.0 --hash=sha256:' in line for line in lines))

    def metadata(self):
        return dict(draft=True, prerelease=False, tag_name='v0.4.0', target_commitish='a'*40,
                    body='<!-- pagebridge-source:'+'a'*40+' -->', assets=[])

    def fixture(self, root):
        (root / 'dist').mkdir()
        assets = []
        for name in verify.asset_names('0.4.0'):
            data = ('offline fixture ' + name).encode()
            (root / 'dist' / name).write_bytes(data)
            assets.append(dict(name=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        report = dict(version='0.4.0', build='13', repository='txp666/PageBridge', assets=assets,
                      windows=dict(status='passed'), windowsInstaller=dict(status='passed'), windowsUpdateSignatureVerified=True, feedSignatureVerified=True, archiveSignatureVerified=True)
        path = root / 'report.json'; path.write_text(json.dumps(report))
        notes = root / 'notes.md'; notes.write_text('Release fixture.\n')
        identity = dict(tag='v0.4.0', version='0.4.0', build='13', sourceCommit='a'*40,
                        workflowCommit='b'*40, repository='txp666/PageBridge')
        metadata = self.metadata()
        metadata['assets'] = [dict(name=item['name'], size=item['bytes'], state='uploaded', digest='sha256:'+item['sha256']) for item in assets]
        return path, notes, report, identity, metadata

    def test_published_and_unrelated_drafts_are_never_overwritten(self):
        for field, value in (('draft',False), ('prerelease',True), ('target_commitish','c'*40), ('body','unrelated')):
            metadata = self.metadata(); metadata[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                ci.require_owned_draft(metadata, 'v0.4.0', 'a'*40)

    def test_complete_upload_is_verified_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, notes, report, identity, metadata = self.fixture(root)
            with patch.object(ci, 'ROOT', root), patch.object(ci, 'identity', return_value=identity) as identify, \
                    patch.object(ci, 'release_lookup', return_value=('https://api.github.com/draft', self.metadata())), \
                    patch.object(ci, 'output', return_value=json.dumps(metadata)), patch.object(ci, 'run') as run:
                ci.publish(path, notes)
            self.assertEqual(identify.call_count, 2)
            self.assertIn('--draft=false', run.call_args.args[0])
            self.assertEqual(len([call for call in run.call_args_list if '--draft=false' in call.args[0]]), 1)

    def test_missing_platform_or_failed_uploaded_digest_leaves_draft(self):
        for defect in ('missing-windows', 'missing-installer', 'unverified-installer', 'missing-windows-feed', 'unsigned-windows-feed', 'wrong-upload-digest', 'unexpected-asset', 'changed-local-asset', 'tag-moved'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path, notes, report, identity, metadata = self.fixture(root)
                if defect == 'missing-windows':
                    report['assets'] = [a for a in report['assets'] if a['name'] != 'PageBridge-0.4.0-windows-x64.zip']
                    path.write_text(json.dumps(report))
                elif defect == 'missing-installer':
                    report['assets'] = [a for a in report['assets'] if not a['name'].endswith('.exe')]
                    path.write_text(json.dumps(report))
                elif defect == 'unverified-installer':
                    report['windowsInstaller']['status'] = 'failed'
                    path.write_text(json.dumps(report))
                elif defect == 'missing-windows-feed':
                    report['assets'] = [a for a in report['assets'] if a['name'] != 'windows-update.json']
                    path.write_text(json.dumps(report))
                elif defect == 'unsigned-windows-feed':
                    report['windowsUpdateSignatureVerified'] = False
                    path.write_text(json.dumps(report))
                elif defect == 'wrong-upload-digest':
                    metadata['assets'][0]['digest'] = 'sha256:'+'0'*64
                elif defect == 'unexpected-asset':
                    metadata['assets'].append(dict(name='old-stale.zip'))
                elif defect == 'changed-local-asset':
                    (root/'dist'/report['assets'][0]['name']).write_bytes(b'changed')
                identities = [identity, ValueError('Remote tag changed')] if defect == 'tag-moved' else [identity, identity]
                with patch.object(ci, 'ROOT', root), patch.object(ci, 'identity', side_effect=identities), \
                        patch.object(ci, 'release_lookup', return_value=('https://api.github.com/draft', self.metadata())), \
                        patch.object(ci, 'output', return_value=json.dumps(metadata)), patch.object(ci, 'run') as run, self.assertRaises(ValueError):
                    ci.publish(path, notes)
                self.assertFalse(any('--draft=false' in call.args[0] for call in run.call_args_list))

    def test_upload_error_never_reaches_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, notes, _, identity, _ = self.fixture(root)
            with patch.object(ci, 'ROOT', root), patch.object(ci, 'identity', return_value=identity), \
                    patch.object(ci, 'release_lookup', return_value=('https://api.github.com/draft', self.metadata())), \
                    patch.object(ci, 'run', side_effect=subprocess.CalledProcessError(1, ['gh'])) as run, self.assertRaises(subprocess.CalledProcessError):
                ci.publish(path, notes)
            self.assertFalse(any('--draft=false' in call.args[0] for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
