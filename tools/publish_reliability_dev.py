#!/usr/bin/env python3
"""Publish the Nov 2026 Scale, e-Ink, and 4-inch Touchscreen Dev fixes.

Run from an activated ESP-IDF v6.0.1 terminal on the computer that holds
~/.kegscale/ota-keys/ota-dev-private.pem:

    python tools/publish_reliability_dev.py --publish

Builds immutable, verified Dev SHAs in temporary Git worktrees, signs each
version separately using the existing trusted key, validates each feed, stages
only the twelve Dev release files and publishes one atomic Git commit.
Does NOT access or modify TouchPour, the Waveshare 4.3-inch test firmware,
or Beta/Production feeds. Never uploads a private key.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {
    "scale": {
        "repository": "KegScaleESP",
        "ref": "4aa5f80f5f908d76e16cceae98a1b80e234567e6",
        "version": "V1.4.20",
        "version_file": "version.txt",
        "subproject": ".",
        "family": "firmware",
        "target": "esp32s3",
        "binary": "keg_scale_esp",
    },
    "display": {
        "repository": "KegScaleESPDisplay",
        "ref": "7ece7353f2239cf9218f6d1a8456cc28724de513",
        "version": "V1.4.7",
        "version_file": "version.txt",
        "subproject": ".",
        "family": "display",
        "target": "esp32",
        "binary": "keg_scale_display",
    },
    "touchscreen": {
        "repository": "KegScaleESPDisplay",
        "ref": "7ece7353f2239cf9218f6d1a8456cc28724de513",
        "version": "V1.4.24",
        "version_file": "touchscreen/version.txt",
        "subproject": "touchscreen",
        "family": "touchscreen",
        "target": "esp32s3",
        "binary": "keg_scale_touchscreen",
    },
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def command(args: list[str | Path], *, cwd: Path, capture: bool = False) -> str:
    words = [str(arg) for arg in args]
    print("+", " ".join(words), f"(in {cwd})", flush=True)
    proc = subprocess.run(
        words,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        check=True,
    )
    return proc.stdout.strip() if capture else ""


def git(repo: Path, *args: str, capture: bool = False) -> str:
    return command(["git", *args], cwd=repo, capture=capture)


def idf_build_command() -> list[str]:
    """Launch ESP-IDF through Python (direct idf.py raises WinError 193 on Windows)."""
    idf_root = os.environ.get("IDF_PATH", "").strip()
    if idf_root:
        idf_script = Path(idf_root) / "tools" / "idf.py"
    else:
        located = shutil.which("idf.py")
        idf_script = Path(located) if located else Path("__missing_idf__.py")
    check(
        idf_script.is_file() and idf_script.suffix.lower() == ".py",
        "Cannot locate ESP-IDF tools/idf.py. Open an activated ESP-IDF v6.0.1 "
        "PowerShell terminal and retry. "
        f"IDF_PATH={idf_root or '<unset>'!r}",
    )
    return [sys.executable, str(idf_script), "build"]


def preflight(key: Path, sources: dict[str, Path]) -> None:
    check(key.is_file(),
          f"Cannot sign Dev OTA: private key not found at {key}. "
          "Run this on the Windows computer containing your current OTA keys.")
    check(git(ROOT, "branch", "--show-current", capture=True) == "main",
          "KegScaleFirmware must be checked out on main.")
    git(ROOT, "fetch", "--no-tags", "origin", "main")
    current = git(ROOT, "rev-parse", "HEAD", capture=True)
    remote = git(ROOT, "rev-parse", "FETCH_HEAD", capture=True)
    check(current == remote,
          "KegScaleFirmware is not at origin/main. Run git pull --ff-only "
          "in KegScaleFirmware and rerun; local modifications will be preserved.")
    check(not git(ROOT, "diff", "--cached", "--name-only", capture=True),
          "The firmware repository has existing staged files. "
          "Unstage or commit them before publication (including Beta/Production).")

    for repo_name, path in sources.items():
        check((path / ".git").exists(), f"Missing source checkout: {path}")
        git(path, "fetch", "--no-tags", "origin", "dev")
        remote_sha = git(path, "rev-parse", "FETCH_HEAD", capture=True)
        expected = SOURCE["scale" if repo_name == "KegScaleESP" else "display"]["ref"]
        check(remote_sha == expected,
              f"{repo_name} Dev changed from validated commit {expected} to "
              f"{remote_sha}. Refuse to publish unreviewed code.")

    expected_files = expected_release_files()
    for name in expected_files:
        local_state = git(ROOT, "status", "--porcelain", "--", name,
                          capture=True)
        check(not local_state,
              f"Dev release file already modified locally: {name}; "
              "inspect it before publishing.")


def expected_release_files() -> list[str]:
    files = []
    for device, spec in SOURCE.items():
        directory = f"{spec['family']}/dev/{spec['target']}"
        files += [
            f"{directory}/manifest.json",
            f"{directory}/manifest.sig",
            f"{directory}/{spec['binary']}-{spec['ref'][:12]}.bin",
            f"docs/releases/{spec['version']}-{device}-dev-artifacts.json",
        ]
    return files


def validate_version(worktree: Path, spec: dict[str, str]) -> None:
    actual = (worktree / spec["version_file"]).read_text().strip()
    check(actual == spec["version"],
          f"{worktree}: expected {spec['version']} in {spec['version_file']}, "
          f"got {actual!r}")


def prepare(device: str, spec: dict[str, str],
            scale_root: Path, display_root: Path) -> None:
    command(
        [
            sys.executable,
            ROOT / "tools" / "prepare_release.py",
            "--version", spec["version"],
            "--device", device,
            "--channel", "dev",
            "--require-signatures",
            "--scale-root", scale_root,
            "--display-root", display_root,
        ],
        cwd=ROOT,
    )


def verify(device: str, spec: dict[str, str],
           scale_root: Path, display_root: Path) -> None:
    command(
        [
            sys.executable,
            ROOT / "tools" / "release_check.py",
            "--channel", "dev",
            "--device", device,
            "--version", spec["version"],
            "--source-check",
            "--skip-docs",
            "--scale-root", scale_root,
            "--display-root", display_root,
        ],
        cwd=ROOT,
    )
    file = ROOT / spec["family"] / "dev" / spec["target"] / "manifest.json"
    manifest = json.loads(file.read_text(encoding="utf-8"))
    check(manifest["commit"] == spec["ref"],
          f"Source provenance mismatch for {device}")
    check(manifest["version"] == spec["version"],
          f"Firmware version mismatch for {device}")
    check(manifest["channel"] == "dev",
          f"Refusing a non-Dev channel for {device}")
    print(f"PASS {device}: {manifest['version']} signed, source checked, "
          f"SHA-256 validated ({manifest['size']} bytes)", flush=True)


def stage_and_publish() -> None:
    expected = expected_release_files()
    git(ROOT, "add", "--", *expected)
    staged = git(ROOT, "diff", "--cached", "--name-only", capture=True)
    staged_set = set(staged.splitlines())
    check(staged_set == set(expected),
          "Unexpected staged files or no-op publication. "
          f"Staged={sorted(staged_set)!r}; expected={sorted(expected)!r}")
    git(ROOT, "diff", "--cached", "--check")
    print("\nChanges ready for one Dev-only commit:")
    print(staged)
    git(ROOT, "commit", "-m",
        "Publish Scale V1.4.20, e-Ink V1.4.7 and Touchscreen V1.4.24 Dev [skip ci]")
    git(ROOT, "push", "origin", "main")
    print("\nPASS: All three signed Dev feeds published atomically.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true",
                        help="After validation, commit and push only the three "
                             "signed Dev releases to origin/main.")
    parser.add_argument("--signing-key-dir", type=Path,
                        help="Directory containing ota-dev-private.pem; "
                             "defaults to KEGSCALE_OTA_KEY_DIR or "
                             "~/.kegscale/ota-keys.")
    args = parser.parse_args()
    if not args.publish:
        parser.error("Publication is explicit: pass --publish to build, sign, "
                     "validate and push the three Dev feeds.")

    signing_dir = args.signing_key_dir or Path(
        os.environ.get("KEGSCALE_OTA_KEY_DIR",
                       str(Path.home() / ".kegscale" / "ota-keys"))
    )
    signing_key = signing_dir.expanduser().resolve() / "ota-dev-private.pem"
    sources = {
        name: ROOT.parent / name
        for name in ("KegScaleESP", "KegScaleESPDisplay")
    }
    preflight(signing_key, sources)

    # In particular, do not change the user's checked-out Waveshare test
    # branch or dirty local source files.
    with tempfile.TemporaryDirectory(prefix="keg-reliability-dev-") as folder:
        worktrees = {
            "KegScaleESP": Path(folder) / "scale",
            "KegScaleESPDisplay": Path(folder) / "display",
        }
        added = []
        try:
            for name, path in worktrees.items():
                source = sources[name]
                ref = SOURCE["scale" if name == "KegScaleESP" else "display"]["ref"]
                git(source, "worktree", "add", "--detach", str(path), ref)
                added.append((source, path))

            for device, spec in SOURCE.items():
                worktree = worktrees[spec["repository"]]
                validate_version(worktree, spec)
                build_path = worktree / spec["subproject"]
                print(f"\nBuilding {device} {spec['version']} "
                      f"from {spec['ref'][:12]} ...", flush=True)
                command(idf_build_command(), cwd=build_path)

            scale_root = worktrees["KegScaleESP"]
            display_root = worktrees["KegScaleESPDisplay"]
            for device, spec in SOURCE.items():
                prepare(device, spec, scale_root, display_root)
            for device, spec in SOURCE.items():
                verify(device, spec, scale_root, display_root)

            stage_and_publish()
        finally:
            for source, path in reversed(added):
                subprocess.run(["git", "worktree", "remove", "--force", str(path)],
                               cwd=str(source), check=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"\nFAIL: Dev release was not confirmed published: {exc}",
              file=sys.stderr)
        raise SystemExit(1)
