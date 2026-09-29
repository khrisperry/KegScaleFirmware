#!/usr/bin/env python3
"""Mirror exact signed Production OTA artifacts into the signed Beta channel.

Beta uses the Dev signing trust domain, keeping the Production private key isolated.
This tool copies the exact Production binary bytes for Scale, e-paper, and Touch,
rewrites only channel/path/provenance metadata, signs the Beta manifest with the
Dev private key, and verifies the resulting Beta signature.
"""

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


def artifact_from_manifest(manifest, spec, channel):
    url = manifest.get("url", "")
    prefix = expected_prefix(spec, channel)
    if not url.startswith(prefix):
        raise SystemExit(
            f"{channel} manifest URL is outside the expected path: {url}"
        )

    filename = url[len(prefix):]
    parsed = urlparse(url)
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or filename in {".", ".."}
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit(f"Invalid {channel} artifact URL: {url}")

    artifact = ROOT / spec["family"] / channel / spec["target"] / filename
    if not artifact.is_file():
        raise SystemExit(f"Missing {channel} artifact: {artifact}")

    data = artifact.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    if len(data) != manifest.get("size"):
        raise SystemExit(
            f"{channel} artifact size mismatch: "
            f"manifest={manifest.get('size')} actual={len(data)}"
        )
    if actual_sha != manifest.get("sha256"):
        raise SystemExit(
            f"{channel} artifact SHA mismatch: "
            f"manifest={manifest.get('sha256')} actual={actual_sha}"
        )
    return artifact, data, actual_sha


def sync_one(device, key_dir, stamp):
    spec = DEVICE_PATHS[device]
    prod_dir = ROOT / spec["family"] / "production" / spec["target"]
    beta_dir = ROOT / spec["family"] / "beta" / spec["target"]

    prod_manifest_path = prod_dir / "manifest.json"
    prod_signature_path = prod_dir / "manifest.sig"
    prod_public = ROOT / "keys" / "ota-production-public-key.json"

    if not prod_manifest_path.is_file() or not prod_signature_path.is_file():
        raise SystemExit(
            f"{device}: Production manifest/signature must both exist"
        )
    if not verify_manifest(prod_manifest_path, prod_signature_path, prod_public):
        raise SystemExit(
            f"{device}: refusing Beta sync because Production signature is invalid"
        )

    prod_manifest_bytes = prod_manifest_path.read_bytes()
    production = json.loads(prod_manifest_bytes.decode("utf-8"))

    if production.get("channel") != "production":
        raise SystemExit(f"{device}: source manifest is not Production")
    if production.get("target") != spec["target"]:
        raise SystemExit(
            f"{device}: Production target mismatch: {production.get('target')!r}"
        )

    prod_artifact, data, artifact_sha = artifact_from_manifest(
        production, spec, "production"
    )

    beta_dir.mkdir(parents=True, exist_ok=True)
    beta_artifact = beta_dir / prod_artifact.name
    if beta_artifact.exists() and beta_artifact.read_bytes() != data:
        raise SystemExit(
            f"{device}: refusing to overwrite different Beta artifact: "
            f"{beta_artifact}"
        )
    shutil.copyfile(prod_artifact, beta_artifact)

    beta = dict(production)
    beta.update(
        branch="beta",
        channel="beta",
        source_branch="main",
        mirrored_from="production",
        mirrored_manifest_sha256=hashlib.sha256(prod_manifest_bytes).hexdigest(),
        published_at=stamp,
        url=(
            "https://raw.githubusercontent.com/"
            "khrisperry/KegScaleFirmware/main/"
            + beta_artifact.relative_to(ROOT).as_posix()
        ),
    )
    beta.pop("promoted_from", None)
    beta.pop("promoted_manifest_sha256", None)

    beta_manifest_path = beta_dir / "manifest.json"
    beta_manifest_path.write_bytes(
        (json.dumps(beta, indent=2) + "\n").encode("utf-8")
    )

    beta_public = ROOT / "keys" / "ota-beta-public-key.json"
    dev_private = key_dir / "ota-dev-private.pem"
    if not beta_public.is_file():
        raise SystemExit(f"Missing Beta public-key metadata: {beta_public}")
    if not dev_private.is_file():
        raise SystemExit(
            f"Missing Dev private key used for Beta signing: {dev_private}"
        )

    beta_signature_path = beta_dir / "manifest.sig"
    signature = sign_manifest(
        beta_manifest_path,
        "beta",
        dev_private,
        beta_public,
        beta_signature_path,
    )
    if not verify_manifest(
        beta_manifest_path,
        beta_signature_path,
        beta_public,
    ):
        raise SystemExit(f"{device}: Beta signature verification failed")

    if beta_artifact.read_bytes() != data:
        raise SystemExit(
            f"{device}: Beta binary differs from Production after copy"
        )

    print(
        f"{device}: Beta now mirrors Production "
        f"version={production.get('version')} "
        f"sha256={artifact_sha} "
        f"beta_key_id={signature['key_id']}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device",
        action="append",
        choices=sorted(DEVICE_PATHS),
        dest="devices",
        help="Device to sync. Repeat as needed. Default: all.",
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

    if git("status", "--porcelain"):
        raise SystemExit(
            "KegScaleFirmware checkout must be clean before Beta synchronization"
        )

    key_dir = resolve_signing_key_dir(args.signing_key_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    selected = args.devices or list(DEVICE_PATHS)

    for device in selected:
        sync_one(device, key_dir, stamp)

    print()
    print("Beta synchronization prepared locally.")
    print("Run: python tools\\release_check.py")
    print("Then review git diff/status before commit and push.")


if __name__ == "__main__":
    main()
