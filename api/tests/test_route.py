"""POST /v1/route: perfiles fijos y nube simulada (sin red ni BD)."""
import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("ASKBOX_MASTER_KEY", "00" * 32)

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.llm.errors import LLMError  # noqa: E402
from app.llm.local_route import PROFILES, fallback_id, order_route_models, pack, parse_choice  # noqa: E402
from app.llm.providers import gemini as gemini_mod  # noqa: E402
from app.routers import route as route_mod  # noqa: E402


def _signals(**over):
    base = {
        "last_user": "arregla el test de login",
        "n_messages": 9,
        "approx_chars": 4200,
        "has_code": True,
        "has_tools": False,
        "profiles": ["lean", "standard", "full"],
    }
    base.update(over)
    return base


class ProfileContractTests(unittest.TestCase):
    def test_frozen_tuples_match_sanitize_contract(self):
        lean, standard, full = PROFILES["lean"], PROFILES["standard"], PROFILES["full"]
        self.assertEqual((lean["keep_messages"], lean["system"]), (4, "lean"))
        self.assertGreaterEqual(lean["max_out"], 64)
        self.assertLessEqual(lean["max_out"], 128)
        self.assertEqual(standard["keep_messages"], 4)
        self.assertIn(standard["system"], ("summary", "lean"))
        self.assertEqual(standard["max_out"], 128)
        self.assertGreaterEqual(full["keep_messages"], 8)
        self.assertLessEqual(full["keep_messages"], 12)
        self.assertIn(full["system"], ("full", "summary"))
        self.assertEqual(full["max_out"], 256)

    def test_fallback_is_standard_not_lean(self):
        self.assertEqual(fallback_id(["full", "lean", "standard"]), "standard")
        self.assertEqual(fallback_id(["full", "lean"]), "full")
        self.assertEqual(fallback_id(["lean"]), "lean")
        self.assertEqual(pack(fallback_id(["lean", "standard", "full"]), "timeout")["keep_messages"], 4)

    def test_parse_json_fence_and_word(self):
        allowed = ["lean", "standard", "full"]
        self.assertEqual(
            parse_choice('```json\n{"profile":"full","reason":"code task"}\n```', allowed),
            ("full", "code task"),
        )
        self.assertEqual(parse_choice("use full now", allowed), ("full", ""))
        self.assertIsNone(parse_choice('{"profile":"huge"}', allowed))
        self.assertIsNone(parse_choice('{"profile":"full"}', ["lean", "standard"]))

    def test_order_prefers_latest_lite_and_drops_dead_slugs(self):
        ordered = order_route_models(
            [
                "gemini-1.5-flash-8b",
                "gemini-2.0-flash",
                "gemini-2.0-flash-lite",
                "gemini-2.5-flash",
                "gemini-2.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-flash-latest",
                "gemini-flash-lite-latest",
            ]
        )
        self.assertEqual(
            ordered[:4],
            [
                "gemini-flash-lite-latest",
                "gemini-2.5-flash-lite",
                "gemini-flash-latest",
                "gemini-3.1-flash-lite",
            ],
        )
        self.assertNotIn("gemini-2.0-flash-lite", ordered)
        self.assertNotIn("gemini-1.5-flash-8b", ordered)
        self.assertNotIn("gemini-2.5-flash", ordered)
        self.assertEqual(
            order_route_models([])[:2],
            ["gemini-flash-lite-latest", "gemini-flash-latest"],
        )

    def test_pack_collapses_reason(self):
        out = pack("lean", "  timeout \n now ")
        self.assertEqual(out["reason"], "timeout now")
        self.assertEqual(out["max_out"], PROFILES["lean"]["max_out"])


class ChooseProfileTests(unittest.TestCase):
    def test_cloud_profile_is_mapped_not_tuned(self):
        async def cloud(_messages):
            self.assertIn("code=1", _messages[-1]["content"])
            self.assertLess(len(_messages[0]["content"]) + len(_messages[-1]["content"]), 400)
            return '{"profile":"FULL","reason":"long code"}'

        out = asyncio.run(route_mod.choose_profile(_signals(), cloud, 1.0))
        self.assertEqual(out["profile"], "full")
        self.assertEqual(out["keep_messages"], PROFILES["full"]["keep_messages"])
        self.assertEqual(out["system"], PROFILES["full"]["system"])
        self.assertEqual(out["max_out"], PROFILES["full"]["max_out"])
        self.assertEqual(out["reason"], "long code")

    def test_model_knobs_do_not_override_the_tuple(self):
        async def cloud(_messages):
            return '{"profile":"lean","keep_messages":99,"max_out":9999,"system":"full","reason":"hi"}'

        out = asyncio.run(route_mod.choose_profile(_signals(), cloud, 1.0))
        self.assertEqual(out, pack("lean", "hi"))

    def test_timeout_is_standard(self):
        async def cloud(_messages):
            await asyncio.sleep(1)
            return '{"profile":"full","reason":"too late"}'

        out = asyncio.run(route_mod.choose_profile(_signals(), cloud, 0.05))
        self.assertEqual(out["profile"], "standard")
        self.assertEqual(out["reason"], "timeout")
        self.assertEqual(out["keep_messages"], 4)
        self.assertEqual(out["system"], "lean")

    def test_explicit_lean_still_wins(self):
        async def cloud(_messages):
            return '{"profile":"lean","reason":"trivial"}'

        out = asyncio.run(route_mod.choose_profile(_signals(), cloud, 1.0))
        self.assertEqual(out["profile"], "lean")
        self.assertEqual(out["keep_messages"], 4)

    def test_cloud_error_and_garbage_stay_standard(self):
        async def boom(_messages):
            raise RuntimeError("down")

        err = asyncio.run(route_mod.choose_profile(_signals(), boom, 1.0))
        self.assertEqual(err["profile"], "standard")
        self.assertEqual(err["reason"], "cloud_unavailable")
        self.assertEqual(err["keep_messages"], 4)
        self.assertEqual(err["system"], "lean")

        async def garbage(_messages):
            return "no idea"

        bad = asyncio.run(route_mod.choose_profile(_signals(), garbage, 1.0))
        self.assertEqual(bad["reason"], "invalid_profile")
        self.assertEqual(bad["profile"], "standard")


class RouteHttpTests(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"ASKBOX_TOKEN": "route-test-token"}, clear=False)
        self._env.start()
        app = FastAPI()

        @app.exception_handler(HTTPException)
        async def http_error(_, exc: HTTPException):
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
            return JSONResponse({"error": detail}, status_code=exc.status_code)

        app.include_router(route_mod.router)
        app.state.pool = None
        app.state.redis = None
        self.client = TestClient(app)

    def tearDown(self):
        self._env.stop()

    def test_no_project_returns_standard(self):
        res = self.client.post(
            "/v1/route",
            headers={"Authorization": "Bearer route-test-token"},
            json=_signals(),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["profile"], "standard")
        self.assertEqual(res.json()["reason"], "cloud_unavailable")
        self.assertEqual(res.json()["system"], "lean")
        self.assertEqual(res.json()["keep_messages"], 4)

    def test_missing_bearer_is_401(self):
        res = self.client.post("/v1/route", json=_signals())
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"]["code"], "unauthenticated")

    def test_unknown_profile_is_422(self):
        res = self.client.post(
            "/v1/route",
            headers={"Authorization": "Bearer route-test-token"},
            json=_signals(profiles=["tiny"]),
        )
        self.assertEqual(res.status_code, 422)

    def test_service_token_uses_mocked_cloud(self):
        async def fake_project(_pool, caller):
            self.assertEqual(caller["mode"], "service")
            return {"id": "00000000-0000-0000-0000-000000000001", "default_tier": "fast", "cache_ttl_seconds": 0, "allow_system_keys": True}

        async def fake_cloud(_pool, _redis, _project, messages, _attempt=None):
            self.assertEqual(messages[0]["role"], "system")
            return '{"profile":"standard","reason":"follow-up"}'

        with mock.patch.object(route_mod, "load_project", fake_project), mock.patch.object(
            route_mod, "cloud_text", fake_cloud
        ):
            res = self.client.post(
                "/v1/route",
                headers={"Authorization": "Bearer route-test-token"},
                json=_signals(has_code=False, n_messages=3, approx_chars=400),
            )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["profile"], "standard")
        self.assertEqual(body["keep_messages"], 4)
        self.assertEqual(body["system"], "summary")
        self.assertEqual(body["max_out"], 128)
        self.assertEqual(body["reason"], "follow-up")

    def test_default_timeout_stays_under_sanitize(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASKBOX_ROUTE_TIMEOUT_MS", None)
            self.assertAlmostEqual(route_mod.route_timeout_s(), 0.65)
        with mock.patch.dict(os.environ, {"ASKBOX_ROUTE_TIMEOUT_MS": "900"}, clear=False):
            self.assertAlmostEqual(route_mod.route_timeout_s(), 0.78)

    def test_cloud_text_calls_fastest_lite_once(self):
        seen = {}

        class Pool:
            async def fetch(self, *_args):
                return [
                    {"slug": "gemini-3.5-flash"},
                    {"slug": "gemini-3.5-flash-lite"},
                    {"slug": "gemini-2.5-flash-lite"},
                ]

        async def fake_keys(_pool, _project, provider):
            self.assertEqual(provider, "gemini")
            from app.llm.keypool import KeyHandle

            return [KeyHandle("gemini", "not-a-real-key", None, "system")]

        async def fake_complete(messages, **kwargs):
            seen["kwargs"] = kwargs
            seen["messages"] = messages
            return {"text": '{"profile":"full","reason":"ok"}', "model": kwargs["model"]}

        route_mod._MISSING.clear()
        project = {
            "id": "00000000-0000-0000-0000-000000000099",
            "default_tier": "fast",
            "cache_ttl_seconds": 0,
            "allow_system_keys": True,
        }
        route_mod._HOT.pop(str(project["id"]), None)
        with mock.patch.object(route_mod.keypool, "candidates", fake_keys), mock.patch.object(
            route_mod.gemini, "complete", fake_complete
        ):
            text = asyncio.run(
                route_mod.cloud_text(Pool(), None, project, [{"role": "user", "content": "x"}])
            )
        route_mod._HOT.pop(str(project["id"]), None)
        route_mod._MISSING.clear()
        self.assertIn("full", text)
        self.assertEqual(seen["kwargs"]["model"], "gemini-flash-lite-latest")
        self.assertEqual(seen["kwargs"]["max_tokens"], 32)
        self.assertTrue(seen["kwargs"]["fast"])
        self.assertEqual(seen["kwargs"]["temperature"], 0.0)
        self.assertEqual(seen["kwargs"]["json_schema"]["required"], ["profile"])
        self.assertEqual(seen["kwargs"]["json_schema"]["type"], "OBJECT")

    def test_404_skips_to_next_slug(self):
        route_mod._MISSING.clear()
        project = {
            "id": "00000000-0000-0000-0000-000000000088",
            "default_tier": "fast",
            "cache_ttl_seconds": 0,
            "allow_system_keys": True,
        }
        route_mod._HOT.pop(str(project["id"]), None)
        calls: list[str] = []

        class Pool:
            async def fetch(self, *_args):
                return [
                    {"slug": "gemini-2.5-flash-lite"},
                    {"slug": "gemini-2.0-flash"},
                ]

        async def fake_keys(_pool, _project, provider):
            self.assertEqual(provider, "gemini")
            from app.llm.keypool import KeyHandle

            return [KeyHandle("gemini", "not-a-real-key", None, "system")]

        async def fake_complete(_messages, **kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "gemini-flash-lite-latest":
                raise LLMError("not found", status=404, kind="retry")
            if "2.0" in kwargs["model"] or "8b" in kwargs["model"]:
                raise AssertionError(kwargs["model"])
            return {"text": '{"profile":"standard","reason":"ok"}', "model": kwargs["model"]}

        with mock.patch.object(route_mod.keypool, "candidates", fake_keys), mock.patch.object(
            route_mod.gemini, "complete", fake_complete
        ), self.assertLogs("askbox.route", level="INFO") as logs:
            text = asyncio.run(
                route_mod.cloud_text(Pool(), None, project, [{"role": "user", "content": "x"}])
            )
        route_mod._HOT.pop(str(project["id"]), None)
        route_mod._MISSING.clear()
        self.assertIn("standard", text)
        self.assertEqual(calls, ["gemini-flash-lite-latest", "gemini-2.5-flash-lite"])
        self.assertIn("route gemini 404 model=gemini-flash-lite-latest", "\n".join(logs.output))

    def test_timeout_log_names_model(self):
        async def slow(_messages):
            await asyncio.sleep(1)
            return "{}"

        attempt = {"model": "gemini-2.5-flash-lite"}
        with self.assertLogs("askbox.route", level="INFO") as logs:
            out = asyncio.run(route_mod.choose_profile(_signals(), slow, 0.05, attempt))
        self.assertEqual(out["profile"], "standard")
        self.assertEqual(out["reason"], "timeout")
        joined = "\n".join(logs.output)
        self.assertIn("route timeout model=gemini-2.5-flash-lite", joined)
        self.assertIn("latency_ms=", joined)

    def test_slow_cloud_returns_standard_200(self):
        async def fake_project(_pool, _caller):
            return {"id": "p", "default_tier": "fast", "cache_ttl_seconds": 0, "allow_system_keys": True}

        async def slow(_pool, _redis, _project, _messages, _attempt=None):
            await asyncio.sleep(1)
            return '{"profile":"full","reason":"late"}'

        with mock.patch.dict(os.environ, {"ASKBOX_ROUTE_TIMEOUT_MS": "50"}, clear=False), mock.patch.object(
            route_mod, "load_project", fake_project
        ), mock.patch.object(route_mod, "cloud_text", slow):
            res = self.client.post(
                "/v1/route",
                headers={"Authorization": "Bearer route-test-token"},
                json=_signals(),
            )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["profile"], "standard")
        self.assertEqual(res.json()["reason"], "timeout")
        self.assertEqual(res.json()["keep_messages"], 4)
        self.assertEqual(res.json()["system"], "lean")


class _JsonResp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class ThinkingConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_latest_alias_uses_thinking_level(self):
        self.assertEqual(
            gemini_mod._thinking_off("gemini-flash-lite-latest"),
            {"thinkingLevel": "minimal"},
        )
        self.assertEqual(gemini_mod._thinking_off("gemini-flash-latest"), {"thinkingLevel": "minimal"})
        self.assertEqual(gemini_mod._thinking_off("gemini-2.0-flash"), {"thinkingBudget": 0})

    async def test_fast_400_retries_without_thinking_config(self):
        seen: list[dict] = []

        class FakeClient:
            is_closed = False

            def __init__(self, *args, **kwargs):
                pass

            async def post(self, url, headers=None, json=None):
                seen.append(
                    {
                        "generationConfig": dict(json["generationConfig"]),
                        "thinkingConfig": dict(json["generationConfig"].get("thinkingConfig") or {}),
                    }
                )
                if len(seen) == 1:
                    return _JsonResp(400, {"error": {"message": "Request contains an invalid argument."}})
                return _JsonResp(
                    200,
                    {
                        "candidates": [{"content": {"parts": [{"text": '{"profile":"standard","reason":"ok"}'}]}}],
                        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
                    },
                )

        gemini_mod._fast_client = None
        with mock.patch.object(gemini_mod.httpx, "AsyncClient", FakeClient):
            out = await gemini_mod.complete(
                [{"role": "user", "content": "n=1"}],
                api_key="not-a-real-key",
                model="gemini-flash-lite-latest",
                max_tokens=32,
                temperature=0.0,
                fast=True,
                timeout=1.0,
            )
        gemini_mod._fast_client = None
        self.assertEqual(out["text"], '{"profile":"standard","reason":"ok"}')
        self.assertNotIn("responseMimeType", seen[0]["generationConfig"])
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["thinkingConfig"], {"thinkingLevel": "minimal"})
        self.assertEqual(seen[1]["thinkingConfig"], {})

    async def test_json_schema_sets_response_mime_and_skips_thinking(self):
        seen: list[dict] = []

        class FakeClient:
            is_closed = False

            def __init__(self, *args, **kwargs):
                pass

            async def post(self, url, headers=None, json=None):
                cfg = dict(json["generationConfig"])
                seen.append(cfg)
                return _JsonResp(
                    200,
                    {
                        "candidates": [{"content": {"parts": [{"text": '{"profile":"lean","reason":"hi"}'}]}}],
                        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
                    },
                )

        schema = {
            "type": "OBJECT",
            "properties": {"profile": {"type": "STRING"}, "reason": {"type": "STRING"}},
            "required": ["profile"],
        }
        gemini_mod._fast_client = None
        with mock.patch.object(gemini_mod.httpx, "AsyncClient", FakeClient):
            out = await gemini_mod.complete(
                [{"role": "user", "content": "u=hola"}],
                api_key="not-a-real-key",
                model="gemini-flash-lite-latest",
                max_tokens=32,
                temperature=0.0,
                fast=True,
                timeout=1.0,
                json_schema=schema,
            )
        gemini_mod._fast_client = None
        self.assertEqual(seen[0]["responseMimeType"], "application/json")
        self.assertEqual(seen[0]["responseSchema"], schema)
        self.assertNotIn("thinkingConfig", seen[0])
        self.assertEqual(parse_choice(out["text"], ["lean", "standard", "full"]), ("lean", "hi"))
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
