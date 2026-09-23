#!/usr/bin/env python3
"""Prepare dev/production artifacts from clean, locally validated source checkouts.

Does not commit, push, tag, or delete older images. Run after all three builds pass.
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
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()

def prepare(version, scale, display):
    for repo in [scale, display]:
        if git(repo, 'status', '--porcelain'):
            raise SystemExit(f'Source checkout must be clean: {repo}')
    devices = [
        ('firmware', 'esp32s3', scale, scale/'build-local-v132', 'keg_scale_esp', 9),
        ('display', 'esp32', display, display/'build-local-v132', 'keg_scale_display', 0),
        ('touchscreen', 'esp32s3', display, display/'touchscreen/build-local-v132', 'keg_scale_touchscreen', 9),
    ]
    prepared = []
    # Validate every image before changing any manifest.
    for family, target, repo, build, app, chip in devices:
        data = (build/(app+'.bin')).read_bytes()
        if data[0] != 0xE9 or struct.unpack_from('<H', data, 12)[0] != chip:
            raise SystemExit(f'Unexpected image target: {app}')
        if struct.unpack_from('<I', data, 32)[0] != 0xABCD5432:
            raise SystemExit(f'Missing ESP-IDF app descriptor: {app}')
        actual_version = data[48:80].split(b'\0')[0].decode()
        actual_app = data[80:112].split(b'\0')[0].decode()
        if actual_version != version or actual_app != app:
            raise SystemExit(f'Image identity mismatch: {app}: {actual_version}, {actual_app}')
        commit = git(repo, 'rev-parse', 'HEAD')
        template = json.loads((ROOT/family/'dev'/target/'manifest.json').read_text())
        prepared.append((family,target,app,data,commit,template))
    inventory = []
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for family,target,app,data,commit,template in prepared:
        for channel in ['dev','production']:
            directory = ROOT/family/channel/target
            directory.mkdir(parents=True,exist_ok=True)
            name = f'{app}-{commit[:12]}.bin'
            image = directory/name
            if image.exists() and image.read_bytes() != data:
                raise SystemExit(f'Refusing to replace a different commit-named image: {image}')
            image.write_bytes(data)
            manifest = dict(template,version=version,commit=commit,
                            branch='main' if channel=='production' else 'dev',
                            channel=channel,target=target,size=len(data),
                            sha256=hashlib.sha256(data).hexdigest(),published_at=stamp,
                            url='https://raw.githubusercontent.com/khrisperry/KegScaleFirmware/main/'+image.relative_to(ROOT).as_posix())
            (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',newline='\n')
            inventory.append(dict(device=family,channel=channel,**{key:manifest[key] for key in ['version','commit','size','sha256','url']}))
    report = ROOT/'docs/releases'/f'{version}-artifacts.json'
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(inventory,indent=2)+'\n',newline='\n')
    print(json.dumps(inventory,indent=2))

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',required=True)
    parser.add_argument('--scale-root',type=Path,default=ROOT.parent/'KegScaleESP')
    parser.add_argument('--display-root',type=Path,default=ROOT.parent/'KegScaleESPDisplay')
    args=parser.parse_args()
    prepare(args.version,args.scale_root.resolve(),args.display_root.resolve())
