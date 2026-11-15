#!/usr/bin/env python3
"""Publish an existing, checksum-verified DVT release; never rebuild an image."""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPOSITORY = "pamir-ai-pkgs/manifest"
REGION = "us-west-2"
ACCOUNT = "322551983552"
CHECK_URL = (
    "https://bf0agb75f8.execute-api.us-west-2.amazonaws.com/api/v1/updates/check"
)


def plan(tag, build_id, channel):
    match = re.fullmatch(r"rk3576-v(\d+\.\d+\.\d+)(-rc\.\d+)?", tag)
    if not match or not re.fullmatch(r"\d+-\d+", build_id):
        raise ValueError(
            "An exact release/RC tag and run-attempt build ID are required"
        )
    if channel not in ("dev", "sit") or (match[2] and channel != "dev"):
        raise ValueError("Only dev and SIT are supported; RC publication is dev-only")
    secure = channel == "sit"
    suffix = "-dvt-sec" if secure else "-dvt"
    prefix = (
        f"pamir-rk3576/candidates/{tag}{suffix}/{build_id}"
        if match[2]
        else f"pamir-rk3576/releases/{tag}{suffix}"
    )
    version = tag.removeprefix("rk3576-v")
    return {
        "version": version,
        "prefix": prefix,
        "bundle": f"lapis-dvt-{'sec' if secure else 'dev'}-v{version}.raucb",
        "compatible": "pamir-lapis-rk3576" + ("" if secure else "-dev"),
        "checksum_asset": "SHA256SUMS-dvt" + ("-secure" if secure else ""),
    }


# A prepared bundle is not a tag build's output (for example the v0.3.0 -> v0.4.0
# migration bundle, repacked for the v0.3.0 dev identity). It is staged under this
# prefix only, named by hash, and verified with the trust the receiving units have.
PREPARED_PREFIX = "s3://lapis-os-artifacts/pamir-rk3576/migration/"


def prepared_plan(bundle_uri, bundle_sha256, uboot_sha256, version, channel, trust_tag):
    if channel != "dev":
        raise ValueError("Prepared bundles are published to the dev channel only")
    if not re.fullmatch(r"rk3576-v\d+\.\d+\.\d+", trust_tag):
        raise ValueError("The trust tag must be an exact final release tag")
    if not re.fullmatch(r"\d+\.\d+\.\d+(-rc\.\d+)?", version):
        raise ValueError("Prepared version must be X.Y.Z or X.Y.Z-rc.N")
    for value in (bundle_sha256, uboot_sha256):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Prepared artifacts need exact SHA-256 digests")
    folder, _, name = bundle_uri.rpartition("/")
    if (not bundle_uri.startswith(PREPARED_PREFIX) or ".." in bundle_uri
            or not re.fullmatch(r"[A-Za-z0-9._-]+\.raucb", name)):
        raise ValueError("Prepared bundles must be staged under " + PREPARED_PREFIX)
    return {
        "version": version,
        "bundle_uri": bundle_uri,
        "uboot_uri": folder + "/uboot.img",
        "bundle": name,
        "compatible": "pamir-lapis-rk3576-dev",
        "sha256": {name: bundle_sha256, "uboot.img": uboot_sha256},
        "trust_tag": trust_tag,
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_checksum(path, sums, relative):
    entries = [line.split() for line in sums.splitlines() if line.strip()]
    matches = [e[0] for e in entries if len(e) == 2 and e[1] == relative]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{64}", matches[0]):
        raise ValueError(f"Missing or ambiguous checksum for {relative}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != matches[0]:
        raise ValueError(f"Checksum mismatch for {relative}")
    return digest.hexdigest()


def source_pins(xml):
    pins = {}
    for project in ET.fromstring(xml).findall("project"):
        name, revision = project.get("name"), project.get("revision", "")
        if name in pins or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Manifest must contain unique, commit-pinned projects")
        pins[name] = revision
    if not {"linux-rockchip-bsp-tools", "linux-rockchip-mkosi"} <= pins.keys():
        raise ValueError("Manifest is missing publisher or image trust source")
    return pins


def validate_offer(result, version, bundle_hash, bundle_size, uboot_hash, uboot_size):
    update = result.get("update") or {}
    expected = {"version": version, "sha256": bundle_hash, "size": bundle_size}
    if not result.get("update_available") or any(
        update.get(k) != v for k, v in expected.items()
    ):
        raise ValueError("Live offer does not match selected release bundle")
    boot = update.get("uboot") or {}
    if boot.get("sha256") != uboot_hash or boot.get("size") != uboot_size:
        raise ValueError("Live offer does not match selected U-Boot")


def validate_bundle(info, selected):
    if (
        info["version"] != "v" + selected["version"]
        or info["compatible"] != selected["compatible"]
    ):
        raise ValueError("Signed bundle version or image posture mismatch")


def output(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs)


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def github_file(repo, revision, path):
    value = json.loads(
        output("gh", "api", f"repos/{repo}/contents/{path}?ref={revision}")
    )
    return base64.b64decode(value["content"])


def build_publisher(work, revision):
    """Use the image's exact publisher and module dependency, including orphan SHAs."""
    source = work / "bsp-tools"
    run("git", "init", "-q", str(source))
    run(
        "git",
        "-C",
        str(source),
        "fetch",
        "--quiet",
        "--depth=1",
        "https://github.com/pamir-ai-pkgs/linux-rockchip-bsp-tools.git",
        revision,
    )
    run("git", "-C", str(source), "checkout", "--quiet", "--detach", "FETCH_HEAD")
    if output("git", "-C", str(source), "rev-parse", "HEAD").strip() != revision:
        raise ValueError("Publisher source revision mismatch")
    module = source / "ota-publish"
    dependency = re.search(
        r"github.com/Pamir-AI/lapis-ota-server\s+(v[^\s]+)",
        (module / "go.mod").read_text(),
    )
    if not dependency:
        raise ValueError("Publisher server schema dependency missing")
    ref = dependency[1].rsplit("-", 1)[-1] if "-" in dependency[1] else dependency[1]
    commit = json.loads(
        output("gh", "api", f"repos/Pamir-AI/lapis-ota-server/commits/{ref}")
    )["sha"]
    server = work / "ota-server"
    run("git", "init", "-q", str(server))
    run(
        "git",
        "-C",
        str(server),
        "fetch",
        "--quiet",
        "--depth=1",
        "https://github.com/Pamir-AI/lapis-ota-server.git",
        commit,
    )
    run("git", "-C", str(server), "checkout", "--quiet", "--detach", "FETCH_HEAD")
    if output("git", "-C", str(server), "rev-parse", "HEAD").strip() != commit:
        raise ValueError("Publisher schema revision mismatch")
    # Only the temporary build module changes; its dependency source stays pinned.
    run(
        "go",
        "mod",
        "edit",
        f"-replace=github.com/Pamir-AI/lapis-ota-server={server}",
        cwd=module,
    )
    run("go", "vet", "./...", cwd=module)
    run("go", "test", "./...", cwd=module)
    executable = work / "ota-publish"
    run("go", "build", "-trimpath", "-o", str(executable), ".", cwd=module)
    print(f"Publisher source {revision}; schema {commit}", flush=True)
    return executable


def check_query(channel, current):
    params = {
        "board": "lapis",
        "channel": channel,
        "current_version": current.split("-")[0],
        "machine_id": "ci-dvt-ota-publish-verifier",
    }
    # "0.0.0" is the server's "unknown version, offer everything" value for
    # current_version; as an exact version (current_version_full) it is refused.
    if current != "0.0.0":
        params["current_version_full"] = current
    return urllib.parse.urlencode(params)


def check_offer(work, token, public_key, channel, current):
    query = check_query(channel, current)
    request = urllib.request.Request(
        CHECK_URL + "?" + query, headers={"Authorization": "LapisOTA-v1 " + token}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    verify_manifest(work, result, public_key, channel)
    return result


def verify_manifest(work, result, public_key, channel):
    sig = result["signature"]
    if sig["alg"] != "ecdsa-p256-sha256-der":
        raise ValueError("Unexpected manifest signature algorithm")
    data, signature = work / "payload.json", work / "signature.der"
    data.write_text(
        json.dumps(
            {k: v for k, v in result.items() if k != "signature"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    signature.write_bytes(base64.b64decode(sig["value"]))
    run(
        "openssl",
        "dgst",
        "-sha256",
        "-verify",
        str(public_key),
        "-keyform",
        "DER",
        "-signature",
        str(signature),
        str(data),
        stdout=subprocess.DEVNULL,
    )
    if result["board"] != "lapis" or result["channel"] != channel:
        raise ValueError("Signed manifest target mismatch")


def publishing_identity():
    # Use native instance credentials; never restore the obsolete assume-role hop.
    os.environ.pop("AWS_PROFILE", None)
    os.environ.update(AWS_REGION=REGION, AWS_DEFAULT_REGION=REGION)
    identity = json.loads(
        output("aws", "sts", "get-caller-identity", "--output", "json")
    )
    if identity["Account"] != ACCOUNT or not identity["Arn"].startswith(
        f"arn:aws:sts::{ACCOUNT}:assumed-role/lapis-ota-build/"
    ):
        raise ValueError("Unexpected OTA publishing identity")


def publish(tag, build_id, channel, verify_only=False):
    selected = plan(tag, build_id, channel)
    publishing_identity()
    with tempfile.TemporaryDirectory(
        prefix="dvt-ota-", dir=os.environ.get("RUNNER_TEMP")
    ) as tmp:
        work = Path(tmp)
        tag_xml = github_file(REPOSITORY, tag, "rk3576-debian-ab.xml")
        pins = source_pins(tag_xml)
        run(
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            REPOSITORY,
            "--dir",
            str(work),
            "--pattern",
            selected["checksum_asset"],
            "--pattern",
            "manifest.xml",
        )
        if source_pins((work / "manifest.xml").read_bytes()) != pins:
            raise ValueError("Release manifest does not match its source tag")
        sums = (work / selected["checksum_asset"]).read_text()
        hashes = {}
        for name in [selected["bundle"], "uboot.img"]:
            run(
                "aws",
                "s3",
                "cp",
                f"s3://lapis-os-artifacts/{selected['prefix']}/IMAGES/{name}",
                str(work / name),
                "--only-show-errors",
            )
            hashes[name] = verify_checksum(work / name, sums, "IMAGES/" + name)
        bundle, uboot = work / selected["bundle"], work / "uboot.img"
        ca = work / "ca.pem"
        ca.write_bytes(
            github_file(
                "pamir-ai-pkgs/linux-rockchip-mkosi",
                pins["linux-rockchip-mkosi"],
                "mkosi.extra/etc/rauc/ca.cert.pem",
            )
        )
        info = json.loads(
            output(
                "rauc",
                "info",
                "--keyring=" + str(ca),
                "--output-format=json",
                str(bundle),
            )
        )
        validate_bundle(info, selected)
        register(work, selected, channel, bundle, uboot, hashes, pins, verify_only)


def publish_prepared(selected, channel, verify_only=False):
    publishing_identity()
    with tempfile.TemporaryDirectory(
        prefix="dvt-ota-", dir=os.environ.get("RUNNER_TEMP")
    ) as tmp:
        work = Path(tmp)
        # The receiving units' trust: their image's RAUC CA and manifest key, and the
        # publisher their release used, all from that release tag's pins.
        pins = source_pins(github_file(REPOSITORY, selected["trust_tag"], "rk3576-debian-ab.xml"))
        hashes = {}
        for name, uri in [(selected["bundle"], selected["bundle_uri"]), ("uboot.img", selected["uboot_uri"])]:
            run("aws", "s3", "cp", uri, str(work / name), "--only-show-errors")
            hashes[name] = sha256_file(work / name)
            if hashes[name] != selected["sha256"][name]:
                raise ValueError(f"Checksum mismatch for {name}")
        bundle, uboot = work / selected["bundle"], work / "uboot.img"
        ca = work / "ca.pem"
        ca.write_bytes(
            github_file(
                "pamir-ai-pkgs/linux-rockchip-mkosi",
                pins["linux-rockchip-mkosi"],
                "mkosi.extra/etc/rauc/ca.cert.pem",
            )
        )
        info = json.loads(
            output("rauc", "info", "--keyring=" + str(ca), "--output-format=json", str(bundle))
        )
        validate_bundle(info, selected)
        print(f"Prepared bundle verified against {selected['trust_tag']}'s RAUC CA: "
              f"{info['compatible']} {info['version']}", flush=True)
        register(work, selected, channel, bundle, uboot, hashes, pins, verify_only)


def register(work, selected, channel, bundle, uboot, hashes, pins, verify_only):
    executable = build_publisher(work, pins["linux-rockchip-bsp-tools"])
    if verify_only:
        print(
            "Artifacts, signature and publisher verified; no OTA publication requested."
        )
        return
    token_text = output(
        "aws",
        "secretsmanager",
        "get-secret-value",
        "--secret-id",
        "lapis-ota/fleet-tokens",
        "--query",
        "SecretString",
        "--output",
        "text",
    )
    token = next(
        line.strip() for line in reversed(token_text.splitlines()) if line.strip()
    )
    key = work / "manifest-key.der"
    key.write_bytes(
        base64.b64decode(
            github_file(
                "pamir-ai-pkgs/linux-rockchip-mkosi",
                pins["linux-rockchip-mkosi"],
                "mkosi.extra/etc/lapis-ota/manifest.p256.pub",
            ).strip()
        )
    )
    version = selected["version"]

    def check(current):
        return check_offer(work, token, key, channel, current)

    def validate(result):
        validate_offer(
            result,
            version,
            hashes[bundle.name],
            bundle.stat().st_size,
            hashes[uboot.name],
            uboot.stat().st_size,
        )

    before = check("0.0.0")
    if before.get("update_available") and before["update"]["version"] == version:
        validate(before)
        print("Matching release already live; skipping duplicate publication.")
    else:
        if check(version).get("update_available"):
            raise ValueError(
                "A newer release is already offered; refusing an older publication"
            )
        run(
            str(executable),
            "publish",
            "--bundle",
            str(bundle),
            "--uboot",
            str(uboot),
            "--board",
            "lapis",
            "--channel",
            channel,
            "--version",
            version,
            "--region",
            REGION,
            "--bucket",
            "pamir-ota-bundles",
            "--table",
            "lapis-ota-releases",
        )
    validate(check("0.0.0"))
    if check(version).get("update_available"):
        raise ValueError("Installed release is still offered an update")
    print(
        "OTA_PUBLISH_VERIFIED "
        + json.dumps(
            {
                "board": "lapis",
                "channel": channel,
                "version": version,
                "bundle_sha256": hashes[bundle.name],
                "uboot_sha256": hashes[uboot.name],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--build-id", help="Original GitHub run ID-attempt")
    parser.add_argument("--channel", required=True, choices=["dev", "sit"])
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--prepared-bundle", help="S3 URI of a prepared bundle under " + PREPARED_PREFIX)
    parser.add_argument("--prepared-bundle-sha256")
    parser.add_argument("--prepared-uboot-sha256")
    parser.add_argument("--prepared-version")
    parser.add_argument("--trust-tag", default="rk3576-v0.3.0",
                        help="Release whose units receive the prepared bundle")
    args = parser.parse_args()
    if args.prepared_bundle:
        publish_prepared(prepared_plan(args.prepared_bundle, args.prepared_bundle_sha256 or "",
                                       args.prepared_uboot_sha256 or "", args.prepared_version or "",
                                       args.channel, args.trust_tag),
                         args.channel, args.verify_only)
    else:
        if not args.tag or not args.build_id:
            parser.error("--tag and --build-id are required without --prepared-bundle")
        publish(args.tag, args.build_id, args.channel, args.verify_only)
