#!/usr/bin/env python3
"""Sign the currently published OTA manifests without rebuilding firmware."""

import argparse
import os
from pathlib import Path

from ota_signing import sign_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[1]

DEVICE_PATHS = {
    "scale": ("firmware", "esp32s3"),
    "display": ("display", "esp32"),
    "touchscreen": ("touchscreen", "esp32s3"),
}


def key_dir_from_args(explicit):
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("KEGSCALE_OTA_KEY_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".kegscale" / "ota-keys").resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device",
        action="append",
        choices=sorted(DEVICE_PATHS),
        dest="devices",
        help="Device to sign. Repeat as needed. Default: all.",
    )
    parser.add_argument(
        "--channel",
        action="append",
        choices=["dev", "production"],
        dest="channels",
        help="Channel to sign. Repeat as needed. Default: both.",
    )
    parser.add_argument(
        "--signing-key-dir",
        type=Path,
        help="Private-key directory. Defaults to KEGSCALE_OTA_KEY_DIR or ~/.kegscale/ota-keys.",
    )
    args = parser.parse_args()

    devices = args.devices or list(DEVICE_PATHS)
    channels = args.channels or ["dev", "production"]
    key_dir = key_dir_from_args(args.signing_key_dir)

    for channel in channels:
        private_key = key_dir / f"ota-{channel}-private.pem"
        public_key = ROOT / "keys" / f"ota-{channel}-public-key.json"
        if not private_key.exists():
            raise SystemExit(f"Missing private signing key: {private_key}")
        if not public_key.exists():
            raise SystemExit(f"Missing committed public-key metadata: {public_key}")

        for device in devices:
            family, target = DEVICE_PATHS[device]
            directory = ROOT / family / channel / target
            manifest = directory / "manifest.json"
            signature = directory / "manifest.sig"
            if not manifest.exists():
                raise SystemExit(f"Missing manifest: {manifest}")

            document = sign_manifest(
                manifest,
                channel,
                private_key,
                public_key,
                signature,
            )
            if not verify_manifest(manifest, signature, public_key):
                raise SystemExit(f"Post-sign verification failed: {manifest}")

            print(
                f"{device}/{channel}: signed {manifest.relative_to(ROOT)} "
                f"key_id={document['key_id']}"
            )


if __name__ == "__main__":
    main()
