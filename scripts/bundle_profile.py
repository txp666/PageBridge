"""Apply an audited native-worker profile only to an unsigned staging runtime.

The development environment and upstream sources remain complete. Distribution
RECORDs identify owned files; runtime data and licenses of retained packages are
never filtered by generic names such as 'data', 'testing', or file extensions.
"""
from importlib import metadata
import os
from pathlib import Path
import re
import shutil


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def footprint(root):
    files = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()]
    return {"fileBytes": sum(p.stat().st_size for p in files),
            "allocatedBytes": sum(p.stat().st_blocks * 512 for p in files),
            "fileCount": len(files)}


def prune_runtime(runtime, profile, engine_versions):
    runtime = runtime.resolve()
    if (tuple(runtime.parts[-4:]) != ("Contents", "Resources", "engine", "runtime")
            or not runtime.parents[3].name.startswith(".")
            or not runtime.parents[3].name.endswith("-staging.app")):
        raise ValueError("Only a staged private engine/runtime can be pruned.")
    for name, version in profile["auditedEngineVersions"].items():
        if engine_versions.get(name) != version:
            raise ValueError("Re-audit bundle profile after changing " + name)
    packages = runtime / "lib/python3.12/site-packages"
    before = footprint(runtime)
    removed = []

    def remove(path, reason):
        path = Path(path)
        # RECORD entries can refer to scripts outside site-packages, but must
        # never reach outside this staging runtime, even via a symlink.
        if not path.resolve().is_relative_to(runtime):
            raise ValueError("Bundle profile attempted to leave staging runtime")
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink():
            size = 0
            path.unlink()
        elif path.is_dir():
            size = footprint(path)["fileBytes"]
            shutil.rmtree(path)
        else:
            size = path.stat().st_size
            path.unlink()
        removed.append({"path": str(path.relative_to(runtime)), "fileBytes": size, "reason": reason})

    excluded = {canonical(name): reason for reason, names in profile["excludedDistributions"].items() for name in names}
    distributions = list(metadata.distributions(path=[str(packages)]))
    removed_distributions = []
    distribution_paths = []
    for distribution in distributions:
        name = canonical(distribution.metadata["Name"])
        if name not in excluded:
            continue
        files = distribution.files
        if files is None:
            raise ValueError("Missing RECORD for excluded distribution: " + name)
        removed_distributions.append({"name": name, "version": distribution.version, "reason": excluded[name]})
        # Materialize before deleting metadata/RECORD itself.
        paths = [Path(distribution.locate_file(file)) for file in files]
        for path in paths:
            if not path.resolve().is_relative_to(runtime):
                raise ValueError("Distribution RECORD leaves staging runtime")
            if path.is_dir():
                raise ValueError("Distribution RECORD must contain files, not directories")
            distribution_paths.append((path, excluded[name]))

    # Validate all distribution receipts before deleting any of their files.
    for path, reason in distribution_paths:
        remove(path, reason)

    for name in profile["testDirectoryPackages"]:
        root = packages / name
        if not root.is_dir():
            continue
        for directory, dirs, _ in os.walk(root, followlinks=False):
            for name in list(dirs):
                if name in ("test", "tests"):
                    remove(Path(directory) / name, "package-test-data")
                    dirs.remove(name)

    for path in profile["runtimeDevelopmentPaths"]:
        remove(runtime / path, "runtime-development-resource")
    for pattern in profile["runtimeDevelopmentGlobs"]:
        for path in sorted(runtime.glob(pattern)):
            remove(path, "runtime-development-resource")

    # Remove empty namespace/package folders after their owned files are gone.
    for directory, _, _ in os.walk(runtime, topdown=False, followlinks=False):
        folder = Path(directory)
        if folder != runtime and not folder.is_symlink() and not any(folder.iterdir()):
            folder.rmdir()
    after = footprint(runtime)
    return {"profile": profile["profile"], "before": before, "after": after,
            "removedFileBytes": before["fileBytes"] - after["fileBytes"],
            "removedDistributions": sorted(removed_distributions, key=lambda item: item["name"]),
            "removedPaths": removed,
            "retainedDistributions": sorted({canonical(d.metadata["Name"]): d.version for d in metadata.distributions(path=[str(packages)])}.items())}
