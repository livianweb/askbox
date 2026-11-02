"""GET /v1/ide/vscode-roo: playbook público, sin red ni BD."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ide import vscode_roo as playbook_mod


def _client() -> TestClient:
    app = FastAPI()
    from app.routers.ide import router

    app.include_router(router)
    return TestClient(app)


class IdeVscodeRooTests(unittest.TestCase):
    def test_json_has_agent_fields(self):
        res = _client().get("/v1/ide/vscode-roo")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["playbook"], "vscode-roo")
        self.assertEqual(body["version"], "1")
        self.assertTrue(body["base_url"].endswith("/v1"), body["base_url"])
        auth = body["auth_header"]
        self.assertEqual(auth["name"], "Authorization")
        self.assertEqual(auth["scheme"], "Bearer")
        self.assertEqual(auth["secret_env"], "SANITIZE_CLIENT_TOKEN")
        self.assertIn("${SANITIZE_CLIENT_TOKEN}", auth["template"])
        ids = [p["id"] for p in body["profiles"]]
        self.assertEqual(ids, ["askbox-cloud-fast", "askbox-cloud-strong", "askbox-local-qwen7b"])
        local = body["profiles"][-1]
        self.assertEqual(local["model"], "local-qwen7b")
        self.assertEqual(local["context_window"], 32768)
        self.assertEqual(local["max_output_tokens"], 4096)
        actions = [s["action"] for s in body["steps"]]
        for action in ("install_extension", "write_json", "merge_vscode_settings", "reload_window"):
            self.assertIn(action, actions)
        self.assertEqual(body["roo_import"]["providerProfiles"]["currentApiConfigName"], "askbox-cloud-strong")
        imported = body["roo_import"]["providerProfiles"]["apiConfigs"]["askbox-local-qwen7b"]
        self.assertEqual(imported["openAiModelId"], "local-qwen7b")
        self.assertEqual(imported["openAiCustomModelInfo"]["contextWindow"], 32768)
        self.assertEqual(imported["openAiCustomModelInfo"]["maxTokens"], 4096)
        self.assertEqual(imported["openAiApiKey"], "${SANITIZE_CLIENT_TOKEN}")
        self.assertIn("local-qwen7b", body["markdown"])
        self.assertTrue(body["markdown_url"].endswith("/v1/ide/vscode-roo.md"))
        blob = json.dumps(body)
        self.assertNotIn("AIza", blob)
        self.assertNotIn("sk-or-", blob)
        self.assertNotIn("sk-", blob)

    def test_markdown_route(self):
        res = _client().get("/v1/ide/vscode-roo.md")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/markdown", res.headers["content-type"])
        self.assertIn("Roo Code", res.text)
        self.assertIn("SANITIZE_CLIENT_TOKEN", res.text)

    def test_base_url_from_env(self):
        env = {
            "ASKBOX_PUBLIC_URL": "https://askbox.softportweb.com",
            "ASKBOX_IDE_OPENAI_BASE_URL": "https://askbox.softportweb.com/v1",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            body = _client().get("/v1/ide/vscode-roo").json()
        self.assertEqual(body["base_url"], "https://askbox.softportweb.com/v1")
        self.assertEqual(body["urls"]["playbook_json"], "https://askbox.softportweb.com/v1/ide/vscode-roo")
        self.assertEqual(
            body["urls"]["playbook_json_api_prefix"],
            "https://askbox.softportweb.com/api/v1/ide/vscode-roo",
        )

    def test_packaged_markdown_matches_docs(self):
        packaged = Path(playbook_mod.__file__).with_name("vscode_roo.md").read_text(encoding="utf-8")
        docs = Path(playbook_mod.__file__).resolve().parents[3] / "docs" / "ide" / "vscode-roo.md"
        self.assertEqual(packaged, docs.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
