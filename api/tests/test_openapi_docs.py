"""Swagger/ReDoc detrás del proxy /api."""
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient


class OpenApiDocsProxyTest(unittest.TestCase):
    def test_swagger_uses_api_prefix_when_configured(self):
        with mock.patch.dict(os.environ, {"ASKBOX_API_EXTERNAL_PREFIX": "/api"}, clear=False):
            from importlib import reload

            import app.config as config_mod
            import app.main as main_mod

            reload(config_mod)
            reload(main_mod)
            client = TestClient(main_mod.app)
            r = client.get("/docs")
            self.assertEqual(r.status_code, 200)
            self.assertIn("'/api/openapi.json'", r.text)
            spec = client.get("/openapi.json").json()
            self.assertEqual(spec.get("servers"), [{"url": "/api"}])


if __name__ == "__main__":
    unittest.main()
