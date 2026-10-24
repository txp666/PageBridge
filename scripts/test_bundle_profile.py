"""Filesystem boundary regressions for the private runtime packager.

All fixtures live in temporary synthetic staging bundles. No installed Python
environment, application bundle, translation cache, or user PDF is touched.
Run with: python3 scripts/test_bundle_profile.py
"""
import copy
import csv
import os
from pathlib import Path
import tempfile
import unittest

from bundle_profile import prune_runtime


class BundleProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pagebridge-pruner-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = (
            self.root / ".Fixture-staging.app" / "Contents" / "Resources"
            / "engine" / "runtime"
        )
        self.packages = self.runtime / "lib" / "python3.12" / "site-packages"
        self.packages.mkdir(parents=True)
        self.versions = {"pdf2zh-next": "2.9.0", "babeldoc": "0.6.2"}
        self.profile = {
            "profile": "test-only-native-profile",
            "auditedEngineVersions": dict(self.versions),
            "excludedDistributions": {"unused-provider-sdk": ["unused-provider"]},
            "testDirectoryPackages": ["retained"],
            "runtimeDevelopmentPaths": [],
            "runtimeDevelopmentGlobs": [],
        }

    def put(self, relative, payload="fixture data", *, base=None):
        path = (base or self.packages) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload)
        return path

    def distribution(self, name, files, extra_record_paths=()):
        """Create minimal real importlib.metadata-readable wheel metadata."""
        directory = self.packages / (name.replace("-", "_") + "-1.0.dist-info")
        directory.mkdir()
        (directory / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: " + name + "\nVersion: 1.0\n"
        )
        (directory / "LICENSE").write_text("Retain this license with its package.\n")
        for relative in files:
            self.put(relative, name + ": " + relative)
        paths = [*files, *extra_record_paths]
        paths.extend(str((directory / name).relative_to(self.packages))
                     for name in ("METADATA", "LICENSE", "RECORD"))
        with (directory / "RECORD").open("w", newline="") as handle:
            csv.writer(handle).writerows((path, "", "") for path in paths)
        return directory

    def run_profile(self):
        return prune_runtime(self.runtime, self.profile, self.versions)

    def test_record_cannot_escape_runtime_with_parent_components(self):
        outside = self.put("outside-important.txt", "DO NOT CHANGE", base=self.root)
        outside_record = os.path.relpath(outside, self.packages)
        self.assertTrue(outside_record.startswith("../"))
        self.assertEqual((self.packages / outside_record).resolve(), outside.resolve())
        self.distribution("unused-provider", ["unused_provider/client.py"], [outside_record])
        before = {str(p.relative_to(self.runtime)): p.read_bytes()
                  for p in self.runtime.rglob("*") if p.is_file()}

        with self.assertRaisesRegex(ValueError, "staging runtime"):
            self.run_profile()

        self.assertEqual(outside.read_text(), "DO NOT CHANGE")
        after = {str(p.relative_to(self.runtime)): p.read_bytes()
                 for p in self.runtime.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_record_cannot_escape_runtime_through_symlink(self):
        outside = self.put("outside-important.txt", "DO NOT CHANGE", base=self.root)
        link = self.packages / "outside-link.py"
        link.symlink_to(outside)
        self.distribution("unused-provider", [], [link.name])

        with self.assertRaisesRegex(ValueError, "staging runtime"):
            self.run_profile()

        self.assertEqual(outside.read_text(), "DO NOT CHANGE")
        self.assertTrue(link.is_symlink())

    def test_record_directory_cannot_remove_other_namespace_members(self):
        self.distribution("retained-provider", ["shared_namespace/retained/client.py"])
        self.distribution("unused-provider", ["shared_namespace/unused/client.py"],
                          ["shared_namespace"])
        before = {str(p.relative_to(self.runtime)): p.read_bytes()
                  for p in self.runtime.rglob("*") if p.is_file()}

        with self.assertRaisesRegex(ValueError, "files, not directories"):
            self.run_profile()

        after = {str(p.relative_to(self.runtime)): p.read_bytes()
                 for p in self.runtime.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_non_staging_runtimes_are_rejected_without_mutation(self):
        locations = [
            self.root / "environment" / "engine" / "runtime",
            self.root / "Installed.app" / "Contents" / "Resources" / "engine" / "runtime",
            self.root / "Fixture-staging.app" / "Contents" / "Resources" / "engine" / "runtime",
            self.root / ".Fixture-staging.app" / "WrongContents" / "Resources" / "engine" / "runtime",
        ]
        for location in locations:
            with self.subTest(location=location):
                sentinel = self.put("important-runtime.py", "DO NOT CHANGE", base=location)
                with self.assertRaisesRegex(ValueError, "Only a staged private"):
                    prune_runtime(location, self.profile, self.versions)
                self.assertEqual(sentinel.read_text(), "DO NOT CHANGE")

    def test_removing_namespace_member_preserves_sibling_package(self):
        self.distribution("unused-provider", ["shared_namespace/unused/client.py"])
        retained_metadata = self.distribution(
            "retained-provider", ["shared_namespace/retained/client.py"]
        )
        sibling = self.packages / "shared_namespace/retained/client.py"
        before = sibling.read_bytes()

        report = self.run_profile()

        self.assertFalse((self.packages / "shared_namespace/unused").exists())
        self.assertEqual(sibling.read_bytes(), before)
        self.assertTrue((retained_metadata / "METADATA").is_file())
        self.assertTrue((retained_metadata / "LICENSE").is_file())
        self.assertEqual([item["name"] for item in report["removedDistributions"]],
                         ["unused-provider"])
        self.assertIn(("retained-provider", "1.0"), report["retainedDistributions"])

    def test_only_named_package_test_directories_are_pruned(self):
        retained = [
            "retained/__init__.py", "retained/data/layout-model.onnx",
            "retained/data/font.ttf", "retained/testing/helper.py",
            "retained/_testutils.py", "retained/LICENSE", "retained/test.py",
            "retained/contest/fixture.txt", "unlisted/tests/runtime-resource.txt",
        ]
        tests = [
            "retained/tests/test_model.py", "retained/nested/test/fixture.pdf",
        ]
        metadata = self.distribution("retained-library", retained + tests)
        expected = {p: (self.packages / p).read_bytes() for p in retained}
        expected_license = (metadata / "LICENSE").read_bytes()

        report = self.run_profile()

        for path, payload in expected.items():
            with self.subTest(path=path):
                self.assertEqual((self.packages / path).read_bytes(), payload)
        self.assertEqual((metadata / "LICENSE").read_bytes(), expected_license)
        self.assertTrue((metadata / "METADATA").is_file())
        self.assertTrue((metadata / "RECORD").is_file())
        for path in tests:
            self.assertFalse((self.packages / path).exists())
        removed_tests = {
            entry["path"] for entry in report["removedPaths"]
            if entry["reason"] == "package-test-data"
        }
        self.assertEqual(removed_tests, {
            "lib/python3.12/site-packages/retained/tests",
            "lib/python3.12/site-packages/retained/nested/test",
        })

    def test_engine_version_drift_rejects_before_removing_files(self):
        self.distribution("unused-provider", ["unused_provider/__init__.py"])
        before = {str(p.relative_to(self.runtime)): p.read_bytes()
                  for p in self.runtime.rglob("*") if p.is_file()}

        for engine in self.versions:
            with self.subTest(engine=engine):
                versions = copy.copy(self.versions)
                versions[engine] = "999.0.0"
                with self.assertRaisesRegex(ValueError, "Re-audit bundle profile"):
                    prune_runtime(self.runtime, self.profile, versions)
                after = {str(p.relative_to(self.runtime)): p.read_bytes()
                         for p in self.runtime.rglob("*") if p.is_file()}
                self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
