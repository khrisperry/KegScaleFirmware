#!/usr/bin/env python3
"""Validate Keg Scale OTA artifacts, signatures, release metadata, and docs.

Examples:
  python tools/release_check.py
  python tools/release_check.py --channel dev --version V1.4.5 --device scale --source-check
  python tools/release_check.py --channel production --version V1.4.5 --device scale --require-promotion
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
from urllib.parse import urlparse

from ota_signing import load_public_metadata, verify_manifest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "release_contract.json"

ERRORS = []
WARNINGS = []
PASSES = []


def fail(message):
    ERRORS.append(message)
    print(f"FAIL: {message}")


def warn(message):
    WARNINGS.append(message)
    print(f"WARN: {message}")


def passed(message):
    PASSES.append(message)
    print(f"PASS: {message}")


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing file: {path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON {path}: {exc}")
    return None


def image_identity(data):
    if len(data) < 112 or data[0] != 0xE9:
        raise ValueError("not an ESP-IDF application image")
    chip = struct.unpack_from("<H", data, 12)[0]
    if struct.unpack_from("<I", data, 32)[0] != 0xABCD5432:
        raise ValueError("missing ESP-IDF app descriptor")
    version = data[48:80].split(b"\0")[0].decode("utf-8")
    app = data[80:112].split(b"\0")[0].decode("utf-8")
    return chip, version, app


def expected_branch(channel):
    if channel == "production":
        return "main"
    if channel == "beta":
        return "beta"
    return "dev"


def expected_prefix(spec, channel):
    return (
        "https://raw.githubusercontent.com/"
        "khrisperry/KegScaleFirmware/main/"
        f"{spec['family']}/{channel}/{spec['target']}/"
    )


def verify_signature(manifest_path, signature_path, public_key_path):
    try:
        ok = verify_manifest(manifest_path, signature_path, public_key_path)
    except SystemExit as exc:
        fail(f"{manifest_path}: signature metadata invalid: {exc}")
        return False
    except Exception as exc:
        fail(f"{manifest_path}: signature verification raised {exc!r}")
        return False
    if not ok:
        fail(f"{manifest_path}: signature verification failed")
        return False
    return True


def validate_manifest(device, channel, spec, contract, expected_version=None):
    directory = ROOT / spec["family"] / channel / spec["target"]
    manifest_path = directory / "manifest.json"
    signature_path = directory / "manifest.sig"
    public_key_path = ROOT / "keys" / contract["channels"][channel]["signing_key"]

    manifest = load_json(manifest_path)
    signature = load_json(signature_path)
    public_key = load_json(public_key_path)
    if manifest is None or signature is None or public_key is None:
        return None

    if verify_signature(manifest_path, signature_path, public_key_path):
        passed(f"{device}/{channel}: manifest signature valid")

    if signature.get("channel") != channel:
        fail(
            f"{device}/{channel}: signature channel={signature.get('channel')!r}, "
            f"expected {channel!r}"
        )
    if signature.get("key_id") != public_key.get("key_id"):
        fail(
            f"{device}/{channel}: signature key_id={signature.get('key_id')!r}, "
            f"expected {public_key.get('key_id')!r}"
        )
    try:
        metadata, _ = load_public_metadata(public_key_path)
        if metadata["channel"] != channel:
            fail(
                f"{device}/{channel}: public-key channel={metadata['channel']!r}, "
                f"expected {channel!r}"
            )
    except SystemExit as exc:
        fail(f"{device}/{channel}: public-key metadata invalid: {exc}")

    required = {
        "version",
        "commit",
        "branch",
        "channel",
        "target",
        "url",
        "size",
        "sha256",
        "published_at",
    }
    missing = sorted(required.difference(manifest))
    if missing:
        fail(f"{device}/{channel}: manifest missing fields: {', '.join(missing)}")
        return manifest

    if manifest["channel"] != channel:
        fail(
            f"{device}/{channel}: manifest channel={manifest['channel']!r}, "
            f"expected {channel!r}"
        )
    if manifest["branch"] != expected_branch(channel):
        fail(
            f"{device}/{channel}: manifest branch={manifest['branch']!r}, "
            f"expected {expected_branch(channel)!r}"
        )
    if manifest["target"] != spec["target"]:
        fail(
            f"{device}/{channel}: target={manifest['target']!r}, "
            f"expected {spec['target']!r}"
        )
    if spec.get("hardware") and manifest.get("hardware") != spec["hardware"]:
        fail(
            f"{device}/{channel}: hardware={manifest.get('hardware')!r}, "
            f"expected {spec['hardware']!r}"
        )

    for key, value in spec["manifest_fields"].items():
        if manifest.get(key) != value:
            fail(
                f"{device}/{channel}: {key}={manifest.get(key)!r}, "
                f"expected {value!r}"
            )

    if expected_version and manifest["version"] != expected_version:
        fail(
            f"{device}/{channel}: version={manifest['version']}, "
            f"expected {expected_version}"
        )

    commit = manifest["commit"]
    if not isinstance(commit, str) or len(commit) != 40:
        fail(f"{device}/{channel}: commit must be a full 40-character SHA")
        return manifest

    prefix = expected_prefix(spec, channel)
    url = manifest["url"]
    if not isinstance(url, str) or not url.startswith(prefix):
        fail(f"{device}/{channel}: artifact URL is outside expected path: {url!r}")
        return manifest

    parsed = urlparse(url)
    if parsed.query or parsed.fragment:
        fail(f"{device}/{channel}: artifact URL must not contain query/fragment")

    filename = url[len(prefix):]
    expected_filename = f"{spec['app']}-{commit[:12]}.bin"
    if filename != expected_filename:
        fail(
            f"{device}/{channel}: artifact filename={filename!r}, "
            f"expected {expected_filename!r}"
        )
        return manifest

    artifact = directory / filename
    if not artifact.is_file():
        fail(f"{device}/{channel}: missing artifact {artifact}")
        return manifest

    data = artifact.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    if len(data) != manifest["size"]:
        fail(
            f"{device}/{channel}: size mismatch manifest={manifest['size']} "
            f"actual={len(data)}"
        )
    if actual_sha != manifest["sha256"]:
        fail(
            f"{device}/{channel}: SHA mismatch manifest={manifest['sha256']} "
            f"actual={actual_sha}"
        )

    try:
        chip, image_version, app = image_identity(data)
    except (ValueError, UnicodeDecodeError) as exc:
        fail(f"{device}/{channel}: invalid ESP-IDF image: {exc}")
        return manifest

    if chip != spec["chip_id"]:
        fail(
            f"{device}/{channel}: image chip={chip}, "
            f"expected {spec['chip_id']}"
        )
    if app != spec["app"]:
        fail(
            f"{device}/{channel}: image app={app!r}, expected {spec['app']!r}"
        )
    if image_version != manifest["version"]:
        fail(
            f"{device}/{channel}: image version={image_version}, "
            f"manifest={manifest['version']}"
        )

    if not any(
        message.startswith(f"{device}/{channel}:")
        for message in ERRORS
    ):
        passed(
            f"{device}/{channel}: binary identity, size, SHA, hardware, "
            "protocol, URL, and metadata valid"
        )
    return manifest


def git_output(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def source_repo_path(spec, scale_root, display_root):
    return scale_root if spec["source_repo"] == "KegScaleESP" else display_root


def validate_source(device, manifest, spec, scale_root, display_root):
    if manifest is None:
        return
    repo = source_repo_path(spec, scale_root, display_root)
    if not repo.exists():
        fail(f"{device}: source checkout not found: {repo}")
        return

    commit = manifest.get("commit")
    try:
        git_output(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        fail(f"{device}: manifest commit {commit} is not present in {repo}: {exc}")
        return

    version_file = spec["source_version_file"]
    try:
        source_version = git_output(repo, "show", f"{commit}:{version_file}")
    except subprocess.CalledProcessError as exc:
        fail(
            f"{device}: cannot read {version_file} from manifest commit "
            f"{commit}: {exc.output.strip()}"
        )
        return

    if source_version != manifest.get("version"):
        fail(
            f"{device}: source version at {commit[:12]} is {source_version}, "
            f"manifest is {manifest.get('version')}"
        )
    else:
        passed(
            f"{device}: manifest commit exists in source and version.txt matches "
            f"{manifest.get('version')}"
        )


def validate_promotion(device, prod_manifest, dev_manifest, spec):
    if prod_manifest is None or dev_manifest is None:
        return

    if prod_manifest.get("promoted_from") != "signed-dev":
        fail(f"{device}/production: missing promoted_from='signed-dev'")
    if prod_manifest.get("source_branch") != "dev":
        fail(f"{device}/production: missing source_branch='dev'")

    dev_manifest_path = (
        ROOT / spec["family"] / "dev" / spec["target"] / "manifest.json"
    )
    expected_manifest_sha = hashlib.sha256(dev_manifest_path.read_bytes()).hexdigest()
    if prod_manifest.get("promoted_manifest_sha256") != expected_manifest_sha:
        fail(
            f"{device}/production: promoted_manifest_sha256 does not match "
            "current signed Dev manifest"
        )

    fields = ["version", "commit", "size", "sha256"]
    for field in fields:
        if prod_manifest.get(field) != dev_manifest.get(field):
            fail(
                f"{device}/production: {field} differs from Dev "
                f"({prod_manifest.get(field)!r} != {dev_manifest.get(field)!r})"
            )

    dev_url = dev_manifest.get("url", "")
    prod_url = prod_manifest.get("url", "")
    dev_name = Path(urlparse(dev_url).path).name
    prod_name = Path(urlparse(prod_url).path).name
    if dev_name != prod_name:
        fail(
            f"{device}/production: promoted binary filename differs from Dev "
            f"({prod_name!r} != {dev_name!r})"
        )

    dev_file = ROOT / spec["family"] / "dev" / spec["target"] / dev_name
    prod_file = ROOT / spec["family"] / "production" / spec["target"] / prod_name
    if dev_file.is_file() and prod_file.is_file():
        if dev_file.read_bytes() != prod_file.read_bytes():
            fail(f"{device}/production: Production bytes differ from Dev")
        else:
            passed(f"{device}/production: exact Dev binary bytes preserved")
    else:
        fail(f"{device}/production: Dev/Production artifact missing for promotion proof")


def validate_beta_mirror(device, beta_manifest, prod_manifest, spec):
    if beta_manifest is None or prod_manifest is None:
        return

    if beta_manifest.get("mirrored_from") != "production":
        fail(f"{device}/beta: missing mirrored_from='production'")
    if beta_manifest.get("source_branch") != "main":
        fail(f"{device}/beta: missing source_branch='main'")

    prod_manifest_path = (
        ROOT / spec["family"] / "production" / spec["target"] / "manifest.json"
    )
    expected_manifest_sha = hashlib.sha256(prod_manifest_path.read_bytes()).hexdigest()
    if beta_manifest.get("mirrored_manifest_sha256") != expected_manifest_sha:
        fail(
            f"{device}/beta: mirrored_manifest_sha256 does not match "
            "current signed Production manifest"
        )

    for field in ["version", "commit", "size", "sha256"]:
        if beta_manifest.get(field) != prod_manifest.get(field):
            fail(
                f"{device}/beta: {field} differs from Production "
                f"({beta_manifest.get(field)!r} != {prod_manifest.get(field)!r})"
            )

    beta_name = Path(urlparse(beta_manifest.get("url", "")).path).name
    prod_name = Path(urlparse(prod_manifest.get("url", "")).path).name
    if beta_name != prod_name:
        fail(
            f"{device}/beta: binary filename differs from Production "
            f"({beta_name!r} != {prod_name!r})"
        )

    beta_file = ROOT / spec["family"] / "beta" / spec["target"] / beta_name
    prod_file = ROOT / spec["family"] / "production" / spec["target"] / prod_name
    if beta_file.is_file() and prod_file.is_file():
        if beta_file.read_bytes() != prod_file.read_bytes():
            fail(f"{device}/beta: Beta bytes differ from Production")
        else:
            passed(f"{device}/beta: exact Production binary bytes preserved")
    else:
        fail(f"{device}/beta: Beta/Production artifact missing for mirror proof")


def validate_firmware_docs(contract):
    readme = ROOT / "README.md"
    if not readme.is_file():
        fail("firmware README.md missing")
        return

    text = readme.read_text(encoding="utf-8")
    version = contract["current_production"]["version"]
    date_value = datetime.strptime(
        contract["current_production"]["date"], "%Y-%m-%d"
    )
    long_date = date_value.strftime("%B %d, %Y").replace(" 0", " ")
    expected = f"Current coordinated release: **{version}**, {long_date}."
    if expected not in text:
        fail(f"README current-release line is stale; expected: {expected}")
    else:
        passed("firmware README current coordinated release matches contract")

    required_phrases = [
        "Production, Beta, and Development are supported release channels.",
        "Beta normally mirrors current Production",
        "sync_beta_to_production.py",
        "promote_dev_to_production.py",
        "release_check.py",
    ]
    for phrase in required_phrases:
        if phrase not in text:
            fail(f"firmware README missing release-policy text: {phrase!r}")


def validate_source_docs(scale_root, display_root):
    roadmap = scale_root / "ROADMAP.md"
    epaper_readme = display_root / "README.md"
    touch_readme = display_root / "touchscreen" / "README.md"

    required = {
        roadmap: [
            "P2 #12 release/documentation automation",
            "Signed OTA manifests",
            "MQTT TLS",
            "Download Support Bundle",
        ],
        epaper_readme: [
            "six-digit pairing code",
            "five-minute pairing window",
            "GPIO12",
            "3-minute scheduled check-in",
            "one-hour safety timer",
            "LILYGO T5 V2.3.1",
        ],
        touch_readme: [
            "Waveshare ESP32-S3-Touch-LCD-4B",
            "six-character hexadecimal pairing code",
            "protocol 1",
            "Beta normally mirrors current Production",
        ],
    }

    forbidden = {
        roadmap: [
            "Finish the Touch `main_wrapper.cpp` removal checkpoint",
            "Add signed OTA manifests and protected production release gates.",
            "Add MQTT TLS certificate trust/configuration",
        ],
        touch_readme: [
            "# Wi-Fi touchscreen controller - V1.3.4",
        ],
    }

    for path, phrases in required.items():
        if not path.is_file():
            warn(f"source documentation not available locally: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                fail(f"{path}: missing required release-documentation text {phrase!r}")
        for phrase in forbidden.get(path, []):
            if phrase in text:
                fail(f"{path}: stale release-documentation text still present {phrase!r}")

    if not any(str(path) in message for path in required for message in ERRORS):
        passed("source pairing, wake-policy, hardware, protocol, and hardening docs are synchronized")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device",
        action="append",
        choices=["scale", "display", "touchscreen"],
        dest="devices",
        help="Device to check. Repeat as needed. Default: all.",
    )
    parser.add_argument(
        "--channel",
        action="append",
        choices=["dev", "beta", "production"],
        dest="channels",
        help="Channel to check. Repeat as needed. Default: dev, beta, and production.",
    )
    parser.add_argument(
        "--version",
        help="Require selected manifests and embedded images to use this version.",
    )
    parser.add_argument(
        "--source-check",
        action="store_true",
        help="Verify manifest commits and source version files in sibling checkouts.",
    )
    parser.add_argument(
        "--require-promotion",
        action="store_true",
        help="Require Production to be an exact signed-Dev promotion with provenance.",
    )
    parser.add_argument(
        "--skip-docs",
        action="store_true",
        help="Skip firmware/source documentation contract checks.",
    )
    parser.add_argument(
        "--scale-root",
        type=Path,
        default=ROOT.parent / "KegScaleESP",
    )
    parser.add_argument(
        "--display-root",
        type=Path,
        default=ROOT.parent / "KegScaleESPDisplay",
    )
    args = parser.parse_args()

    contract = load_json(CONTRACT_PATH)
    if contract is None:
        return 1

    devices = args.devices or list(contract["devices"])
    channels = args.channels or ["dev", "beta", "production"]
    scale_root = args.scale_root.resolve()
    display_root = args.display_root.resolve()

    print("Keg Scale release validation")
    print(f"  devices: {', '.join(devices)}")
    print(f"  channels: {', '.join(channels)}")
    if args.version:
        print(f"  required version: {args.version}")

    manifests = {}
    for device in devices:
        spec = contract["devices"][device]
        for channel in channels:
            manifests[(device, channel)] = validate_manifest(
                device,
                channel,
                spec,
                contract,
                args.version,
            )

    if "production" in channels and not args.version:
        production_versions = {
            manifests[(device, "production")].get("version")
            for device in devices
            if manifests.get((device, "production"))
        }
        expected_production = contract["current_production"]["version"]
        if production_versions != {expected_production}:
            fail(
                "Production manifests do not match coordinated release contract: "
                f"found={sorted(production_versions)} expected={expected_production}"
            )
        else:
            passed(
                f"coordinated Production version is {expected_production} "
                "for all selected devices"
            )

    if "beta" in channels:
        for device in devices:
            spec = contract["devices"][device]
            prod_manifest = manifests.get((device, "production"))
            if prod_manifest is None:
                prod_manifest = validate_manifest(
                    device, "production", spec, contract, args.version
                )
            validate_beta_mirror(
                device,
                manifests.get((device, "beta")),
                prod_manifest,
                spec,
            )

    if args.source_check:
        source_channel = "dev" if "dev" in channels else channels[0]
        for device in devices:
            validate_source(
                device,
                manifests.get((device, source_channel)),
                contract["devices"][device],
                scale_root,
                display_root,
            )

    if args.require_promotion:
        if "production" not in channels:
            fail("--require-promotion requires --channel production")
        for device in devices:
            spec = contract["devices"][device]
            dev_manifest = manifests.get((device, "dev"))
            if dev_manifest is None:
                dev_manifest = validate_manifest(
                    device, "dev", spec, contract, args.version
                )
            validate_promotion(
                device,
                manifests.get((device, "production")),
                dev_manifest,
                spec,
            )

    if not args.skip_docs:
        validate_firmware_docs(contract)
        validate_source_docs(scale_root, display_root)

    print()
    print(
        f"Release check summary: {len(PASSES)} pass, "
        f"{len(WARNINGS)} warning, {len(ERRORS)} failure"
    )
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())
