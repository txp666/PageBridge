"""Cross-platform tests of Windows bundle boundaries, using synthetic packages."""
import importlib.util
import json
import os
import shutil
from pathlib import Path
import struct
import tempfile
import unittest
import warnings
import zipfile
from unittest.mock import Mock, patch


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load("windows_builder", "build-windows-engine.py")
verifier = load("windows_verifier", "verify-windows-engine.py")
release = load("windows_package", "windows_package.py")


class StableWindowsPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'source'; self.root.mkdir()
        for name in ('VERSION.json','README.md','LICENSE','THIRD_PARTY_NOTICES.md','windows/Directory.Build.props',
                     'windows/PageBridge.Windows/packages.lock.json','scripts/build-windows.ps1','scripts/windows_package.py',
                     'scripts/build-windows-engine.py','scripts/verify-windows-engine.py','scripts/bundle-profile.json',
                     'scripts/setup-inno.ps1','scripts/windows-installer.iss','docs/WINDOWS.md','docs/USER_GUIDE.md',
                     'desktop/bridge.py','desktop/pagebridge/core.py','demos/README.md',
                     *('engine/'+name for name in release.ENGINE_FILES)):
            path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('public synthetic fixture\n')
        (self.root/'VERSION.json').write_text(json.dumps({'version':'0.4.0','build':'13'}))
        (self.root/'windows/PageBridge.Windows/packages.lock.json').write_text(json.dumps({'version':1,'dependencies':{'fixture':{}}}))
        self.folder=Path(self.temp.name)/release.package_name(self.root);self.folder.mkdir()
        for target,source in release.payload_sources(self.root).items():
            path=self.folder/target;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(self.root/source,path)
        for name in release.UI_FILES+('resources/engine/runtime/python.exe',):
            (self.folder/name).parent.mkdir(parents=True,exist_ok=True)
            (self.folder/name).write_bytes(b'MZ'+b'\0'*126)
        (self.folder/'WINDOWS_BUILD.json').write_text(json.dumps(release.build_metadata(self.root)))
        (self.folder/'resources/windows/app.manifest').write_text('<assembly xmlns="urn:schemas-microsoft-com:asm.v1"><assemblyIdentity version="0.4.0.13"/></assembly>')
        self.archive=self.folder.with_suffix('.zip') # dotted version requires appending, not suffix replacement
        self.archive=Path(str(self.folder)+'.zip')

    def archive_package(self):
        return release.archive_bundle(self.folder,self.archive,self.root)

    def test_archive_matches_exact_sources_documentation_and_version(self):
        result=self.archive_package()
        self.assertEqual((result['status'],result['version'],result['build']),('passed','0.4.0','13'))
        self.assertFalse(result['signed']);self.assertEqual(result['updateMode'],'manual')
        self.assertEqual(release.verify_archive(self.archive,self.root),result)
        self.assertIn(result['sha256'],Path(str(self.archive)+'.sha256').read_text())

    def test_metadata_and_payload_changes_fail_before_archive(self):
        (self.folder/'WINDOWS_BUILD.json').write_text(json.dumps(dict(release.build_metadata(self.root),channel='preview')))
        with self.assertRaisesRegex(ValueError,'metadata'):self.archive_package()
        (self.folder/'WINDOWS_BUILD.json').write_text(json.dumps(release.build_metadata(self.root)))
        (self.folder/'resources/desktop/bridge.py').write_text('different build')
        with self.assertRaisesRegex(ValueError,'source differs'):self.archive_package()

    def test_changed_release_sources_invalidate_archive(self):
        self.archive_package();(self.root/'scripts/windows-installer.iss').write_text('changed installer')
        with self.assertRaisesRegex(ValueError,'exact release source'):release.verify_archive(self.archive,self.root)

    def test_duplicate_case_and_traversal_zip_members_rejected(self):
        self.archive_package()
        for member in ('README.md','readme.md','../escape'):
            bad=Path(self.temp.name)/('bad'+str(len(member))+'.zip')
            shutil.copyfile(self.archive,bad)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                with zipfile.ZipFile(bad,'a') as handle:handle.writestr(self.folder.name+'/'+member,b'bad')
            with self.assertRaises(ValueError):release.verify_archive(bad,self.root)

    def test_invalid_or_duplicate_version_input_rejected(self):
        for text in ('{"version":"0.4.0","build":"13","build":"14"}',
                     '{"version":"0.4.0-preview","build":"13"}',
                     '{"version":"0.4.0","build":13}',
                     '{"version":"0.4.0","build":"65536"}'):
            (self.root/'VERSION.json').write_text(text)
            with self.assertRaises(ValueError):release.load_version(self.root)

    @staticmethod
    def pe_fixture(strings):
        def block(key,value=b'',children=(),kind=0):
            content=bytearray(struct.pack('<HHH',0,len(value)//2 if kind else len(value),kind)+key.encode('utf-16-le')+b'\0\0')
            content.extend(b'\0'*(-len(content)%4));content.extend(value);content.extend(b'\0'*(-len(content)%4))
            for child in children:content.extend(child);content.extend(b'\0'*(-len(content)%4))
            struct.pack_into('<H',content,0,len(content));return bytes(content)
        values=[block(k,(v+'\0').encode('utf-16-le'),kind=1) for k,v in strings.items()]
        table=block('040904B0',children=values,kind=1)
        fixed=struct.pack('<13I',0xfeef04bd,0x10000,4,13,4,13,0x3f,0,0x40004,1,0,0,0)
        blob=block('VS_VERSION_INFO',fixed,[block('StringFileInfo',children=[table],kind=1)])
        resource=bytearray(88+len(blob))
        for offset,name,target in ((0,16,0x80000018),(24,1,0x80000030),(48,1033,72)):
            struct.pack_into('<HHII',resource,offset+12,0,1,name,target)
        struct.pack_into('<IIII',resource,72,4096+88,len(blob),0,0);resource[88:]=blob
        data=bytearray(512+len(resource));data[:2]=b'MZ';struct.pack_into('<I',data,60,128)
        data[128:132]=b'PE\0\0';struct.pack_into('<HH',data,132,0x14c,1);struct.pack_into('<H',data,148,224)
        struct.pack_into('<H',data,152,0x10b);struct.pack_into('<II',data,152+96+16,4096,len(resource))
        struct.pack_into('<IIII',data,376+8,len(resource),4096,len(resource),512);data[512:]=resource
        return bytes(data)

    def test_installer_pe_version_is_bound_to_verified_zip_and_source(self):
        report=self.archive_package(); setup=self.archive.with_name(self.folder.name+'-setup.exe')
        strings={'FileDescription':'PageBridge Setup; source='+release.source_identity_hash(self.root)+'; bundle='+report['bundleManifestSHA256'],
                 'OriginalFilename':setup.name,'ProductVersion':'0.4.0+build.13'}
        setup.write_bytes(self.pe_fixture(strings))
        result=release.verify_installer(setup,self.root)
        self.assertEqual(result['status'],'passed');self.assertEqual(result['bootstrapMachine'],'0x14c')
        strings['FileDescription']=strings['FileDescription'].replace(report['bundleManifestSHA256'],'0'*64)
        setup.write_bytes(self.pe_fixture(strings))
        with self.assertRaisesRegex(ValueError,'release source or ZIP'):release.verify_installer(setup,self.root)

    def test_pe_resource_rejects_overlay_string_and_out_of_range_resource(self):
        with self.assertRaises(ValueError):release.pe_version_info(b'MZ'+b'0'*126+b'0.4.0.13')
        data=bytearray(self.pe_fixture({}));struct.pack_into('<I',data,152+96+16,0xffffff00)
        with self.assertRaisesRegex(ValueError,'leaves'):release.pe_version_info(data)


class WindowsPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stage = Path(self.temp.name) / ".pagebridge-engine-stage-test"
        self.runtime = self.stage / "runtime"
        self.packages = self.runtime / "Lib/site-packages"
        self.packages.mkdir(parents=True)
        self.profile = {"auditedEngineVersions": {"pdf2zh-next": "2.9.0"},
                        "excludedDistributions": {"web-interface": ["gradio", "tzdata"]},
                        "testDirectoryPackages": ["example"]}

    def package(self, name, receipt=None):
        source = self.packages / name / "__init__.py"
        source.parent.mkdir()
        source.write_text("# synthetic package\n")
        info = self.packages / (name + "-1.0.dist-info")
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
        (info / "RECORD").write_text(receipt or f"{name}/__init__.py,,\n{info.name}/METADATA,,\n{info.name}/RECORD,,\n")
        return source

    @unittest.skipUnless(os.name == "nt", "Windows DACL integration")
    def test_staged_payload_inherits_parent_acl(self):
        import ctypes
        from ctypes import wintypes
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p)]
        advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p,
            ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
        advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        with tempfile.TemporaryDirectory() as source:
            (Path(source) / "payload.dll").write_bytes(b"public runtime fixture")
            stage = builder.create_stage(Path(self.temp.name))
            shutil.copytree(source, stage / "runtime")
        for path in (stage, stage / "runtime", stage / "runtime/payload.dll"):
            descriptor = ctypes.c_void_p()
            result = advapi.GetNamedSecurityInfoW(str(path), 1, 4, None, None, None, None,
                                                  ctypes.byref(descriptor))
            self.assertEqual(result, 0)
            try:
                control, revision = wintypes.WORD(), wintypes.DWORD()
                self.assertTrue(advapi.GetSecurityDescriptorControl(descriptor,
                    ctypes.byref(control), ctypes.byref(revision)))
                self.assertFalse(control.value & 0x1000, f"Protected DACL on shipped payload: {path.name}")
            finally:
                kernel.LocalFree(descriptor)

    def test_refuses_non_staging_runtime(self):
        with self.assertRaisesRegex(ValueError, "generated Windows"):
            builder.prune(self.packages, self.profile, {"pdf2zh-next": "2.9.0"})

    def test_refuses_changed_engine_before_removal(self):
        source = self.package("gradio")
        with self.assertRaisesRegex(ValueError, "Re-audit"):
            builder.prune(self.runtime, self.profile, {"pdf2zh-next": "3.0.0"})
        self.assertTrue(source.exists())

    def test_receipt_escape_cannot_delete_external_file(self):
        outside = self.stage / "keep.txt"
        outside.write_text("protected")
        source = self.package("gradio", "gradio/__init__.py,,\n../../../keep.txt,,\n")
        with self.assertRaisesRegex(ValueError, "leaves runtime"):
            builder.prune(self.runtime, self.profile, {"pdf2zh-next": "2.9.0"})
        self.assertEqual(outside.read_text(), "protected")
        self.assertTrue(source.exists())

    def test_windows_timezone_and_retained_runtime_data_survive(self):
        excluded = self.package("gradio")
        timezone = self.package("tzdata")
        data = self.packages / "example/data/weights.bin"
        tests = self.packages / "example/tests/test_unused.py"
        for path in (data, tests):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        result = builder.prune(self.runtime, self.profile, {"pdf2zh-next": "2.9.0"})
        self.assertFalse(excluded.exists())
        self.assertFalse(tests.exists())
        self.assertTrue(timezone.exists())
        self.assertTrue(data.exists())
        self.assertLess(result["after"]["bytes"], result["before"]["bytes"])

    def test_pe_architecture_is_read_from_file_not_host(self):
        binary = bytearray(128)
        binary[:2] = b"MZ"
        binary[0x3c:0x40] = struct.pack("<I", 64)
        binary[64:70] = b"PE\0\0" + struct.pack("<H", 0x8664)
        path = self.stage / "python.exe"
        path.write_bytes(binary)
        self.assertEqual(verifier.pe_machine(path), 0x8664)
        builder.check_interpreter_abi({"pythonPlatform": "win-amd64", "processBits": 64, "architecture": "ARM64"}, path)
        with self.assertRaisesRegex(ValueError, "win-amd64"):
            builder.check_interpreter_abi({"pythonPlatform": "win-arm64", "processBits": 64}, path)
        binary[68:70] = struct.pack("<H", 0xaa64)
        path.write_bytes(binary)
        with self.assertRaisesRegex(ValueError, "PE machine"):
            builder.check_interpreter_abi({"pythonPlatform": "win-amd64", "processBits": 64}, path)
        path.write_bytes(b"not an executable")
        with self.assertRaises(ValueError):
            verifier.pe_machine(path)

    def test_transient_dll_lock_retries_without_deleting_source(self):
        source, target = Mock(), Path('ready')
        locked = PermissionError('Windows image section is still closing')
        locked.winerror = 5
        source.rename.side_effect = [locked, target]
        with patch.object(builder.time, 'sleep') as sleep:
            self.assertEqual(builder.rename_with_retry(source, target), target)
        self.assertEqual(source.rename.call_count, 2)
        sleep.assert_called_once_with(0.5)
        source.unlink.assert_not_called()

    def test_permanent_or_unrelated_rename_failure_is_preserved(self):
        for windows_error in (5, 183):
            source = Mock()
            error = OSError('rename failed')
            error.winerror = windows_error
            source.rename.side_effect = error
            with self.assertRaises(OSError) as raised:
                builder.rename_with_retry(source, Path('ready'), timeout=0)
            self.assertIs(raised.exception, error)
            source.rename.assert_called_once()
            source.unlink.assert_not_called()


if __name__ == "__main__":
    unittest.main()
