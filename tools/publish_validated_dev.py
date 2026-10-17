#!/usr/bin/env python3
"""Publish the hardware-tested Scale/Touch Dev checkpoint using local signing keys.

Run from the normal Windows ESP-IDF terminal. No rebuilding or key upload.
All feed changes, verification, pruning and commit work happen in an isolated
worktree; one fast-forward push publishes both devices atomically.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

from ota_signing import load_private_key, load_public_metadata, public_key_raw
from prepare_release import image_identity

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINTS = {
    'scale': dict(repo='KegScaleESP', version='V1.4.14',
                  commit='ff35afc935421e9a10b0feb7c5a58e550e95d698',
                  version_file='version.txt', build='build',
                  family='firmware', app='keg_scale_esp'),
    'touchscreen': dict(repo='KegScaleESPDisplay', version='V1.4.23',
                        commit='ddb9afad318493b9a9b311773e487fbeac935118',
                        version_file='touchscreen/version.txt', build='touchscreen/build',
                        family='touchscreen', app='keg_scale_touchscreen'),
}


def command(args, cwd=None, capture=False):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, check=True,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.rstrip('\r\n') if capture else None


def git(repo, *args, capture=True):
    return command(['git', '-C', repo, *args], capture=capture)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def manifest_image_name(manifest, family, app):
    prefix = f'https://raw.githubusercontent.com/khrisperry/KegScaleFirmware/main/{family}/dev/esp32s3/'
    url = manifest.get('url', '')
    require(isinstance(url, str) and url.startswith(prefix),
            f'Unexpected Dev artifact URL: {url!r}')
    parsed = urlparse(url)
    name = url[len(prefix):]
    require(not parsed.query and not parsed.fragment and
            re.fullmatch(re.escape(app) + r'-[0-9a-f]{12}\.bin', name),
            f'Unexpected Dev artifact filename: {name!r}')
    return name


def prune_old_dev_images(folder, keep_names, app):
    # Validate every retained name before deleting anything. Never follow links,
    # traverse other channels, or delete binaries belonging to a different app.
    pattern = re.compile(re.escape(app) + r'-[0-9a-f]{12}\.bin')
    require(len(keep_names) == 2, 'Retention requires the new and previous binary')
    for name in keep_names:
        require(pattern.fullmatch(name) and (folder / name).is_file() and
                not (folder / name).is_symlink(), f'Missing/invalid retained binary: {name}')
    removed = []
    candidates = sorted(folder.glob(app + '-*.bin'))
    for old in candidates:
        require(pattern.fullmatch(old.name) and not old.is_symlink(),
                f'Refusing unexpected firmware entry: {old}')
    for old in candidates:
        if old.name not in keep_names:
            old.unlink()
            removed.append(old.name)
    return removed


def inspect_sources(sources):
    snapshots = {}
    for device, spec in CHECKPOINTS.items():
        repo = sources[device]
        require(not git(repo, 'status', '--porcelain'), f'Source checkout must be clean: {repo}')
        head = git(repo, 'rev-parse', 'HEAD')
        require(head == spec['commit'],
                f'{device}: expected tested dev commit {spec["commit"]}, got {head}. '
                'Pull the dev branch before publishing; do not relabel an untested build.')
        require((repo / spec['version_file']).read_text().strip() == spec['version'],
                f'{device}: source version does not match the tested checkpoint')
        binary = repo / spec['build'] / (spec['app'] + '.bin')
        data = binary.read_bytes()
        require(image_identity(data) == (9, spec['version'], spec['app']),
                f'{device}: local image target/version/application mismatch: {binary}')
        require(len(data) <= (0x1F0000 if device == 'scale' else 0x600000),
                f'{device}: application exceeds its OTA slot')
        snapshots[device] = hashlib.sha256(data).hexdigest()
    return snapshots


def release_check(worktree, sources, source_check=False):
    args = [sys.executable, worktree / 'tools/release_check.py',
            '--device', 'scale', '--device', 'touchscreen', '--channel', 'dev', '--skip-docs']
    if source_check:
        args += ['--source-check', '--scale-root', sources['scale'],
                 '--display-root', sources['touchscreen']]
    command(args)


def publish(args):
    sources = {'scale': args.scale_root.resolve(), 'touchscreen': args.display_root.resolve()}
    snapshots = inspect_sources(sources)
    key_dir = (args.signing_key_dir or Path(os.environ.get(
        'KEGSCALE_OTA_KEY_DIR', str(Path.home() / '.kegscale/ota-keys')))).expanduser().resolve()
    private_path = key_dir / 'ota-dev-private.pem'
    require(private_path.is_file(), f'Dev signing key not found: {private_path}')
    private_key = load_private_key(private_path)
    git(ROOT, 'fetch', '--no-tags', 'origin', 'main', capture=False)
    base = git(ROOT, 'rev-parse', 'FETCH_HEAD')
    with tempfile.TemporaryDirectory(prefix='keg-dev-ota-') as temp:
        worktree = Path(temp) / 'firmware'
        added = False
        try:
            git(ROOT, 'worktree', 'add', '--detach', worktree, base, capture=False)
            added = True
            metadata, raw = load_public_metadata(worktree / 'keys/ota-dev-public-key.json')
            require(metadata['channel'] == 'dev' and public_key_raw(private_key.public_key()) == raw,
                    'Local Dev private key does not match the published verification key')
            previous = {}
            already_current = []
            current_versions = {}
            for device, spec in CHECKPOINTS.items():
                manifest = json.loads((worktree / spec['family'] / 'dev/esp32s3/manifest.json').read_text())
                current_versions[device] = manifest.get('version', '')
                previous[device] = manifest_image_name(manifest, spec['family'], spec['app'])
                already_current.append(manifest.get('version') == spec['version'] and
                                       manifest.get('commit') == spec['commit'] and
                                       manifest.get('sha256') == snapshots[device])
            release_check(worktree, sources)
            if all(already_current):
                print('PASS: Both tested Dev releases are already published; nothing changed.')
                return
            require(not any(already_current),
                    'Only one checkpoint is already published. Stop and inspect the feed before retrying.')
            def version_tuple(version):
                match = re.fullmatch(r'V(\d+)\.(\d+)\.(\d+)', version)
                require(match is not None, f'Invalid firmware version: {version!r}')
                return tuple(map(int, match.groups()))
            for device, spec in CHECKPOINTS.items():
                require(version_tuple(current_versions[device]) < version_tuple(spec['version']),
                        f'{device}: refusing equal-version replacement or downgrade from {current_versions[device]}')
            for device, spec in CHECKPOINTS.items():
                command([sys.executable, worktree / 'tools/prepare_release.py',
                         '--version', spec['version'], '--device', device, '--channel', 'dev',
                         '--source-commit', spec['commit'], '--scale-root', sources['scale'],
                         '--display-root', sources['touchscreen'], '--signing-key-dir', key_dir,
                         '--require-signatures'])
            # Detect local build/source changes during preparation before pruning/pushing.
            require(inspect_sources(sources) == snapshots, 'Source/build changed while preparing OTA')
            paths = []
            for device, spec in CHECKPOINTS.items():
                relative = Path(spec['family']) / 'dev/esp32s3'
                folder = worktree / relative
                manifest = json.loads((folder / 'manifest.json').read_text())
                current = manifest_image_name(manifest, spec['family'], spec['app'])
                require(manifest['sha256'] == snapshots[device], f'{device}: artifact changed during preparation')
                removed = prune_old_dev_images(folder, {current, previous[device]}, spec['app'])
                print(f'PASS: {device}: retaining {current} and {previous[device]}; removing {len(removed)} older Dev images')
                paths += [relative.as_posix(), f'docs/releases/{spec["version"]}-{device}-dev-artifacts.json']
            release_check(worktree, sources, source_check=True)
            changed = git(worktree, 'status', '--porcelain', '--untracked-files=all')
            for line in changed.splitlines():
                name = line[3:].replace('\\', '/')
                require(any(name == path or name.startswith(path + '/') for path in paths),
                        f'Unexpected change outside the two Dev releases: {name}')
            if args.dry_run:
                print('PASS: Dry run verified signing, artifacts and current-plus-previous retention; no push performed.')
                return
            git(worktree, 'add', '--', *paths, capture=False)
            git(worktree, 'commit', '-m',
                'Publish signed Scale V1.4.14 and Touch V1.4.23 Dev OTA; retain one previous [skip ci]',
                capture=False)
            # A concurrent feed update must cause a non-fast-forward rejection;
            # never force-push or rebase stale manifests/retention decisions.
            git(worktree, 'push', 'origin', 'HEAD:main', capture=False)
            print(f'PASS: Published both signed Dev releases atomically: {git(worktree, "rev-parse", "HEAD")}')
        finally:
            if added:
                git(ROOT, 'worktree', 'remove', '--force', worktree, capture=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scale-root', type=Path, default=ROOT.parent / 'KegScaleESP')
    parser.add_argument('--display-root', type=Path, default=ROOT.parent / 'KegScaleESPDisplay')
    parser.add_argument('--signing-key-dir', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    try:
        publish(parser.parse_args())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
