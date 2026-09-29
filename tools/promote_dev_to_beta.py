#!/usr/bin/env python3
"""Promote an exact signed Dev OTA artifact to Beta for release-candidate testing.

Beta uses the Dev signing trust domain. This does not rebuild firmware and does
not use the Production private key.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlparse

from ota_signing import sign_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[1]

DEVICE_PATHS = {
    "scale": {"family": "firmware", "target": "esp32s3", "app": "keg_scale_esp"},
    "display": {"family": "display", "target": "esp32", "app": "keg_scale_display"},
    "touchscreen": {
        "family": "touchscreen",
        "target": "esp32s3",
        "app": "keg_scale_touchscreen",
    },
}


def git(*args):
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args],
        text=True,
    ).strip()


def semantic_version(value):
    if not value or not value.startswith("V"):
        raise SystemExit(f"Invalid semantic firmware version: {value!r}")
    parts = value[1:].split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise SystemExit(f"Invalid semantic firmware version: {value!r}")
    return tuple(int(part) for part in parts)


def resolve_signing_key_dir(explicit):
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("KEGSCALE_OTA_KEY_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".kegscale" / "ota-keys").resolve()


def expected_prefix(spec, channel):
    return (
        "https://raw.githubusercontent.com/"
        "khrisperry/KegScaleFirmware/main/"
        f"{spec['family']}/{channel}/{spec['target']}/"
    )


def resolve_dev_artifact(manifest, spec):
    url = manifest.get("url", "")
    prefix = expected_prefix(spec, "dev")
    if not url.startswith(prefix):
        raise SystemExit(f"Dev manifest URL is outside expected path: {url}")

    filename = url[len(prefix):]
    parsed = urlparse(url)
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit(f"Invalid Dev artifact URL: {url}")

    artifact = ROOT / spec["family"] / "dev" / spec["target"] / filename
    if not artifact.is_file():
        raise SystemExit(f"Missing Dev artifact: {artifact}")

    data = artifact.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if len(data) != manifest.get("size") or sha != manifest.get("sha256"):
        raise SystemExit("Dev artifact size/SHA does not match signed manifest")
    return artifact, data, sha


def promote_one(device, requested_version, key_dir, stamp):
    spec = DEVICE_PATHS[device]
    dev_dir = ROOT / spec["family"] / "dev" / spec["target"]
    beta_dir = ROOT / spec["family"] / "beta" / spec["target"]

    dev_manifest_path = dev_dir / "manifest.json"
    dev_signature_path = dev_dir / "manifest.sig"
    dev_public = ROOT / "keys" / "ota-dev-public-key.json"

    if not verify_manifest(dev_manifest_path, dev_signature_path, dev_public):
        raise SystemExit(f"{device}: Dev manifest signature is invalid")

    dev_manifest_bytes = dev_manifest_path.read_bytes()
    dev = json.loads(dev_manifest_bytes.decode("utf-8"))
    if dev.get("channel") != "dev":
        raise SystemExit(f"{device}: source manifest is not Dev")
    if dev.get("version") != requested_version:
        raise SystemExit(
            f"{device}: Dev version={dev.get('version')} "
            f"requested={requested_version}"
        )

    artifact, data, sha = resolve_dev_artifact(dev, spec)

    beta_manifest_path = beta_dir / "manifest.json"
    if beta_manifest_path.is_file():
        current_beta = json.loads(beta_manifest_path.read_text(encoding="utf-8"))
        current_version = current_beta.get("version")
        if semantic_version(requested_version) <= semantic_version(current_version):
            raise SystemExit(
                f"{device}: refusing non-new Beta candidate: "
                f"current={current_version} candidate={requested_version}"
            )

    beta_dir.mkdir(parents=True, exist_ok=True)
    beta_artifact = beta_dir / artifact.name
    if beta_artifact.exists() and beta_artifact.read_bytes() != data:
        raise SystemExit(
            f"{device}: refusing to overwrite different Beta artifact: {beta_artifact}"
        )
    shutil.copyfile(artifact, beta_artifact)

    beta = dict(dev)
    beta.update(
        branch="beta",
        channel="beta",
        source_branch="dev",
        promoted_from="signed-dev",
        promoted_manifest_sha256=hashlib.sha256(dev_manifest_bytes).hexdigest(),
        published_at=stamp,
        url=(
            "https://raw.githubusercontent.com/"
            "khrisperry/KegScaleFirmware/main/"
            + beta_artifact.relative_to(ROOT).as_posix()
        ),
    )
    beta.pop("mirrored_from", None)
    beta.pop("mirrored_manifest_sha256", None)

    beta_manifest_path.write_bytes(
        (json.dumps(beta, indent=2) + "\n").encode("utf-8")
    )

    beta_public = ROOT / "keys" / "ota-beta-public-key.json"
    dev_private = key_dir / "ota-dev-private.pem"
    signature_path = beta_dir / "manifest.sig"
    signature = sign_manifest(
        beta_manifest_path,
        "beta",
        dev_private,
        beta_public,
        signature_path,
    )
    if not verify_manifest(beta_manifest_path, signature_path, beta_public):
        raise SystemExit(f"{device}: Beta signature verification failed")

    if beta_artifact.read_bytes() != data:
        raise SystemExit(f"{device}: Beta candidate bytes changed during promotion")

    print(
        f"{device}: promoted exact Dev binary to Beta "
        f"version={requested_version} sha256={sha} "
        f"beta_key_id={signature['key_id']}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--device",
        action="append",
        choices=sorted(DEVICE_PATHS),
        dest="devices",
        help="Device to promote. Repeat as needed. Default: all.",
    )
    parser.add_argument("--signing-key-dir", type=Path)
    args = parser.parse_args()

    semantic_version(args.version)
    if git("status", "--porcelain"):
        raise SystemExit(
            "KegScaleFirmware checkout must be clean before Beta promotion"
        )

    key_dir = resolve_signing_key_dir(args.signing_key_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    selected = args.devices or list(DEVICE_PATHS)

    for device in selected:
        promote_one(device, args.version, key_dir, stamp)

    check_cmd = [
        sys.executable,
        str(ROOT / "tools" / "release_check.py"),
        "--channel", "beta",
        "--version", args.version,
        "--allow-beta-candidate",
        "--source-check",
        "--skip-docs",
    ]
    for device in selected:
        check_cmd.extend(["--device", device])
    subprocess.check_call(check_cmd)

    print()
    print("Beta candidate prepared locally from exact signed Dev bytes.")
    print("Review git diff/status before commit and push.")


if __name__ == "__main__":
    main()
