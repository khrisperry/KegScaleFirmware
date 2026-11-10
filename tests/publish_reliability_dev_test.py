#!/usr/bin/env python3
"""Fast, hardware-free tests for the Windows-safe Dev publisher.

These must pass before another expensive three-firmware local release.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import publish_reliability_dev as publisher


class PublishReliabilityDevTest(unittest.TestCase):
    def test_idf_launcher_uses_python_and_valid_idf_action(self):
        with tempfile.TemporaryDirectory() as temp:
            tools = Path(temp) / "tools"
            tools.mkdir()
            (tools / "idf.py").write_text("# fake idf\n")
            with patch.dict(os.environ, {"IDF_PATH": temp}):
                self.assertEqual(
                    publisher.idf_command("reconfigure"),
                    [sys.executable, str(tools / "idf.py"), "reconfigure"],
                )
                self.assertEqual(
                    publisher.idf_command("build"),
                    [sys.executable, str(tools / "idf.py"), "build"],
                )
                with self.assertRaisesRegex(RuntimeError, "Unsupported IDF action"):
                    publisher.idf_command("-j")

    def test_touchscreen_uses_native_ninja_jobs_not_invalid_idf_option(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "build").mkdir()
            (project / "build/keg_scale_touchscreen.bin").write_bytes(b"image")
            with patch.object(publisher, "idf_command",
                              side_effect=lambda action: ["python", "idf.py", action]), \
                 patch.object(publisher, "command",
                              side_effect=lambda args, **kwargs: calls.append((args, kwargs))):
                publisher.build_firmware("touchscreen", project)
        self.assertEqual(
            [entry[0] for entry in calls],
            [["python", "idf.py", "reconfigure"],
             ["ninja", "-C", "build", "-j", "2"]],
        )
        for cmd, opts in calls:
            self.assertEqual(opts["env_overrides"]["CCACHE_DISABLE"], "1")
            if cmd[0] == "python":
                self.assertNotIn("-j", cmd)

    def test_touchscreen_retries_one_job_after_compiler_failure(self):
        calls = []

        def fake_command(args, **kwargs):
            calls.append(list(args))
            if list(args) == ["ninja", "-C", "build", "-j", "2"]:
                raise subprocess.CalledProcessError(1, args)

        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "build").mkdir()
            (project / "build/keg_scale_touchscreen.bin").write_bytes(b"image")
            with patch.object(publisher, "idf_command",
                              return_value=["python", "idf.py", "reconfigure"]), \
                 patch.object(publisher, "command", side_effect=fake_command):
                publisher.build_firmware("touchscreen", project)
        self.assertEqual(
            calls,
            [["python", "idf.py", "reconfigure"],
             ["ninja", "-C", "build", "-j", "2"],
             ["ninja", "-C", "build", "-j", "1"]],
        )

    def test_touchscreen_failed_retry_must_stop_release(self):
        def fail_ninja(args, **kwargs):
            if args[0] == "ninja":
                raise subprocess.CalledProcessError(1, args)

        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "build").mkdir()
            with patch.object(publisher, "idf_command",
                              return_value=["python", "idf.py", "reconfigure"]), \
                 patch.object(publisher, "command", side_effect=fail_ninja):
                with self.assertRaises(subprocess.CalledProcessError):
                    publisher.build_firmware("touchscreen", project)

    def test_other_targets_use_regular_idf_build(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(publisher, "idf_command",
                              return_value=["python", "idf.py", "build"]), \
                 patch.object(publisher, "command",
                              side_effect=lambda args, **kwargs: calls.append(args)):
                publisher.build_firmware("scale", Path(temp))
                publisher.build_firmware("display", Path(temp))
        self.assertEqual(calls, [["python", "idf.py", "build"]] * 2)

    def test_only_three_dev_feeds_are_staged(self):
        targets = set(publisher.expected_release_files())
        self.assertEqual(len(targets), 12)
        self.assertTrue(all("/dev/" in name or name.startswith("docs/releases/") for name in targets))
        self.assertFalse(any("touchpour" in name.lower() for name in targets))
        self.assertFalse(any("/production/" in name or "/beta/" in name for name in targets))


if __name__ == "__main__":
    unittest.main()
