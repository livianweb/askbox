import os
import tempfile
import unittest
from unittest import mock

from app import cf_tunnel, tunnel_apply  # noqa: E402


class CfTunnelHostnameTests(unittest.TestCase):
    def test_normalize_strips_scheme(self):
        self.assertEqual(cf_tunnel.normalize_hostname("https://askbox.example.com/"), "askbox.example.com")

    def test_zone_candidates(self):
        self.assertEqual(cf_tunnel.zone_name_candidates("askbox.softportweb.com"), ["askbox.softportweb.com", "softportweb.com"])

    def test_invalid_hostname(self):
        with self.assertRaises(cf_tunnel.CloudflareApiError):
            cf_tunnel.normalize_hostname("not a host")


class TunnelApplyEnvTests(unittest.TestCase):
    def test_merge_env_updates_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("FOO=bar\nASKBOX_PUBLIC_URL=http://localhost:8067\n")
            tunnel_apply.merge_env_file(path, {"ASKBOX_PUBLIC_URL": "https://x.test", "CLOUDFLARE_TUNNEL_TOKEN": "tok"})
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("ASKBOX_PUBLIC_URL=https://x.test", text)
            self.assertIn("CLOUDFLARE_TUNNEL_TOKEN=tok", text)
            self.assertIn("FOO=bar", text)
            self.assertNotIn("localhost:8067", text)


class ProvisionTunnelMockTests(unittest.IsolatedAsyncioTestCase):
    async def test_provision_happy_path(self):
        hostname = "askbox.example.com"
        tunnel_id = "11111111-2222-3333-4444-555555555555"

        async def fake_request(client, method, path, api_token, **kwargs):
            if path == "/user/tokens/verify":
                return {"success": True, "result": {"status": "active"}}
            if "/cfd_tunnel" in path and method == "GET" and not path.endswith("/token"):
                return {"success": True, "result": []}
            if path.endswith("/cfd_tunnel") and method == "POST":
                return {"success": True, "result": {"id": tunnel_id, "name": "askbox"}}
            if path.endswith("/configurations"):
                return {"success": True, "result": {}}
            if path.endswith("/token"):
                return {"success": True, "result": "connector-token-value-" + ("x" * 40)}
            if path == "/zones":
                return {"success": True, "result": [{"id": "zone1", "name": "example.com"}]}
            if "/dns_records" in path and method == "GET":
                return {"success": True, "result": []}
            if "/dns_records" in path and method == "POST":
                return {"success": True, "result": {"id": "dns1"}}
            if path == "/accounts?per_page=1":
                return {"success": True, "result": [{"id": "acc1"}]}
            raise AssertionError(f"unexpected {method} {path}")

        with mock.patch.object(cf_tunnel, "_request", fake_request):
            result = await cf_tunnel.provision_tunnel("api-token-" + ("a" * 30), hostname)
        self.assertEqual(result.tunnel_id, tunnel_id)
        self.assertEqual(result.hostname, hostname)
        self.assertTrue(result.connector_token.startswith("connector-token"))


if __name__ == "__main__":
    unittest.main()
