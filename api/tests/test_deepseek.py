"""DeepSeek nativo: catálogo, ruta explícita y paso de sanitize. Sin red."""
from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ASKBOX_MASTER_KEY", "00" * 32)

from pydantic import ValidationError  # noqa: E402

from app.llm.errors import LLMError  # noqa: E402
from app.llm.keypool import KeyHandle  # noqa: E402
from app.llm.keypool import _system_key, candidates  # noqa: E402
from app.llm.ollama_route import parse_explicit_provider  # noqa: E402
from app.llm.providers import deepseek  # noqa: E402
from app.llm.router import PROVIDER_ORDER, AskFailure, ask  # noqa: E402
from app.routers.ask import AskIn  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_MIGRATION = Path(__file__).resolve().parents[1] / "app" / "migrations" / "018_deepseek_provider.sql"


def _load_sanitize():
    path = _REPO / "openclaw" / "sanitize-proxy" / "app.py"
    spec = importlib.util.spec_from_file_location("sanitize_deepseek_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"no se pudo cargar {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class CatalogTests(unittest.TestCase):
    def test_migration_registers_models_and_check(self):
        sql = _MIGRATION.read_text()
        self.assertIn("CHECK (provider IN ('gemini', 'openrouter', 'deepseek'))", sql)
        self.assertIn("('deepseek', 'deepseek-chat',     'M'", sql)
        self.assertIn("('deepseek', 'deepseek-reasoner', 'L'", sql)
        self.assertIn("0.2700", sql)
        self.assertIn("1.1000", sql)
        self.assertIn("0.5500", sql)
        self.assertIn("2.1900", sql)
        self.assertIn("ON CONFLICT (provider, slug, tier) DO UPDATE", sql)
        inserted = sql.split("VALUES", 1)[1]
        self.assertNotIn("deepseek-r1", inserted)

    def test_provider_order_keeps_gemini_first(self):
        self.assertEqual(PROVIDER_ORDER[0], "gemini")
        self.assertIn("deepseek", PROVIDER_ORDER)
        self.assertLess(PROVIDER_ORDER.index("deepseek"), PROVIDER_ORDER.index("openrouter"))


class ParseTests(unittest.TestCase):
    def test_provider_alone_defaults_to_chat(self):
        self.assertEqual(parse_explicit_provider("deepseek", None), ("deepseek", "deepseek-chat"))
        self.assertEqual(parse_explicit_provider("DeepSeek", "auto"), ("deepseek", "deepseek-chat"))

    def test_model_forces_provider(self):
        self.assertEqual(parse_explicit_provider(None, "deepseek-chat"), ("deepseek", "deepseek-chat"))
        self.assertEqual(parse_explicit_provider(None, "deepseek-reasoner"), ("deepseek", "deepseek-reasoner"))
        self.assertEqual(
            parse_explicit_provider(None, "deepseek/deepseek-chat"),
            ("deepseek", "deepseek-chat"),
        )
        self.assertEqual(
            parse_explicit_provider(None, "askbox/deepseek-reasoner"),
            ("deepseek", "deepseek-reasoner"),
        )

    def test_local_tag_stays_ollama(self):
        self.assertEqual(
            parse_explicit_provider("ollama", "deepseek-r1:14b"),
            ("ollama", "deepseek-r1:14b"),
        )
        self.assertEqual(
            parse_explicit_provider(None, "ollama/deepseek-r1:14b"),
            ("ollama", "deepseek-r1:14b"),
        )
        with self.assertRaises(ValueError):
            parse_explicit_provider(None, "deepseek-r1:14b")

    def test_unknown_deepseek_model_rejected(self):
        with self.assertRaises(ValueError):
            parse_explicit_provider("deepseek", "deepseek-v4-pro")

    def test_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            parse_explicit_provider("ollama", "deepseek-chat")
        with self.assertRaises(ValueError):
            parse_explicit_provider("gemini", "deepseek-reasoner")


class AskPayloadTests(unittest.TestCase):
    def test_shapes(self):
        chat = AskIn(prompt="hola", provider="deepseek")
        self.assertEqual((chat.provider, chat.model), ("deepseek", "deepseek-chat"))
        reasoner = AskIn(prompt="hola", model="deepseek-reasoner")
        self.assertEqual((reasoner.provider, reasoner.model), ("deepseek", "deepseek-reasoner"))
        prefixed = AskIn(prompt="hola", model="deepseek/deepseek-chat")
        self.assertEqual((prefixed.provider, prefixed.model), ("deepseek", "deepseek-chat"))

    def test_local_slug_still_needs_ollama(self):
        with self.assertRaises(ValidationError):
            AskIn(prompt="hola", model="deepseek-r1:14b")
        local = AskIn(prompt="hola", provider="ollama", model="deepseek-r1:14b")
        self.assertEqual((local.provider, local.model), ("ollama", "deepseek-r1:14b"))


class KeyTests(unittest.IsolatedAsyncioTestCase):
    def test_system_env_name(self):
        with patch("app.llm.keypool.settings.system_deepseek_key", "sk-deepseek-test"):
            self.assertEqual(_system_key("deepseek"), "sk-deepseek-test")
        with patch("app.llm.keypool.settings.system_deepseek_key", ""):
            self.assertEqual(_system_key("deepseek"), "")

    async def test_allow_system_keys_appends_env(self):
        class Pool:
            async def fetch(self, *args):
                return []

        project = {"id": "p1", "allow_system_keys": True}
        with patch("app.llm.keypool.settings.system_deepseek_key", "sk-sys"):
            keys = await candidates(Pool(), project, "deepseek")
        self.assertEqual([(k.source, k.secret, k.provider) for k in keys], [("system", "sk-sys", "deepseek")])

        project["allow_system_keys"] = False
        with patch("app.llm.keypool.settings.system_deepseek_key", "sk-sys"):
            self.assertEqual(await candidates(Pool(), project, "deepseek"), [])


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_chat_completions(self):
        seen = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                seen["timeout"] = kwargs.get("timeout")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, json=None):
                seen["url"] = url
                seen["headers"] = headers
                seen["json"] = json
                return _FakeResp(
                    200,
                    {
                        "choices": [{"message": {"content": " hola ", "reasoning_content": "pienso"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    },
                )

        with (
            patch("app.llm.providers.deepseek.settings.deepseek_base_url", "https://api.deepseek.com"),
            patch("app.llm.providers.deepseek.settings.deepseek_timeout", 120.0),
            patch("app.llm.providers.deepseek.httpx.AsyncClient", FakeClient),
        ):
            out = await deepseek.complete(
                [{"role": "user", "content": "di hola"}],
                api_key="sk-test",
                model="deepseek-chat",
                max_tokens=64,
            )
        self.assertEqual(seen["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(seen["json"]["model"], "deepseek-chat")
        self.assertEqual(out["text"], "hola")
        self.assertNotIn("pienso", out["text"])
        self.assertEqual(out["input_tokens"], 3)

    def test_base_url_suffixes(self):
        self.assertEqual(
            deepseek.chat_completions_url("https://api.deepseek.com"),
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(
            deepseek.chat_completions_url("https://api.deepseek.com/v1"),
            "https://api.deepseek.com/v1/chat/completions",
        )

    async def test_http_error(self):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, json=None):
                return _FakeResp(401, {"error": {"message": "invalid api key"}})

        with patch("app.llm.providers.deepseek.httpx.AsyncClient", FakeClient):
            with self.assertRaises(LLMError) as ctx:
                await deepseek.complete(
                    [{"role": "user", "content": "x"}],
                    api_key="sk-bad",
                    model="deepseek-chat",
                    max_tokens=8,
                )
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertNotIn("sk-bad", str(ctx.exception))


class RouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.project = {
            "id": "p1",
            "cache_ttl_seconds": 0,
            "default_tier": "auto",
            "allow_system_keys": False,
        }

    async def test_explicit_model_skips_cascade(self):
        complete = AsyncMock(
            return_value={"text": "desde deepseek", "model": "deepseek-reasoner", "input_tokens": 4, "output_tokens": 6}
        )
        cascade = AsyncMock(side_effect=AssertionError("la cascada no debe ejecutarse"))
        gemini = AsyncMock(side_effect=AssertionError("gemini"))
        handle = KeyHandle("deepseek", "sk-test", "k1", "project")
        with (
            patch("app.llm.router.deepseek.complete", complete),
            patch("app.llm.router._cascade", cascade),
            patch("app.llm.router.gemini.complete", gemini),
            patch("app.llm.router.keypool.candidates", AsyncMock(return_value=[handle])),
            patch("app.llm.router.keypool.mark_success", AsyncMock()),
            patch("app.llm.router.catalog.cost_for_model", AsyncMock(return_value=(0.55, 2.19))),
        ):
            res = await ask(
                None,
                None,
                project=self.project,
                messages=[{"role": "user", "content": "razona"}],
                tier="auto",
                max_tokens=128,
                model="deepseek-reasoner",
            )
        cascade.assert_not_called()
        gemini.assert_not_called()
        self.assertEqual(res["provider"], "deepseek")
        self.assertEqual(res["model_used"], "deepseek-reasoner")
        self.assertEqual(res["text"], "desde deepseek")
        self.assertEqual(res["key_source"], "project")
        self.assertEqual(res["models_chain"], ["deepseek:deepseek-reasoner"])
        self.assertEqual(complete.await_args.kwargs["model"], "deepseek-reasoner")
        self.assertEqual(complete.await_args.kwargs["api_key"], "sk-test")

    async def test_provider_alone_uses_chat(self):
        complete = AsyncMock(
            return_value={"text": "chat", "model": "deepseek-chat", "input_tokens": 1, "output_tokens": 1}
        )
        cascade = AsyncMock(side_effect=AssertionError("cascada"))
        handle = KeyHandle("deepseek", "sk-test", None, "system")
        with (
            patch("app.llm.router.deepseek.complete", complete),
            patch("app.llm.router._cascade", cascade),
            patch("app.llm.router.keypool.candidates", AsyncMock(return_value=[handle])),
            patch("app.llm.router.keypool.mark_success", AsyncMock()),
            patch("app.llm.router.catalog.cost_for_model", AsyncMock(return_value=(0.27, 1.1))),
        ):
            res = await ask(
                None,
                None,
                project=self.project,
                messages=[{"role": "user", "content": "hola"}],
                tier="balanced",
                max_tokens=32,
                provider="deepseek",
            )
        cascade.assert_not_called()
        self.assertEqual(res["model_used"], "deepseek-chat")
        self.assertEqual(res["key_source"], "system")

    async def test_missing_key(self):
        cascade = AsyncMock(side_effect=AssertionError("sin clave no hay cascada"))
        with (
            patch("app.llm.router._cascade", cascade),
            patch("app.llm.router.keypool.candidates", AsyncMock(return_value=[])),
            patch("app.llm.router.deepseek.complete", AsyncMock(side_effect=AssertionError("sin clave no se llama"))),
        ):
            with self.assertRaises(AskFailure) as ctx:
                await ask(
                    None,
                    None,
                    project=self.project,
                    messages=[{"role": "user", "content": "hola"}],
                    tier="fast",
                    max_tokens=32,
                    provider="deepseek",
                    model="deepseek-chat",
                )
        cascade.assert_not_called()
        self.assertEqual(ctx.exception.code, "no_provider_key")

    async def test_provider_failure_does_not_fall_through(self):
        complete = AsyncMock(side_effect=LLMError("deepseek caído", kind="retry"))
        cascade = AsyncMock(side_effect=AssertionError("no caer a la cascada"))
        handle = KeyHandle("deepseek", "sk-test", "k1", "project")
        with (
            patch("app.llm.router.deepseek.complete", complete),
            patch("app.llm.router._cascade", cascade),
            patch("app.llm.router.keypool.candidates", AsyncMock(return_value=[handle])),
            patch("app.llm.router.keypool.mark_quota", AsyncMock()),
        ):
            with self.assertRaises(AskFailure) as ctx:
                await ask(
                    None,
                    None,
                    project=self.project,
                    messages=[{"role": "user", "content": "hola"}],
                    tier="strong",
                    max_tokens=32,
                    model="deepseek/deepseek-chat",
                )
        cascade.assert_not_called()
        self.assertEqual(ctx.exception.code, "provider_failed")
        self.assertIn("deepseek caído", str(ctx.exception))


class SanitizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.san = _load_sanitize()

    def test_models_list_includes_cloud_ids(self):
        self.assertIn("deepseek-chat", self.san.ASKBOX_MODELS)
        self.assertIn("deepseek-reasoner", self.san.ASKBOX_MODELS)
        self.assertIn("deepseek-r1:14b", self.san.ASKBOX_MODELS)

    def test_explicit_deepseek_skips_split(self):
        code = {"messages": [{"role": "user", "content": "arregla este ```python\nbug\n```"}]}
        slug, reason = self.san.resolve_chat_model({**code, "model": "deepseek-chat"})
        self.assertEqual((slug, reason), ("deepseek-chat", "explicit_deepseek"))
        slug, reason = self.san.resolve_chat_model({**code, "model": "deepseek/deepseek-reasoner"})
        self.assertEqual((slug, reason), ("deepseek-reasoner", "explicit_deepseek"))
        slug, reason = self.san.resolve_chat_model({**code, "model": "allin", "provider": "deepseek"})
        self.assertEqual((slug, reason), ("deepseek-chat", "explicit_deepseek"))

    def test_split_defaults_unchanged(self):
        code = {"messages": [{"role": "user", "content": "arregla este ```python\nbug\n```"}]}
        chat = {"messages": [{"role": "user", "content": "hola, qué tal"}]}
        self.assertEqual(self.san.resolve_chat_model({**code, "model": "allin"})[0], self.san.DEFAULT_LOCAL_MODEL)
        self.assertEqual(self.san.resolve_chat_model({**chat, "model": "allin"})[0], "cloud-fast")
        self.assertEqual(
            self.san.resolve_chat_model({**code, "model": "cloud-strong"}),
            ("cloud-strong", "explicit_cloud"),
        )
        # El tag local no se promueve a la API de DeepSeek; el SPLIT lo reescribe.
        self.assertEqual(
            self.san.resolve_chat_model({**chat, "model": "deepseek-r1:14b"})[0],
            "cloud-fast",
        )
        self.assertEqual(
            self.san.resolve_chat_model({**code, "model": "ollama/deepseek-r1:14b"})[0],
            self.san.DEFAULT_LOCAL_MODEL,
        )

    def test_to_askin_passes_deepseek_without_lean_trim(self):
        long = "x" * 4000
        out = self.san._to_askin(
            {
                "model": "askbox/deepseek-chat",
                "messages": [{"role": "user", "content": long}],
                "max_tokens": 256,
            }
        )
        self.assertEqual(out["provider"], "deepseek")
        self.assertEqual(out["model"], "deepseek-chat")
        self.assertNotIn("tier", out)
        self.assertGreater(len(out["prompt"]), 3000)

        local = self.san._to_askin(
            {
                "model": "qwen2.5-coder:14b",
                "messages": [{"role": "user", "content": long}],
            }
        )
        self.assertEqual(local["provider"], "ollama")
        self.assertLess(len(local["prompt"]), 2000)


if __name__ == "__main__":
    unittest.main()
