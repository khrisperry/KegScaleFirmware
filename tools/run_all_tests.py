#!/usr/bin/env python3
"""Run every non-destructive Keg Scale regression suite from one command.

Intended for Windows PowerShell with WSL installed. Scale and Display host suites
run inside WSL; the Playwright UI regression and Firmware release/signing checks
run with the current Windows Python interpreter.

All emitted result lines begin with PASS: or FAIL:. Interactive terminals render
PASS in green and FAIL in red. Set NO_COLOR=1 or pass --no-color to disable ANSI
color without changing the prefixes.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARENT = ROOT.parent

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
_GREEN = "\033[32m" if _USE_COLOR else ""
_RED = "\033[31m" if _USE_COLOR else ""
_RESET = "\033[0m" if _USE_COLOR else ""


def set_color(enabled: bool) -> None:
    global _USE_COLOR, _GREEN, _RED, _RESET
    _USE_COLOR = enabled
    _GREEN = "\033[32m" if enabled else ""
    _RED = "\033[31m" if enabled else ""
    _RESET = "\033[0m" if enabled else ""


def status(prefix: str, message: str) -> None:
    color = _GREEN if prefix == "PASS" else _RED
    print(f"{color}{prefix}:{_RESET} {message}")


def pass_line(message: str) -> None:
    status("PASS", message)


def fail_line(message: str) -> None:
    status("FAIL", message)


def clean_line(line: str) -> str:
    return ANSI_RE.sub("", line).strip()


def emit_child_output(label: str, output: str, succeeded: bool) -> tuple[int, int]:
    pass_count = 0
    fail_count = 0
    fallback_prefix = "PASS" if succeeded else "FAIL"

    for raw in output.splitlines():
        line = clean_line(raw)
        if not line:
            continue
        if line.startswith("PASS:"):
            pass_line(line[len("PASS:"):].strip())
            pass_count += 1
        elif line.startswith("FAIL:"):
            fail_line(line[len("FAIL:"):].strip())
            fail_count += 1
        else:
            message = f"{label}: {line}"
            if fallback_prefix == "PASS":
                pass_line(message)
                pass_count += 1
            else:
                fail_line(message)
                fail_count += 1

    return pass_count, fail_count


def run_suite(
    label: str,
    command: list[str],
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> tuple[bool, int, int]:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    if extra_env:
        env.update(extra_env)

    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )
    except OSError as exc:
        fail_line(f"{label}: unable to start: {exc}")
        return False, 0, 1

    succeeded = completed.returncode == 0
    pass_count, fail_count = emit_child_output(
        label, completed.stdout or "", succeeded
    )

    if succeeded:
        pass_line(f"{label} complete")
        pass_count += 1
    else:
        fail_line(f"{label} failed with exit code {completed.returncode}")
        fail_count += 1

    return succeeded, pass_count, fail_count


def wsl_executable() -> str | None:
    return shutil.which("wsl.exe") or shutil.which("wsl")


def windows_to_wsl_path(path: Path) -> str | None:
    """Convert a normal Windows drive path without passing backslashes to WSL."""

    value = str(path.resolve())
    match = re.match(r"^([A-Za-z]):[\\\\/](.*)$", value)
    if not match:
        fail_line(
            f"WSL path conversion supports Windows drive paths only; got {value}"
        )
        return None

    drive = match.group(1).lower()
    tail = match.group(2).replace("\\\\", "/")
    return f"/mnt/{drive}/{tail}"


def wsl_directory_available(wsl: str, windows_path: Path, linux_path: str) -> bool:
    quoted = linux_path.replace("'", "'\\''")
    completed = subprocess.run(
        [wsl, "bash", "-lc", f"test -d '{quoted}'"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    if completed.returncode == 0:
        pass_line(f"WSL path ready: {windows_path} -> {linux_path}")
        return True

    detail = clean_line(completed.stdout or "")
    suffix = f": {detail}" if detail else ""
    fail_line(
        f"WSL cannot access {windows_path} as {linux_path}{suffix}"
    )
    return False


def wsl_bash_command(wsl: str, repo: Path, script: str) -> list[str] | None:
    translated = windows_to_wsl_path(repo)
    if translated is None:
        return None
    if not wsl_directory_available(wsl, repo, translated):
        return None
    quoted = translated.replace("'", "'\\''")
    return [
        wsl,
        "bash",
        "-lc",
        f"cd '{quoted}' && NO_COLOR=1 bash {script}",
    ]


def require_directory(label: str, path: Path) -> bool:
    if not path.is_dir():
        fail_line(f"{label} repository missing: {path}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scale-root",
        type=Path,
        default=DEFAULT_PARENT / "KegScaleESP",
        help="Scale repository root.",
    )
    parser.add_argument(
        "--display-root",
        type=Path,
        default=DEFAULT_PARENT / "KegScaleESPDisplay",
        help="Display/Touch repository root.",
    )
    parser.add_argument(
        "--firmware-root",
        type=Path,
        default=ROOT,
        help="Firmware repository root.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors while keeping PASS:/FAIL: prefixes.",
    )
    args = parser.parse_args()

    if args.no_color or os.environ.get("NO_COLOR"):
        set_color(False)

    scale = args.scale_root.resolve()
    display = args.display_root.resolve()
    firmware = args.firmware_root.resolve()

    roots_ok = all(
        (
            require_directory("Scale", scale),
            require_directory("Display/Touch", display),
            require_directory("Firmware", firmware),
        )
    )
    if not roots_ok:
        fail_line("All-regression summary: repository preflight failed")
        return 1

    suites: list[tuple[str, list[str], Path]] = []

    if os.name == "nt":
        wsl = wsl_executable()
        if not wsl:
            fail_line("WSL is required for Scale and Display host regression suites")
            fail_line("All-regression summary: WSL preflight failed")
            return 1

        scale_cmd = wsl_bash_command(wsl, scale, "tests/run_host_tests.sh")
        display_cmd = wsl_bash_command(wsl, display, "tests/run_host_tests.sh")
        if scale_cmd is None or display_cmd is None:
            fail_line("All-regression summary: WSL path preflight failed")
            return 1
    else:
        scale_cmd = ["bash", "tests/run_host_tests.sh"]
        display_cmd = ["bash", "tests/run_host_tests.sh"]

    suites.extend(
        [
            ("Scale host regressions", scale_cmd, scale),
            (
                "Scale Playwright web/UI regressions",
                [sys.executable, str(scale / "tools" / "check_web_preview.py")],
                scale,
            ),
            ("Display/e-paper/Touch host regressions", display_cmd, display),
            (
                "Firmware signed release validation",
                [
                    sys.executable,
                    str(firmware / "tools" / "release_check.py"),
                    "--source-check",
                ],
                firmware,
            ),
            (
                "OTA signing self-test",
                [
                    sys.executable,
                    str(firmware / "tools" / "ota_signing.py"),
                    "self-test",
                ],
                firmware,
            ),
        ]
    )

    suite_passes = 0
    suite_failures = 0
    pass_lines = 0
    fail_lines = 0

    for label, command, cwd in suites:
        succeeded, passed, failed = run_suite(label, command, cwd)
        pass_lines += passed
        fail_lines += failed
        if succeeded:
            suite_passes += 1
        else:
            suite_failures += 1

    summary = (
        f"All-regression summary: {suite_passes}/{len(suites)} suites passed, "
        f"{suite_failures} suites failed, {pass_lines} PASS lines, "
        f"{fail_lines} FAIL lines"
    )
    if suite_failures:
        fail_line(summary)
        return 1

    pass_line(summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        fail_line("All-regression run interrupted")
        raise SystemExit(130)
