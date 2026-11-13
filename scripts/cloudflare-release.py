"""Stage a verified release in R2, atomically switch the site and OTA, then prune older releases."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path

from botocore.config import Config
from botocore.exceptions import ClientError
import boto3

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "pagebridge-downloads"
PUBLIC = "https://pagebridge.modusignal.cn/download"
VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
spec = importlib.util.spec_from_file_location("verify_release", Path(__file__).with_name("verify-release.py"))
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def version_key(tag):
    return tuple(map(int, VERSION.fullmatch(tag).groups()))


def current(s3):
    try:
        response = s3.get_object(Bucket=BUCKET, Key="latest.json")
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None, None
        raise
    with response["Body"] as body:
        return json.load(body), response["ETag"]


def prepare(dist, report, config):
    version = report["version"]
    tag = "v" + version
    if not VERSION.fullmatch(tag) or str(report["build"]) != str(int(report["build"])):
        raise ValueError("Invalid release identity")
    files = {}
    rows = {item["name"]: item for item in report["assets"]}
    if set(rows) != set(verify.asset_names(version)):
        raise ValueError("Expected the complete verified release")
    for name in verify.asset_names(version):
        path = dist / name
        if name == "windows-update.json" and config["feedURL"] == PUBLIC + "/appcast.xml":
            path = dist / "cloudflare" / name
            installer = dist / f"PageBridge-{version}-windows-x64-setup.exe"
            signed = verify.helper("windows-update.py").verify_installer_binding(
                path.read_bytes(), config["publicEDKey"], report, installer)
            if signed["url"] != f"{PUBLIC}/ota/{tag}/{installer.name}":
                raise ValueError("Windows Cloudflare manifest points to a different installer")
        checksum = verify.digest(path)
        size = path.stat().st_size
        if path.parent == dist and (checksum != rows[name]["sha256"] or size != rows[name]["bytes"]):
            raise ValueError("Release file differs from the verified copy: " + name)
        files[name] = (path, dict(name=name, bytes=size, sha256=checksum))
    return tag, files


def put_release_file(s3, tag, path, item):
    name = item["name"]
    media = {".zip": "application/zip", ".exe": "application/octet-stream", ".json": "application/json",
             ".xml": "application/rss+xml", ".sha256": "text/plain"}
    try:
        with path.open("rb") as body:
            s3.put_object(Bucket=BUCKET, Key=f"{tag}/{name}", Body=body, ContentLength=item["bytes"],
                          ContentType=media[path.suffix], ContentDisposition=f'attachment; filename="{name}"'
                          if path.suffix in (".zip", ".exe") else "inline",
                          CacheControl="public, max-age=86400", IfNoneMatch="*",
                          ContentMD5=file_md5(path))
    except ClientError as exc:
        if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
            raise
    # Also verifies saved bytes after a retry and Range for installers and archives.
    verify.verify_public_asset(f"{PUBLIC}/{tag}/{name}", item)


def file_md5(path):
    with path.open("rb") as stream:
        return base64.b64encode(hashlib.file_digest(stream, "md5").digest()).decode()


def prune(s3, tag):
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            prefix, _, name = key.partition("/")
            if (VERSION.fullmatch(prefix) and version_key(prefix) < version_key(tag)
                    and name in verify.asset_names(prefix[1:])):
                s3.delete_object(Bucket=BUCKET, Key=key)


def stage(s3, dist, report, config, pointer):
    tag, files = prepare(dist, report, config)
    build = int(report["build"])
    previous, _ = current(s3)
    if previous and (previous["build"] >= build or version_key("v" + previous["version"]) >= version_key(tag)):
        raise ValueError("Cloudflare already serves this or a newer release")
    for path, item in files.values():
        print("Staging", tag, item["name"], flush=True)
        put_release_file(s3, tag, path, item)
        if item['name'] in (f"PageBridge-{tag[1:]}-macOS-arm64.zip", f"PageBridge-{tag[1:]}-windows-x64-setup.exe"):
            # The signed OTA URLs and the website point to the same R2 bytes.
            first = verify.fetch(f"{PUBLIC}/ota/{tag}/{item['name']}", headers={"Range": "bytes=0-31"}, limit=32)
            with path.open('rb') as stream:
                if first != stream.read(32):
                    raise ValueError("OTA download route differs: " + item['name'])
    release = dict(version=tag[1:], build=build, assets=[{k: item[k] for k in ("name", "bytes", "sha256")}
                   for _, item in files.values()])
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps(release) + "\n")


def activate(s3, release):
    tag = "v" + release["version"]
    previous, etag = current(s3)
    if previous != release:
        if previous and (previous["build"] >= release["build"] or version_key("v" + previous["version"]) >= version_key(tag)):
            raise ValueError("Refusing to replace this or a newer Cloudflare release")
        condition = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        s3.put_object(Bucket=BUCKET, Key="latest.json", Body=json.dumps(release).encode(),
                      ContentType="application/json", CacheControl="no-store", **condition)
    if json.loads(verify.fetch(PUBLIC + "/latest.json")) != release:
        raise ValueError("Public release pointer differs")
    for item in release["assets"]:
        if item["name"] in ("appcast.xml", "windows-update.json"):
            if hashlib.sha256(verify.fetch(PUBLIC + "/" + item["name"])).hexdigest() != item["sha256"]:
                raise ValueError("Public update feed differs: " + item["name"])
    prune(s3, tag)
    print(f"Cloudflare now serves {tag}; earlier release objects removed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "activate"))
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--report", type=Path, default=ROOT / "validation/release/assets.json")
    parser.add_argument("--pointer", type=Path, default=ROOT / "validation/release/r2-pointer.json")
    args = parser.parse_args()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not re.fullmatch(r"[0-9a-f]{32}", account) or not all(os.environ.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")):
        parser.error("Configure CLOUDFLARE_ACCOUNT_ID, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY in GitHub Actions")
    client = boto3.client("s3", endpoint_url=f"https://{account}.r2.cloudflarestorage.com", region_name="auto",
                          config=Config(request_checksum_calculation="when_required", response_checksum_validation="when_required"))
    if args.action == "stage":
        stage(client, args.dist, json.loads(args.report.read_bytes()), json.loads((ROOT / "update-config.json").read_bytes()), args.pointer)
    else:
        activate(client, json.loads(args.pointer.read_bytes()))
