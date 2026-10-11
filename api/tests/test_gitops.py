"""Tests sin red ni BD: python -m unittest discover -s tests (con PYTHONPATH=.)."""
import os
import shutil
import subprocess
import tempfile
import unittest

os.environ["ASKBOX_MASTER_KEY"] = "00" * 32  # clave ficticia solo para tests

from app import gitops  # noqa: E402


class SanitizeUrlTests(unittest.TestCase):
    def test_strips_embedded_credentials(self):
        self.assertEqual(
            gitops.sanitize_remote_url("https://x-access-token:ghp_abc123@github.com/u/r.git"),
            "https://github.com/u/r.git",
        )

    def test_leaves_plain_url_untouched(self):
        self.assertEqual(gitops.sanitize_remote_url("https://github.com/u/r.git"), "https://github.com/u/r.git")

    def test_handles_ssh_and_empty(self):
        self.assertEqual(gitops.sanitize_remote_url("git@github.com:u/r.git"), "git@github.com:u/r.git")
        self.assertEqual(gitops.sanitize_remote_url(""), "")


class ParseRemotesTests(unittest.TestCase):
    def test_parses_and_dedupes_fetch_push(self):
        raw = (
            "origin\thttps://x-access-token:secret@github.com/u/r.git (fetch)\n"
            "origin\thttps://x-access-token:secret@github.com/u/r.git (push)\n"
        )
        remotes = gitops.parse_remotes(raw)
        self.assertEqual(len(remotes), 2)
        self.assertTrue(all("secret" not in r["url"] for r in remotes))
        self.assertEqual({r["type"] for r in remotes}, {"fetch", "push"})

    def test_empty_input(self):
        self.assertEqual(gitops.parse_remotes(None), [])
        self.assertEqual(gitops.parse_remotes(""), [])


class PreferencesRoundtripTests(unittest.TestCase):
    def test_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = gitops.load_preferences(tmp)
            self.assertEqual(prefs["remote_name"], "origin")
            self.assertTrue(prefs["auto_commit"])
            self.assertIsNone(prefs["last_push_at"])

    def test_save_and_reload_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            gitops.save_preferences(tmp, {"auto_commit": False})
            gitops.save_preferences(tmp, {"remote_name": "upstream"})
            prefs = gitops.load_preferences(tmp)
            self.assertFalse(prefs["auto_commit"])
            self.assertEqual(prefs["remote_name"], "upstream")
            self.assertEqual(prefs["default_visibility"], "private")  # default conservado

    def test_mark_pushed_sets_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = gitops.mark_pushed(tmp)
            self.assertIsNotNone(prefs["last_push_at"])


class RepoAvailabilityTests(unittest.TestCase):
    def test_not_available_without_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(gitops.is_repo_available(tmp))
            status = gitops.git_status(tmp)
            self.assertFalse(status["available"])
            self.assertIn("reason", status)

    @unittest.skipUnless(shutil.which("git"), "git no instalado en este entorno de test (imagen slim)")
    def test_available_with_real_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "config", "user.email", "t@example.com"], check=True)
            subprocess.run(["git", "-C", tmp, "config", "user.name", "t"], check=True)
            readme = os.path.join(tmp, "README.md")
            with open(readme, "w", encoding="utf-8") as fh:
                fh.write("hola\n")
            subprocess.run(["git", "-C", tmp, "add", "README.md"], check=True)
            subprocess.run(["git", "-C", tmp, "commit", "-q", "-m", "inicial"], check=True)

            self.assertTrue(gitops.is_repo_available(tmp))
            status = gitops.git_status(tmp)
            self.assertTrue(status["available"])
            self.assertIsNotNone(status["last_commit"]["hash"])
            self.assertEqual(status["last_commit"]["subject"], "inicial")
            self.assertFalse(status["dirty"])

    @unittest.skipUnless(shutil.which("git"), "git no instalado en este entorno de test (imagen slim)")
    def test_set_remote_add_then_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            ok, err = gitops.set_remote(tmp, "origin", "https://example.com/a.git")
            self.assertTrue(ok, err)
            ok, err = gitops.set_remote(tmp, "origin", "https://example.com/b.git")
            self.assertTrue(ok, err)
            remotes = gitops.parse_remotes(subprocess.run(
                ["git", "-C", tmp, "remote", "-v"], capture_output=True, text=True, check=True,
            ).stdout)
            self.assertTrue(any(r["url"] == "https://example.com/b.git" for r in remotes))
