#!/usr/bin/env python3
"""Promote exact signed Dev OTA artifacts to Production without rebuilding."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse

from ota_signing import sign_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[1]

DEVICE_PATHS = {
    "scale": {
        "family": "firmware",
        "target": "esp32s3",
        "app": "keg_scale_esp",
    },
    "display": {
        "family": "display",
        "target": "esp32",
        "app": "keg_scale_display",
    },
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


def expected_raw_prefix(family, channel, target):
    return (
        "https://raw.githubusercontent.com/"
        "khrisperry/KegScaleFirmware/main/"
        f"{family}/{channel}/{target}/"
    )


def resolve_artifact(manifest, family, target):
    url = manifest.get("url", "")
    prefix = expected_raw_prefix(family, "dev", target)
    if not url.startswith(prefix):
        raise SystemExit(
            f"Dev manifest URL is outside the expected signed Dev path: {url}"
        )

    filename = url[len(prefix):]
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise SystemExit(f"Invalid Dev artifact filename in manifest: {filename!r}")

    parsed = urlparse(url)
    if parsed.query or parsed.fragment:
        raise SystemExit(f"Dev artifact URL must not contain query/fragment: {url}")

    artifact = ROOT / family / "dev" / target / filename
    if not artifact.is_file():
        raise SystemExit(f"Dev artifact is missing locally: {artifact}")

    data = artifact.read_bytes()
    expected_size = manifest.get("size")
    expected_sha = manifest.get("sha256")

    if not isinstance(expected_size, int) or expected_size <= 0:
        raise SystemExit(f"Dev manifest has invalid size: {expected_size!r}")
    if len(data) != expected_size:
        raise SystemExit(
            f"Dev artifact size mismatch: manifest={expected_size} actual={len(data)}"
        )

    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise SystemExit(
            f"Dev artifact SHA-256 mismatch: manifest={expected_sha} actual={actual_sha}"
        )

    return artifact, data, actual_sha


def promote_one(device, requested_version, key_dir, stamp):
    spec = DEVICE_PATHS[device]
    family = spec["family"]
    target = spec["target"]

    dev_dir = ROOT / family / "dev" / target
    prod_dir = ROOT / family / "production" / target
    dev_manifest_path = dev_dir / "manifest.json"
    dev_signature_path = dev_dir / "manifest.sig"

    if not dev_manifest_path.is_file() or not dev_signature_path.is_file():
        raise SystemExit(
            f"{device}: Dev manifest/signature must both exist before promotion"
        )

    dev_public = ROOT / "keys" / "ota-dev-public-key.json"
    prod_public = ROOT / "keys" / "ota-production-public-key.json"
    prod_private = key_dir / "ota-production-private.pem"

    if not prod_private.is_file():
        raise SystemExit(f"Missing Production private signing key: {prod_private}")
    if not dev_public.is_file() or not prod_public.is_file():
        raise SystemExit("Committed OTA public-key metadata is missing")

    if not verify_manifest(dev_manifest_path, dev_signature_path, dev_public):
        raise SystemExit(
            f"{device}: refusing promotion because the Dev manifest signature is invalid"
        )

    dev_manifest_bytes = dev_manifest_path.read_bytes()
    dev_manifest = json.loads(dev_manifest_bytes.decode("utf-8"))

    if dev_manifest.get("channel") != "dev":
        raise SystemExit(f"{device}: source manifest is not Dev")
    if dev_manifest.get("target") != target:
        raise SystemExit(
            f"{device}: Dev target mismatch: {dev_manifest.get('target')!r}"
        )
    if dev_manifest.get("version") != requested_version:
        raise SystemExit(
            f"{device}: Dev version {dev_manifest.get('version')} does not match "
            f"requested promotion {requested_version}"
        )

    artifact, data, artifact_sha = resolve_artifact(dev_manifest, family, target)

    prod_manifest_path = prod_dir / "manifest.json"
    if prod_manifest_path.is_file():
        current_prod = json.loads(prod_manifest_path.read_text(encoding="utf-8"))
        current_version = current_prod.get("version")
        if semantic_version(requested_version) <= semantic_version(current_version):
            raise SystemExit(
                f"{device}: refusing non-new Production promotion: "
                f"current={current_version} candidate={requested_version}"
            )

    prod_dir.mkdir(parents=True, exist_ok=True)
    prod_artifact = prod_dir / artifact.name
    if prod_artifact.exists() and prod_artifact.read_bytes() != data:
        raise SystemExit(
            f"{device}: refusing to overwrite different Production artifact: "
            f"{prod_artifact}"
        )
    shutil.copyfile(artifact, prod_artifact)

    production = dict(dev_manifest)
    production.update(
        branch="main",
        channel="production",
        source_branch=dev_manifest.get("branch", "dev"),
        promoted_from="signed-dev",
        promoted_manifest_sha256=hashlib.sha256(dev_manifest_bytes).hexdigest(),
        published_at=stamp,
        url=(
            "https://raw.githubusercontent.com/"
            "khrisperry/KegScaleFirmware/main/"
            + prod_artifact.relative_to(ROOT).as_posix()
        ),
    )

    prod_manifest_path.write_bytes(
        (json.dumps(production, indent=2) + "\n").encode("utf-8")
    )

    prod_signature_path = prod_dir / "manifest.sig"
    signature = sign_manifest(
        prod_manifest_path,
        "production",
        prod_private,
        prod_public,
        prod_signature_path,
    )
    if not verify_manifest(prod_manifest_path, prod_signature_path, prod_public):
        raise SystemExit(
            f"{device}: Production manifest failed post-sign verification"
        )

    if hashlib.sha256(prod_artifact.read_bytes()).hexdigest() != artifact_sha:
        raise SystemExit(
            f"{device}: Production binary changed during exact-artifact promotion"
        )

    print(
        f"{device}: promoted exact Dev binary {artifact.name} "
        f"version={requested_version} sha256={artifact_sha} "
        f"production_key_id={signature['key_id']}"
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
    parser.add_argument(
        "--signing-key-dir",
        type=Path,
        help=(
            "Private-key directory. Defaults to KEGSCALE_OTA_KEY_DIR or "
            "~/.kegscale/ota-keys."
        ),
    )
    args = parser.parse_args()

    semantic_version(args.version)

    if git("status", "--porcelain"):
        raise SystemExit(
            "KegScaleFirmware checkout must be clean before Production promotion"
        )

    key_dir = resolve_signing_key_dir(args.signing_key_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for device in args.devices or list(DEVICE_PATHS):
        promote_one(device, args.version, key_dir, stamp)

    print()
    print("Production promotion prepared locally. Review git diff/status before commit.")
    print("No firmware was rebuilt.")


if __name__ == "__main__":
    main()
