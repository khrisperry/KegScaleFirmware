#!/usr/bin/env python3
"""Offline, signed end-to-end publication tests using a local bare Git remote."""
import argparse
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'tools'))
import ota_signing
import publish_validated_dev as publisher


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def image(version, app):
    data = bytearray(512)
    data[0] = 0xE9
    struct.pack_into('<H', data, 12, 9)
    struct.pack_into('<I', data, 32, 0xABCD5432)
    for offset, text in [(48, version), (80, app)]:
        encoded = text.encode()
        data[offset:offset + len(encoded)] = encoded
    return bytes(data)


def init_repo(path):
    path.mkdir()
    git(path, 'init', '-b', 'main')
    git(path, 'config', 'user.name', 'OTA Test')
    git(path, 'config', 'user.email', 'ota-test@example.invalid')


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.feed = self.root / 'KegScaleFirmware'
        init_repo(self.feed)
        self.keys = self.root / 'private-keys'
        with contextlib.redirect_stdout(io.StringIO()):
            ota_signing.generate('dev', self.keys, self.feed / 'keys')
        shutil.copytree(SOURCE / 'tools', self.feed / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy(SOURCE / 'release_contract.json', self.feed / 'release_contract.json')
        self.plan = copy.deepcopy(publisher.CHECKPOINTS)
        self.sources = {}
        self.previous = {}
        contract = json.loads((self.feed / 'release_contract.json').read_text())
        for n, (device, spec) in enumerate(self.plan.items(), 1):
            source = self.root / spec['repo']
            init_repo(source)
            self.sources[device] = source
            version = source / spec['version_file']
            version.parent.mkdir(parents=True, exist_ok=True)
            version.write_text(spec['version'] + '\n')
            (source / '.gitignore').write_text('build/\n')
            git(source, 'add', '.')
            git(source, 'commit', '-m', 'Tested source')
            spec['commit'] = git(source, 'rev-parse', 'HEAD')
            build = source / spec['build']
            build.mkdir(parents=True)
            (build / (spec['app'] + '.bin')).write_bytes(image(spec['version'], spec['app']))
            folder = self.feed / spec['family'] / 'dev/esp32s3'
            folder.mkdir(parents=True)
            old_version = 'V1.4.7' if device == 'scale' else 'V1.4.14'
            old_commit = str(n) * 40
            name = spec['app'] + '-' + old_commit[:12] + '.bin'
            self.previous[device] = name
            data = image(old_version, spec['app'])
            (folder / name).write_bytes(data)
            for old in ['a' * 12, 'b' * 12]:
                (folder / (spec['app'] + '-' + old + '.bin')).write_bytes(b'obsolete fixture')
            dev_spec = contract['devices'][device]
            manifest = dict(version=old_version, commit=old_commit, branch='dev', channel='dev',
                            published_at='2026-10-15T23:42:49Z',
                            target='esp32s3', size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                            url=f'https://raw.githubusercontent.com/khrisperry/KegScaleFirmware/main/{spec["family"]}/dev/esp32s3/{name}',
                            **dev_spec['manifest_fields'])
            if 'hardware' in dev_spec:
                manifest['hardware'] = dev_spec['hardware']
            (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            ota_signing.sign_manifest(folder / 'manifest.json', 'dev', self.keys / 'ota-dev-private.pem',
                                      self.feed / 'keys/ota-dev-public-key.json', folder / 'manifest.sig')
        (self.feed / 'untouched.txt').write_text('local settings')
        for relative in ['firmware/production/esp32s3/keep.bin', 'touchscreen/beta/esp32s3/keep.bin',
                         'display/dev/esp32/keep.bin']:
            path = self.feed / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b'unchanged')
        git(self.feed, 'add', '.')
        git(self.feed, 'commit', '-m', 'Signed existing feed')
        self.initial = git(self.feed, 'rev-parse', 'HEAD')
        self.remote = self.root / 'remote.git'
        subprocess.check_call(['git', 'init', '--bare', '--initial-branch=main', str(self.remote)],
                              stdout=subprocess.DEVNULL)
        git(self.feed, 'remote', 'add', 'origin', str(self.remote))
        git(self.feed, 'push', '-u', 'origin', 'main')
        self.args = argparse.Namespace(scale_root=self.sources['scale'],
                                       display_root=self.sources['touchscreen'],
                                       signing_key_dir=self.keys, dry_run=False)

    def run_publish(self):
        with patch.object(publisher, 'ROOT', self.feed), patch.object(publisher, 'CHECKPOINTS', self.plan):
            publisher.publish(self.args)

    def test_signed_atomic_publish_and_scoped_retention(self):
        # A dirty/staged unrelated local file must survive the isolated publish.
        (self.feed / 'untouched.txt').write_text('uncommitted local settings')
        git(self.feed, 'add', 'untouched.txt')
        index_before = git(self.feed, 'diff', '--cached')
        self.run_publish()
        head = git(self.remote, 'rev-parse', 'main')
        self.assertNotEqual(head, self.initial)
        self.assertEqual(git(self.remote, 'rev-parse', 'main^'), self.initial)
        self.assertEqual(git(self.feed, 'diff', '--cached'), index_before)
        self.assertEqual((self.feed / 'untouched.txt').read_text(), 'uncommitted local settings')
        names = git(self.remote, 'ls-tree', '-r', '--name-only', 'main').splitlines()
        for device, spec in self.plan.items():
            prefix = spec['family'] + '/dev/esp32s3/'
            kept = {Path(n).name for n in names if n.startswith(prefix) and n.endswith('.bin')}
            self.assertEqual(kept, {self.previous[device], spec['app'] + '-' + spec['commit'][:12] + '.bin'})
            manifest = json.loads(git(self.remote, 'show', 'main:' + prefix + 'manifest.json'))
            self.assertEqual(manifest['commit'], spec['commit'])
            sig = json.loads(git(self.remote, 'show', 'main:' + prefix + 'manifest.sig'))
            metadata, public = ota_signing.load_public_metadata(self.feed / 'keys/ota-dev-public-key.json')
            payload = subprocess.check_output(['git', '-C', str(self.remote), 'show', 'main:' + prefix + 'manifest.json'])
            self.assertTrue(ota_signing.verify_bytes(ota_signing.public_key_from_raw(public), payload,
                                                    ota_signing.signature_bytes(sig)))
        for path in ['firmware/production/esp32s3/keep.bin', 'touchscreen/beta/esp32s3/keep.bin',
                     'display/dev/esp32/keep.bin']:
            self.assertEqual(git(self.remote, 'show', 'main:' + path), 'unchanged')

    def test_dry_run_does_not_publish(self):
        self.args.dry_run = True
        self.run_publish()
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), self.initial)
        self.assertEqual(len(git(self.feed, 'worktree', 'list', '--porcelain').split('worktree ')), 2)

    def test_missing_key_does_not_publish_or_prune(self):
        self.args.signing_key_dir = self.root / 'missing-key'
        with self.assertRaisesRegex(RuntimeError, 'signing key not found'):
            self.run_publish()
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), self.initial)

    def test_unrelated_touchpour_commits_and_dirty_work_are_preserved(self):
        source = self.sources['touchscreen']
        touchpour = source / 'touchpour'
        touchpour.mkdir()
        path = touchpour / 'ui.cpp'
        path.write_text('committed TouchPour work')
        git(source, 'add', 'touchpour')
        git(source, 'commit', '-m', 'Independent TouchPour work')
        path.write_text('staged TouchPour work')
        git(source, 'add', 'touchpour')
        path.write_text('unstaged TouchPour work')
        (touchpour / 'new-file.txt').write_text('untracked TouchPour work')
        status_before = git(source, 'status', '--porcelain')
        index_before = git(source, 'diff', '--cached')
        self.args.dry_run = True
        self.run_publish()
        self.assertEqual(git(source, 'status', '--porcelain'), status_before)
        self.assertEqual(git(source, 'diff', '--cached'), index_before)
        self.assertEqual(path.read_text(), 'unstaged TouchPour work')
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), self.initial)

    def test_dirty_touchscreen_source_is_still_rejected(self):
        path = self.sources['touchscreen'] / 'touchscreen/local.cpp'
        path.write_text('uncommitted selected project source')
        with self.assertRaisesRegex(RuntimeError, 'selected source/build inputs must be clean'):
            self.run_publish()
        self.assertEqual(path.read_text(), 'uncommitted selected project source')
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), self.initial)

    def test_shared_build_input_changes_are_still_rejected(self):
        source = self.sources['touchscreen']
        path = source / 'components/ble_client/local.c'
        path.parent.mkdir(parents=True)
        path.write_text('shared input')
        with self.assertRaisesRegex(RuntimeError, 'selected source/build inputs must be clean'):
            self.run_publish()
        git(source, 'add', 'components')
        git(source, 'commit', '-m', 'Shared input changed')
        with self.assertRaisesRegex(RuntimeError, 'build inputs changed since tested commit'):
            self.run_publish()
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), self.initial)

    def test_invalid_retained_name_prevents_deletion(self):
        folder = self.feed / 'firmware/dev/esp32s3'
        before = sorted(p.name for p in folder.iterdir())
        with self.assertRaises(RuntimeError):
            publisher.prune_old_dev_images(folder, {'../keep.bin', self.previous['scale']}, 'keg_scale_esp')
        self.assertEqual(sorted(p.name for p in folder.iterdir()), before)


if __name__ == '__main__':
    unittest.main()
