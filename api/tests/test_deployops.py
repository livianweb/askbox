import os
import tempfile
import unittest

from app import deployops  # noqa: E402


class DeployOpsTests(unittest.TestCase):
    def test_save_and_effective_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            deployops.save_preferences(tmp, {"public_url": "https://example.com/"})
            self.assertEqual(deployops.effective_public_url(tmp, "http://localhost:8067"), "https://example.com")
            self.assertTrue(deployops.effective_cookie_secure(tmp, False, "http://localhost:8067"))

    def test_env_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(deployops.effective_public_url(tmp, "http://a:1"), "http://a:1")


if __name__ == "__main__":
    unittest.main()
