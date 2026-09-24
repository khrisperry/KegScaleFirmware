#!/usr/bin/env python3
"""Prepare selected OTA artifacts from clean, locally validated source checkouts.

Does not commit, push, tag, or delete older images. By default it preserves the
historical behavior of preparing all devices for dev and production. Use
--device and --channel to limit the publication scope, for example:

  python tools/prepare_release.py --version V1.3.12 --device scale --channel dev
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.check_output(
        ['git', '-C', str(repo), *args], text=True
    ).strip()


def image_identity(data):
    if len(data) < 112 or data[0] != 0xE9:
        raise SystemExit('Not an ESP-IDF application image')
    chip = struct.unpack_from('<H', data, 12)[0]
    if struct.unpack_from('<I', data, 32)[0] != 0xABCD5432:
        raise SystemExit('Missing ESP-IDF app descriptor')
    version = data[48:80].split(b'\0')[0].decode()
    app = data[80:112].split(b'\0')[0].decode()
    return chip, version, app


def device_specs(scale, display):
    return {
        'scale': {
            'family': 'firmware',
            'target': 'esp32s3',
            'repo': scale,
            'build': scale / 'build',
            'app': 'keg_scale_esp',
            'chip': 9,
        },
        'display': {
            'family': 'display',
            'target': 'esp32',
            'repo': display,
            'build': display / 'build',
            'app': 'keg_scale_display',
            'chip': 0,
        },
        'touchscreen': {
            'family': 'touchscreen',
            'target': 'esp32s3',
            'repo': display,
            'build': display / 'touchscreen' / 'build',
            'app': 'keg_scale_touchscreen',
            'chip': 9,
        },
    }


def prepare(version, scale, display, selected_devices, channels):
    specs = device_specs(scale, display)

    repos = {specs[name]['repo'] for name in selected_devices}
    for repo in repos:
        if git(repo, 'status', '--porcelain'):
            raise SystemExit(f'Source checkout must be clean: {repo}')

    prepared = []

    # Validate every selected image before changing any manifest or artifact.
    for name in selected_devices:
        spec = specs[name]
        image_path = spec['build'] / (spec['app'] + '.bin')
        if not image_path.exists():
            raise SystemExit(
                f'Missing local build for {name}: {image_path}\n'
                'Build the selected device locally before preparing OTA.'
            )

        data = image_path.read_bytes()
        chip, actual_version, actual_app = image_identity(data)

        if chip != spec['chip']:
            raise SystemExit(
                f'Unexpected image target for {name}: chip={chip} '
                f'expected={spec["chip"]}'
            )
        if actual_version != version or actual_app != spec['app']:
            raise SystemExit(
                f'Image identity mismatch for {name}: '
                f'version={actual_version}, app={actual_app}; '
                f'expected version={version}, app={spec["app"]}'
            )

        repo = spec['repo']
        commit = git(repo, 'rev-parse', 'HEAD')
        template_path = (
            ROOT / spec['family'] / 'dev' / spec['target'] / 'manifest.json'
        )
        template = json.loads(template_path.read_text())
        prepared.append((name, spec, data, commit, template))

    inventory = []
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    for name, spec, data, commit, template in prepared:
        for channel in channels:
            directory = ROOT / spec['family'] / channel / spec['target']
            directory.mkdir(parents=True, exist_ok=True)
            filename = f'{spec["app"]}-{commit[:12]}.bin'
            artifact = directory / filename

            if artifact.exists() and artifact.read_bytes() != data:
                raise SystemExit(
                    'Refusing to replace a different commit-named image: '
                    f'{artifact}'
                )

            artifact.write_bytes(data)
            manifest = dict(
                template,
                version=version,
                commit=commit,
                branch='main' if channel == 'production' else 'dev',
                channel=channel,
                target=spec['target'],
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                published_at=stamp,
                url=(
                    'https://raw.githubusercontent.com/'
                    'khrisperry/KegScaleFirmware/main/'
                    + artifact.relative_to(ROOT).as_posix()
                ),
            )
            (directory / 'manifest.json').write_text(
                json.dumps(manifest, indent=2) + '\n',
                newline='\n',
            )
            inventory.append(
                dict(
                    device=spec['family'],
                    channel=channel,
                    **{
                        key: manifest[key]
                        for key in [
                            'version',
                            'commit',
                            'size',
                            'sha256',
                            'url',
                        ]
                    },
                )
            )

    scope = '-'.join(selected_devices)
    channel_scope = '-'.join(channels)
    report = (
        ROOT / 'docs' / 'releases'
        / f'{version}-{scope}-{channel_scope}-artifacts.json'
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(inventory, indent=2) + '\n',
        newline='\n',
    )

    print(json.dumps(inventory, indent=2))
    print(f'Artifact inventory: {report}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument(
        '--scale-root',
        type=Path,
        default=ROOT.parent / 'KegScaleESP',
    )
    parser.add_argument(
        '--display-root',
        type=Path,
        default=ROOT.parent / 'KegScaleESPDisplay',
    )
    parser.add_argument(
        '--device',
        action='append',
        choices=['scale', 'display', 'touchscreen'],
        dest='devices',
        help='Device to prepare. Repeat to select multiple. Default: all.',
    )
    parser.add_argument(
        '--channel',
        action='append',
        choices=['dev', 'production'],
        dest='channels',
        help='Channel to prepare. Repeat to select multiple. Default: both.',
    )
    args = parser.parse_args()

    prepare(
        args.version,
        args.scale_root.resolve(),
        args.display_root.resolve(),
        args.devices or ['scale', 'display', 'touchscreen'],
        args.channels or ['dev', 'production'],
    )
