"""Tests de auto-update (git check, validación de stack, flags)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from app import auto_update, deployops


class AutoUpdateTests(unittest.TestCase):
    def test_is_askbox_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(auto_update.is_askbox_compose(tmp))
            with open(os.path.join(tmp, "docker-compose.yml"), "w", encoding="utf-8") as fh:
                fh.write("services:\n  api:\n  worker:\n  web:\n")
            self.assertTrue(auto_update.is_askbox_compose(tmp))

    def test_effective_enabled_env_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            with mock.patch.dict(os.environ, {"ASKBOX_AUTO_UPDATE": "false"}, clear=False):
                self.assertFalse(auto_update.effective_enabled(tmp))

    def test_run_skips_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            with open(os.path.join(tmp, "docker-compose.yml"), "w", encoding="utf-8") as fh:
                fh.write("services:\n  api:\n  worker:\n  web:\n")
            with mock.patch.dict(os.environ, {"ASKBOX_AUTO_UPDATE": "false"}, clear=False):
                result = auto_update.run_auto_update(tmp, dry_run=True)
            self.assertTrue(result.get("skipped"))
            self.assertEqual(result.get("skip_reason"), "disabled")

    def test_dry_run_behind_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmp,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "test"],
                cwd=tmp,
                check=True,
                capture_output=True,
            )
            compose = os.path.join(tmp, "docker-compose.yml")
            with open(compose, "w", encoding="utf-8") as fh:
                fh.write("services:\n  api:\n  worker:\n  web:\n")
            with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("v1\n")
            subprocess.run(["git", "add", "."], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)

            with tempfile.TemporaryDirectory() as remote_dir:
                subprocess.run(["git", "init", "-b", "main", "--bare"], cwd=remote_dir, check=True, capture_output=True)
                subprocess.run(["git", "remote", "add", "origin", remote_dir], cwd=tmp, check=True, capture_output=True)
                subprocess.run(["git", "push", "-u", "origin", "main"], cwd=tmp, check=True, capture_output=True)
                with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as fh:
                    fh.write("v2\n")
                subprocess.run(["git", "commit", "-am", "v2"], cwd=tmp, check=True, capture_output=True)
                subprocess.run(["git", "push", "origin", "main"], cwd=tmp, check=True, capture_output=True)
                subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=tmp, check=True, capture_output=True)

                os.makedirs(os.path.join(tmp, ".askbox"), exist_ok=True)
                deployops.save_preferences(tmp, {"auto_update_enabled": True})
                with mock.patch.dict(os.environ, {"ASKBOX_AUTO_UPDATE": "true"}, clear=False):
                    result = auto_update.run_auto_update(tmp, dry_run=True)
                self.assertTrue(result.get("ok"))
                self.assertTrue(result.get("updates_available"))
                self.assertIn("dry-run", result.get("message", ""))

    def test_record_auto_update_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".askbox"))
            deployops.record_auto_update_run(
                tmp,
                {"ok": True, "skipped": True, "message": "test", "dry_run": True, "started_at": "2020-01-01T00:00:00Z"},
            )
            prefs = deployops.load_preferences(tmp)
            self.assertEqual(prefs.get("auto_update_last_status"), "skipped")
            self.assertEqual(prefs.get("auto_update_last_message"), "test")


if __name__ == "__main__":
    unittest.main()
