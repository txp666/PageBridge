"""Publish verified releases to Tencent COS and retain only the newest version."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "pagebridge-downloads-1257631357"
REGION = "ap-hongkong"
PREFIX = "PageBridge/ota"
PUBLIC = f"https://{BUCKET}.cos.{REGION}.myqcloud.com/{PREFIX}"
VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
LEGACY = {"legacy-appcast.xml": "appcast.xml", "legacy-windows-update.json": "windows-update.json"}
spec = importlib.util.spec_from_file_location("verify_release", Path(__file__).with_name("verify-release.py"))
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def version_key(tag):
    match = VERSION.fullmatch(tag)
    if not match:
        raise ValueError("Invalid COS release tag")
    return tuple(map(int, match.groups()))


def missing(error):
    return getattr(error, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound")


def current(cos):
    try:
        response = cos.get_object(Bucket=BUCKET, Key=f"{PREFIX}/latest.json")
    except Exception as error:
        if missing(error):
            return None
        raise
    with response["Body"] as body:
        return json.load(body)


def prepare(dist, report, config):
    version, build = report["version"], report["build"]
    tag = "v" + version
    version_key(tag)
    if str(build) != str(int(build)) or config["feedURL"] != PUBLIC + "/appcast.xml":
        raise ValueError("Invalid COS release identity or feed")
    rows = {item["name"]: item for item in report["assets"]}
    if set(rows) != set(verify.asset_names(version)):
        raise ValueError("Expected the complete verified release")
    verify.verify_cos_feeds(dist, config, report)
    files = {}
    for name in verify.asset_names(version):
        path = dist / "cos" / name if name in ("appcast.xml", "windows-update.json") else dist / name
        item = dict(name=name, bytes=path.stat().st_size, sha256=verify.digest(path))
        if path.parent == dist and item != rows[name]:
            raise ValueError("Release file differs from verified copy: " + name)
        files[name] = path, item
    for name, source in LEGACY.items():
        path = dist / source
        if verify.digest(path) != rows[source]["sha256"] or path.stat().st_size != rows[source]["bytes"]:
            raise ValueError("Compatibility feed differs from verified copy")
        files[name] = path, dict(name=name, bytes=path.stat().st_size, sha256=verify.digest(path))
    return tag, files


def forbid_overwrite(request, **_):
    # COS's native create-only header remains effective with S3-compatible signing.
    # COS ignores this protection when bucket versioning is enabled; main rejects it.
    request.headers["x-cos-forbid-overwrite"] = "true"


def put_release_file(cos, tag, path, item):
    key = f"{PREFIX}/releases/{tag}/{item['name']}"
    try:
        existing = cos.head_object(Bucket=BUCKET, Key=key)
    except Exception as error:
        if not missing(error):
            raise
    else:
        if existing["ContentLength"] != item["bytes"]:
            raise ValueError("Existing COS object differs: " + item["name"])
        verify.verify_public_asset(f"{PUBLIC}/releases/{tag}/{item['name']}", item)
        return
    media = {".zip": "application/zip", ".exe": "application/octet-stream", ".json": "application/json",
             ".xml": "application/rss+xml", ".sha256": "text/plain"}
    with path.open("rb") as stream:
        md5 = base64.b64encode(hashlib.file_digest(stream, "md5").digest()).decode()
    try:
        with path.open("rb") as body:
            cos.put_object(Bucket=BUCKET, Key=key, Body=body, ContentLength=item["bytes"], ACL="public-read",
                ContentType=media[path.suffix], CacheControl="public, max-age=86400", ContentMD5=md5,
                ContentDisposition=f'attachment; filename="{item["name"]}"' if path.suffix in (".zip", ".exe") else "inline")
    except Exception as error:
        if getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") not in (409, 412):
            raise
    verify.verify_public_asset(f"{PUBLIC}/releases/{tag}/{item['name']}", item)


def stage(cos, dist, report, config, pointer):
    tag, files = prepare(dist, report, config)
    previous = current(cos)
    if previous and (previous["build"] >= int(report["build"]) or version_key("v" + previous["version"]) >= version_key(tag)):
        raise ValueError("COS already serves this or a newer release")
    def upload(path, item):
        print("Staging", tag, item["name"], flush=True)
        put_release_file(cos, tag, path, item)
        print("Verified", tag, item["name"], flush=True)
    # Independent immutable objects can transfer together. Register the shared
    # client's header once, before starting threads, rather than mutating hooks
    # while another upload is being signed.
    cos.meta.events.register("before-sign.s3.PutObject", forbid_overwrite)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks = [pool.submit(upload, path, item) for path, item in files.values()]
            for task in as_completed(tasks):
                task.result()
    finally:
        cos.meta.events.unregister("before-sign.s3.PutObject", forbid_overwrite)
    public_item = lambda item: {key: item[key] for key in ("name", "bytes", "sha256")}
    release = dict(version=tag[1:], build=int(report["build"]),
        assets=[public_item(item) for name, (_, item) in files.items() if name not in LEGACY],
        compatibility=[public_item(files[name][1]) for name in LEGACY])
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps(release) + "\n")


def prune(cos, tag):
    prefix = f"{PREFIX}/releases/"
    removed = 0
    for page in cos.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].startswith(prefix):
                continue
            old_tag, _, name = obj["Key"][len(prefix):].partition("/")
            if VERSION.fullmatch(old_tag) and version_key(old_tag) < version_key(tag):
                if name in (*verify.asset_names(old_tag[1:]), *LEGACY):
                    cos.delete_object(Bucket=BUCKET, Key=obj["Key"])
                    removed += 1
    return removed


def activate(cos, release):
    tag = "v" + release["version"]
    version_key(tag)
    if {item["name"] for item in release["assets"]} != set(verify.asset_names(tag[1:])):
        raise ValueError("Incomplete COS release pointer")
    if {item["name"] for item in release["compatibility"]} != set(LEGACY):
        raise ValueError("Incomplete compatibility feeds")
    previous = current(cos)
    if previous != release and previous and (previous["build"] >= release["build"] or
            version_key("v" + previous["version"]) >= version_key(tag)):
        raise ValueError("Refusing to replace this or a newer COS release")
    # The release workflow serializes writers. Publish each authenticated feed
    # before the website pointer, and keep old packages until every readback passes.
    for item in release["assets"] + release["compatibility"]:
        if item["name"] not in ("appcast.xml", "windows-update.json", *LEGACY):
            continue
        raw = verify.fetch(f"{PUBLIC}/releases/{tag}/{item['name']}", limit=64 * 1024)
        if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Staged COS update feed differs")
        cos.put_object(Bucket=BUCKET, Key=f"{PREFIX}/{item['name']}", Body=raw, ACL="public-read",
            ContentType="application/rss+xml" if item["name"].endswith(".xml") else "application/json", CacheControl="no-store")
        if verify.fetch(f"{PUBLIC}/{item['name']}", limit=64 * 1024) != raw:
            raise ValueError("Public COS feed differs")
    cos.put_object(Bucket=BUCKET, Key=f"{PREFIX}/latest.json", Body=json.dumps(release).encode(),
        ACL="public-read", ContentType="application/json", CacheControl="no-store")
    if json.loads(verify.fetch(PUBLIC + "/latest.json")) != release or current(cos) != release:
        raise ValueError("Public COS release pointer differs")
    removed = prune(cos, tag)
    print(f"Tencent COS now serves {tag}; removed {removed} earlier release objects.")


def probe(cos):
    key = f"{PREFIX}/_probe/{uuid.uuid4().hex}.zip"
    raw = b"PageBridge COS release validation\n" * 4
    created = False
    cos.meta.events.register("before-sign.s3.PutObject", forbid_overwrite)
    try:
        cos.put_object(Bucket=BUCKET, Key=key, Body=raw, ACL="public-read", CacheControl="no-store")
        created = True
        try:
            cos.put_object(Bucket=BUCKET, Key=key, Body=b"replacement", ACL="public-read")
        except Exception as error:
            if getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") not in (409, 412):
                raise
        else:
            raise ValueError("COS create-only protection did not reject an overwrite")
        item = dict(name="probe.zip", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        verify.verify_public_asset(f"https://{BUCKET}.cos.{REGION}.myqcloud.com/{key}", item)
        print(json.dumps(dict(status="passed", bucket=BUCKET, region=REGION,
            createOnlyVerified=True, publicSHA256Verified=True, httpRangeVerified=True)))
    finally:
        cos.meta.events.unregister("before-sign.s3.PutObject", forbid_overwrite)
        if created:
            cos.delete_object(Bucket=BUCKET, Key=key)


if __name__ == "__main__":
    from botocore.config import Config
    import boto3
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "activate", "probe"))
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--report", type=Path, default=ROOT / "validation/release/assets.json")
    parser.add_argument("--pointer", type=Path, default=ROOT / "validation/release/cos-pointer.json")
    args = parser.parse_args()
    if not all(os.environ.get(k) for k in ("TENCENT_CLOUD_SECRET_ID", "TENCENT_CLOUD_SECRET_KEY")):
        parser.error("Configure TENCENT_CLOUD_SECRET_ID and TENCENT_CLOUD_SECRET_KEY in GitHub Actions")
    client = boto3.client("s3", endpoint_url=f"https://cos.{REGION}.myqcloud.com", region_name=REGION,
        aws_access_key_id=os.environ["TENCENT_CLOUD_SECRET_ID"], aws_secret_access_key=os.environ["TENCENT_CLOUD_SECRET_KEY"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"},
            request_checksum_calculation="when_required", response_checksum_validation="when_required"))
    if client.get_bucket_versioning(Bucket=BUCKET).get("Status") == "Enabled":
        raise ValueError("COS bucket versioning must be disabled for create-only release objects")
    if args.action == "stage":
        stage(client, args.dist, json.loads(args.report.read_bytes()), json.loads((ROOT / "update-config.json").read_bytes()), args.pointer)
    elif args.action == "activate":
        activate(client, json.loads(args.pointer.read_bytes()))
    else:
        client.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX + "/", MaxKeys=1)
        probe(client)
