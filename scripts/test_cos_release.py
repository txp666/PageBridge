"""Release storage boundaries: immutable packages and cleanup only after activation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('cos_release', Path(__file__).with_name('cos-release.py'))
cos_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cos_release)


class CosReleaseTests(unittest.TestCase):
    def test_digest_does_not_consume_the_uploaded_body(self):
        cos = Mock()
        error = RuntimeError('missing')
        error.response = dict(Error=dict(Code='404'))
        cos.head_object.side_effect = error
        raw = b'release package fixture'
        def upload(**fields):
            self.assertEqual(fields['Body'].read(), raw)
            self.assertEqual(fields['ContentLength'], len(raw))
        cos.put_object.side_effect = upload
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.zip'
            path.write_bytes(raw)
            with patch.object(cos_release.verify, 'verify_public_asset'):
                cos_release.put_release_file(cos, 'v0.7.3', path, dict(name=path.name, bytes=len(raw)))
        cos.put_object.assert_called_once()

    def test_existing_different_object_is_never_overwritten(self):
        cos = Mock()
        cos.head_object.return_value = dict(ContentLength=3)
        with self.assertRaisesRegex(ValueError, 'Existing COS object differs'):
            cos_release.put_release_file(cos, 'v0.7.3', Path('unused.zip'), dict(name='unused.zip', bytes=4))
        cos.put_object.assert_not_called()

    def test_existing_equal_size_object_still_requires_hash_verification(self):
        cos = Mock()
        cos.head_object.return_value = dict(ContentLength=4)
        with patch.object(cos_release.verify, 'verify_public_asset', side_effect=ValueError('SHA-256 differs')), \
                self.assertRaisesRegex(ValueError, 'SHA-256 differs'):
            cos_release.put_release_file(cos, 'v0.7.3', Path('unused.zip'), dict(name='unused.zip', bytes=4))
        cos.put_object.assert_not_called()

    def test_prune_removes_only_known_older_release_files(self):
        cos = Mock()
        prefix = cos_release.PREFIX + '/releases/'
        names = ['v0.7.2/PageBridge-0.7.2-windows-x64.zip', 'v0.7.2/legacy-appcast.xml',
                 'v0.7.3/PageBridge-0.7.3-windows-x64.zip', 'v0.7.4/appcast.xml',
                 'v0.7.2/operator-notes.txt', 'latest.json', '../outside.zip']
        cos.get_paginator.return_value.paginate.return_value = [dict(Contents=[dict(Key=prefix + name) for name in names])]
        self.assertEqual(cos_release.prune(cos, 'v0.7.3'), 2)
        self.assertEqual([call.kwargs['Key'] for call in cos.delete_object.call_args_list],
                         [prefix + names[0], prefix + names[1]])

    def release(self, raw):
        def item(name):
            return dict(name=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        return dict(version='0.7.3', build=21,
            assets=[item(name) for name in cos_release.verify.asset_names('0.7.3')],
            compatibility=[item(name) for name in cos_release.LEGACY])

    def test_feed_readback_failure_keeps_old_pointer_and_packages(self):
        cos = Mock()
        raw = b'authenticated feed fixture'
        with patch.object(cos_release, 'current', return_value=dict(version='0.7.2', build=20)), \
                patch.object(cos_release.verify, 'fetch', side_effect=[raw, b'wrong feed']), \
                patch.object(cos_release, 'prune') as prune, self.assertRaisesRegex(ValueError, 'Public COS feed differs'):
            cos_release.activate(cos, self.release(raw))
        prune.assert_not_called()
        self.assertEqual(cos.put_object.call_count, 1)
        self.assertNotIn('latest.json', cos.put_object.call_args.kwargs['Key'])

    def test_activation_rejects_rollback_before_writing(self):
        cos = Mock()
        with patch.object(cos_release, 'current', return_value=dict(version='0.7.4', build=22)), \
                self.assertRaisesRegex(ValueError, 'Refusing to replace'):
            cos_release.activate(cos, self.release(b'feed'))
        cos.put_object.assert_not_called()
        cos.delete_object.assert_not_called()

    def test_public_pointer_contains_only_asset_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            pointer = Path(directory) / 'pointer.json'
            names = [*cos_release.verify.asset_names('0.7.3'), *cos_release.LEGACY]
            files = {name: (Path('unused'), dict(name=name, bytes=1, sha256='0'*64)) for name in names}
            def verified(_cos, _tag, _path, item):
                item.update(publicSHA256Verified=True, httpRangeVerified=True)
            with patch.object(cos_release, 'prepare', return_value=('v0.7.3', files)), \
                    patch.object(cos_release, 'current', return_value=None), \
                    patch.object(cos_release, 'put_release_file', side_effect=verified):
                cos_release.stage(Mock(), Path(directory), dict(build=21), {}, pointer)
            result = json.loads(pointer.read_bytes())
            self.assertTrue(all(set(item) == {'name','bytes','sha256'} for item in result['assets'] + result['compatibility']))

    def test_failed_parallel_transfer_does_not_create_a_ready_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            pointer = Path(directory) / 'pointer.json'
            files = {'failed.zip': (Path('unused'), dict(name='failed.zip', bytes=1))}
            with patch.object(cos_release, 'prepare', return_value=('v0.7.3', files)), \
                    patch.object(cos_release, 'current', return_value=None), \
                    patch.object(cos_release, 'put_release_file', side_effect=ValueError('download differs')), \
                    self.assertRaisesRegex(ValueError, 'download differs'):
                cos_release.stage(Mock(), Path(directory), dict(build=21), {}, pointer)
            self.assertFalse(pointer.exists())


if __name__ == '__main__':
    unittest.main()
