"""Ollama explícito: sin red. La cascada Gemini/OpenRouter no se invoca."""
from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ASKBOX_MASTER_KEY", "00" * 32)

from pydantic import ValidationError  # noqa: E402

from app.llm import cache  # noqa: E402
from app.llm.errors import LLMError  # noqa: E402
from app.llm.ollama_route import parse_explicit_provider  # noqa: E402
from app.llm.providers import ollama  # noqa: E402
from app.llm.router import AskFailure, ask  # noqa: E402
from app.routers.ask import AskIn  # noqa: E402


class RouteParseTests(unittest.TestCase):
    def test_absent_keeps_cascade(self):
        self.assertIsNone(parse_explicit_provider(None, None))
        self.assertIsNone(parse_explicit_provider("  ", ""))

    def test_provider_without_model_is_auto(self):
        self.assertEqual(parse_explicit_provider("Ollama", None), ("ollama", "auto"))
        self.assertEqual(parse_explicit_provider("ollama", "auto"), ("ollama", "auto"))
        self.assertEqual(parse_explicit_provider(None, "ollama/auto"), ("ollama", "auto"))

    def test_prefix_implies_provider(self):
        self.assertEqual(
            parse_explicit_provider(None, "ollama/qwen2.5-coder:14b"),
            ("ollama", "qwen2.5-coder:14b"),
        )

    def test_provider_and_model(self):
        self.assertEqual(
            parse_explicit_provider("ollama", "llama3.2:3b"),
            ("ollama", "llama3.2:3b"),
        )

    def test_model_without_provider_rejected(self):
        with self.assertRaises(ValueError):
            parse_explicit_provider(None, "llama3.2:3b")

    def test_other_provider_rejected(self):
        with self.assertRaises(ValueError):
            parse_explicit_provider("gemini", None)

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            parse_explicit_provider("ollama", "../secret")


class AskPayloadTests(unittest.TestCase):
    def test_plain_prompt_unchanged(self):
        body = AskIn(prompt="hola")
        self.assertIsNone(body.provider)
        self.assertIsNone(body.model)
        self.assertEqual(body.tier, "auto")

    def test_provider_without_model_stays_auto(self):
        body = AskIn(prompt="hola", provider="ollama")
        self.assertEqual(body.provider, "ollama")
        self.assertEqual(body.model, "auto")
        self.assertIsNone(body.mode)

    def test_prefixed_model(self):
        body = AskIn(prompt="hola", model="ollama/qwen2.5-coder:14b")
        self.assertEqual((body.provider, body.model), ("ollama", "qwen2.5-coder:14b"))

    def test_bare_model_422(self):
        with self.assertRaises(ValidationError):
            AskIn(prompt="hola", model="qwen2.5-coder:14b")


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class OllamaClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_openai_chat_without_auth(self):
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
                        "model": json["model"],
                        "choices": [{"message": {"content": " hola "}}],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                    },
                )

        with (
            patch("app.llm.providers.ollama.settings.ollama_base_url", "http://ollama:11434"),
            patch("app.llm.providers.ollama.settings.ollama_timeout", 12.0),
            patch("app.llm.providers.ollama.httpx.AsyncClient", FakeClient),
        ):
            out = await ollama.complete(
                [{"role": "user", "content": "di hola"}],
                model="qwen2.5-coder:14b",
                max_tokens=32,
            )
        self.assertEqual(seen["url"], "http://ollama:11434/v1/chat/completions")
        self.assertNotIn("Authorization", seen["headers"])
        self.assertEqual(seen["json"]["model"], "qwen2.5-coder:14b")
        self.assertEqual(seen["timeout"], 12.0)
        self.assertEqual(out["text"], "hola")
        self.assertEqual(out["input_tokens"], 4)
        self.assertEqual(out["output_tokens"], 2)

    def test_base_url_with_v1_suffix(self):
        self.assertEqual(
            ollama.chat_completions_url("http://ollama:11434/v1/"),
            "http://ollama:11434/v1/chat/completions",
        )
        self.assertEqual(
            ollama.chat_completions_url("http://127.0.0.1:11434"),
            "http://127.0.0.1:11434/v1/chat/completions",
        )
        self.assertEqual(
            ollama.chat_completions_url("http://ollama:11434/v1/chat/completions"),
            "http://ollama:11434/v1/chat/completions",
        )

    async def test_http_error_is_llmerror(self):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, json=None):
                return _FakeResp(404, {"error": {"message": "model not found"}})

        with patch("app.llm.providers.ollama.httpx.AsyncClient", FakeClient):
            with self.assertRaises(LLMError) as ctx:
                await ollama.complete([{"role": "user", "content": "x"}], model="nope", max_tokens=8)
        self.assertIn("model not found", str(ctx.exception))

    async def test_attachments_rejected(self):
        with self.assertRaises(LLMError) as ctx:
            await ollama.complete(
                [{"role": "user", "content": "mira", "attachments": [object()]}],
                model="qwen2.5-coder:14b",
                max_tokens=8,
            )
        self.assertEqual(ctx.exception.kind, "fatal")


class RouterOllamaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.project = {"id": "p1", "cache_ttl_seconds": 0, "default_tier": "auto"}

    async def test_explicit_skips_cascade(self):
        complete = AsyncMock(
            return_value={"text": "desde ollama", "model": "qwen2.5-coder:14b", "input_tokens": 1, "output_tokens": 2}
        )
        cascade = AsyncMock(side_effect=AssertionError("la cascada no debe ejecutarse"))
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=self.project,
                messages=[{"role": "user", "content": "hola"}],
                tier="auto",
                max_tokens=64,
                provider="ollama",
                model="qwen2.5-coder:14b",
            )
        cascade.assert_not_called()
        complete.assert_awaited()
        self.assertEqual(res["provider"], "ollama")
        self.assertEqual(res["model_used"], "qwen2.5-coder:14b")
        self.assertEqual(res["text"], "desde ollama")
        self.assertEqual(res["tier_used"], "auto")
        self.assertEqual(res["key_source"], "local")
        self.assertEqual(res["estimated_cost_usd"], 0.0)
        self.assertFalse(res["cache_hit"])
        kwargs = complete.await_args.kwargs
        self.assertEqual(kwargs["model"], "qwen2.5-coder:14b")

    async def test_without_provider_uses_cascade(self):
        complete = AsyncMock(side_effect=AssertionError("ollama no debe llamarse"))
        cascade = AsyncMock(
            return_value={
                "text": "gemini",
                "provider": "gemini",
                "model_used": "m",
                "cache_hit": False,
                "tier_used": "fast",
                "attempts": 1,
                "key_id": None,
                "key_source": "system",
                "input_tokens": 1,
                "output_tokens": 1,
                "estimated_cost_usd": 0.0,
                "models_chain": ["gemini:m"],
            }
        )
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=self.project,
                messages=[{"role": "user", "content": "hola"}],
                tier="fast",
                max_tokens=32,
            )
        complete.assert_not_called()
        cascade.assert_awaited()
        self.assertEqual(res["provider"], "gemini")

    async def test_attachments_do_not_call_provider(self):
        complete = AsyncMock(side_effect=AssertionError("no debe llamar a Ollama"))
        with patch("app.llm.ollama_local.ollama.complete", complete):
            with self.assertRaises(AskFailure) as ctx:
                await ask(
                    None,
                    None,
                    project=self.project,
                    messages=[{"role": "user", "content": "mira"}],
                    tier="fast",
                    max_tokens=16,
                    provider="ollama",
                    model="qwen2.5-coder:14b",
                    normalized_attachments=[object()],
                )
        complete.assert_not_called()
        self.assertEqual(ctx.exception.code, "provider_failed")

    async def test_failure_does_not_fall_back(self):
        complete = AsyncMock(side_effect=LLMError("caído", kind="retry"))
        cascade = AsyncMock(side_effect=AssertionError("sin fallback"))
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            with self.assertRaises(AskFailure) as ctx:
                await ask(
                    None,
                    None,
                    project=self.project,
                    messages=[{"role": "user", "content": "hola"}],
                    tier="fast",
                    max_tokens=16,
                    provider="ollama",
                )
        cascade.assert_not_called()
        self.assertEqual(ctx.exception.code, "provider_failed")

    async def test_schema_mismatch_is_failure(self):
        complete = AsyncMock(return_value={"text": "no soy json", "model": "qwen2.5-coder:14b", "input_tokens": 0, "output_tokens": 1})
        with patch("app.llm.ollama_local.ollama.complete", complete):
            with self.assertRaises(AskFailure):
                await ask(
                    None,
                    None,
                    project=self.project,
                    messages=[{"role": "user", "content": "hola"}],
                    tier="fast",
                    max_tokens=16,
                    json_schema={"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
                    provider="ollama",
                    model="qwen2.5-coder:14b",
                )
        self.assertEqual(complete.await_count, 1)

    def test_cache_key_changes_with_route(self):
        plain = cache.cache_key("p", "user:hola", "fast", 32, "")
        routed = cache.cache_key("p", "user:hola", "fast", 32, "", route="ollama:qwen2.5-coder:14b")
        self.assertNotEqual(plain, routed)
        self.assertTrue(plain.startswith("askbox:cache:"))
