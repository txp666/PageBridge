#!/usr/bin/env python3
"""Fail-closed identity and draft-publishing steps used by release CI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def output(command):
    return subprocess.check_output(command, text=True, cwd=ROOT).strip()


def run(command):
    subprocess.run(command, check=True, cwd=ROOT)


def release_lookup(tag, repository):
    # The tags REST endpoint omits drafts. gh discovers the release-ID URL.
    result = subprocess.run(["gh", "release", "view", tag, "--repo", repository, "--json", "apiUrl"],
                            capture_output=True, text=True, cwd=ROOT)
    if result.returncode:
        require("release not found" in result.stderr.lower(), "Release lookup failed: " + result.stderr)
        return None, None
    url = json.loads(result.stdout)["apiUrl"]
    require(url.startswith("https://api.github.com/repos/" + repository + "/releases/"), "Unexpected release metadata URL")
    return url, json.loads(output(["gh", "api", url]))


def require_owned_draft(metadata, tag, commit):
    marker = "<!-- pagebridge-source:" + commit + " -->"
    require(metadata["draft"] and not metadata["prerelease"], "Refusing to overwrite a published or prerelease release")
    require(metadata["tag_name"] == tag and metadata["target_commitish"] == commit and marker in (metadata.get("body") or ""),
            "Draft does not belong to this source commit")


def identity():
    release = json.loads((ROOT / "VERSION.json").read_text())
    config = json.loads((ROOT / "update-config.json").read_text())
    version, build = release["version"], str(release["build"])
    require(re.fullmatch(r"\d+\.\d+\.\d+", version) and build.isdecimal() and int(build) > 0, "Invalid release version/build")
    tag = os.environ["RELEASE_REF"].removeprefix("refs/tags/")
    require(re.fullmatch(r"v\d+\.\d+\.\d+", tag) and tag == "v" + version, "Tag must exactly match VERSION.json")
    repository = config["repository"]
    require(repository == os.environ["GITHUB_REPOSITORY"] == "txp666/PageBridge", "Unexpected release repository")
    commit = output(["git", "rev-parse", "HEAD"])
    require(output(["git", "rev-parse", "refs/tags/" + tag + "^{commit}"]) == commit, "Checkout differs from version tag")
    workflow = os.environ["WORKFLOW_COMMIT"]
    require(re.fullmatch(r"[a-f0-9]{40}", workflow) is not None, "Invalid workflow commit")
    # A dispatch may use newer automation, but not silently different publishing
    # instructions than the workflow reviewed in the release source.
    actual_workflow = subprocess.check_output(["git", "show", workflow + ":.github/workflows/release.yml"], cwd=ROOT)
    require(actual_workflow == (ROOT / ".github/workflows/release.yml").read_bytes(), "Workflow differs from tagged release workflow")
    refs = output(["git", "ls-remote", "origin", "refs/tags/" + tag, "refs/tags/" + tag + "^{}"])
    mapping = {line.split()[1]: line.split()[0] for line in refs.splitlines()}
    require(mapping.get("refs/tags/" + tag + "^{}", mapping.get("refs/tags/" + tag)) == commit, "Remote tag changed")
    _, metadata = release_lookup(tag, repository)
    if metadata is not None:
        require_owned_draft(metadata, tag, commit)
    return dict(tag=tag, version=version, build=build, sourceCommit=commit, workflowCommit=workflow,
                event=os.environ.get("GITHUB_EVENT_NAME", ""), repository=repository)


def verify_upload(metadata, report):
    actual = {item["name"]: item for item in metadata["assets"]}
    require(len(actual) == len(metadata["assets"]) and set(actual) == {item["name"] for item in report["assets"]},
            "Uploaded asset inventory differs")
    for expected in report["assets"]:
        item = actual[expected["name"]]
        require(item["state"] == "uploaded" and item["size"] == expected["bytes"], "Uploaded asset size/state differs")
        require(item.get("digest") == "sha256:" + expected["sha256"], "Uploaded asset SHA-256 differs")


def publish(report_path, notes_path):
    verified = identity()  # Re-check tag, workflow and draft immediately before mutation.
    tag, version, commit, repository = (verified[key] for key in ("tag", "version", "sourceCommit", "repository"))
    report = json.loads(report_path.read_text())
    require(report["version"] == version and str(report["build"]) == verified["build"] and report["repository"] == repository,
            "Verified release identity differs")
    require(report.get("feedSignatureVerified") and report.get("archiveSignatureVerified"), "OTA signature validation is missing")
    require(report.get("windowsUpdateSignatureVerified") is True, "Windows OTA signature validation is missing")
    # Deliberately do not import the cryptography verifier here; the CI publication
    # step consumes an already verified report and independently binds every file.
    names = [f"PageBridge-{version}-{kind}.zip" for kind in ("macOS-arm64", "windows-x64", "source", "third-party-sources")]
    names.append(f"PageBridge-{version}-windows-x64-setup.exe")
    expected_names = set(names + [name + ".sha256" for name in names] + ["appcast.xml", "windows-update.json"])
    require(len(report["assets"]) == 12 and {item["name"] for item in report["assets"]} == expected_names,
            "Both platforms and all twelve release assets must pass verification")
    require(report.get("windows", {}).get("status") == "passed", "Windows archive validation is missing")
    require(report.get("windowsInstaller", {}).get("status") == "passed", "Windows installer validation is missing")
    assets = []
    for item in report["assets"]:
        path = ROOT / "dist" / item["name"]
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        require(path.stat().st_size == item["bytes"] and digest == item["sha256"], "Asset changed after verification")
        assets.append(str(path))
    marker = "<!-- pagebridge-source:" + commit + " -->"
    note = notes_path.read_text().rstrip() + "\n\n" + marker + "\n"
    # Keep both identities in the public audit marker; only the source marker is
    # used to resume an owned draft after an interrupted upload.
    note += "<!-- pagebridge-workflow:" + verified["workflowCommit"] + " -->\n"
    notes_path.write_text(note)
    url, metadata = release_lookup(tag, repository)
    if metadata is None:
        run(["gh", "release", "create", tag, "--repo", repository, "--verify-tag", "--draft", "--target", commit,
             "--title", "PageBridge " + version, "--notes-file", str(notes_path), *assets])
        url, metadata = release_lookup(tag, repository)
    else:
        require_owned_draft(metadata, tag, commit)
        run(["gh", "release", "upload", tag, "--repo", repository, "--clobber", *assets])
        run(["gh", "release", "edit", tag, "--repo", repository, "--title", "PageBridge " + version, "--notes-file", str(notes_path)])
    # Any error up to this point leaves the release unpublished, including extra
    # stale assets in a resumed draft. Never silently delete unexpected assets.
    metadata = json.loads(output(["gh", "api", url]))
    require_owned_draft(metadata, tag, commit)
    verify_upload(metadata, report)
    # A final tag read closes the long upload window before making it public.
    identity()
    run(["gh", "release", "edit", tag, "--repo", repository, "--draft=false", "--latest"])


def requirements(source_root):
    packages = {item["name"]: item for item in tomllib.loads((source_root / "engine/uv.lock").read_text())["package"]}
    lines = []
    for name in ("cryptography", "cffi", "pycparser"):
        package = packages[name]
        hashes = sorted({wheel["hash"] for wheel in package["wheels"]})
        require(hashes and all(re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in hashes), "Missing pinned verification dependency hashes")
        lines.append(name + "==" + package["version"] + " " + " ".join("--hash=" + value for value in hashes))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("identity", "publish", "requirements"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path, default=ROOT / "validation/release/assets.json")
    parser.add_argument("--notes", type=Path, default=ROOT / "validation/release/notes.md")
    args = parser.parse_args()
    if args.action == "publish":
        publish(args.report, args.notes)
        return
    if args.action == "requirements":
        result = requirements(args.source_root)
    else:
        checked = identity()
        result = json.dumps(checked, indent=2) + "\n"
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                for key in ("tag", "version", "build", "sourceCommit", "workflowCommit"):
                    stream.write(f"{key}={checked[key]}\n")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
    print(result, end="")


if __name__ == "__main__":
    main()
