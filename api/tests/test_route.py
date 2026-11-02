"""POST /v1/route: perfiles fijos y nube simulada (sin red ni BD)."""
import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("ASKBOX_MASTER_KEY", "00" * 32)

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.llm.local_route import PROFILES, fallback_id, pack, parse_choice  # noqa: E402
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
        self.assertEqual((lean["keep_messages"], lean["system"]), (2, "lean"))
        self.assertGreaterEqual(lean["max_out"], 64)
        self.assertLessEqual(lean["max_out"], 128)
        self.assertEqual(standard["keep_messages"], 4)
        self.assertIn(standard["system"], ("summary", "lean"))
        self.assertEqual(standard["max_out"], 128)
        self.assertGreaterEqual(full["keep_messages"], 8)
        self.assertLessEqual(full["keep_messages"], 12)
        self.assertIn(full["system"], ("full", "summary"))
        self.assertEqual(full["max_out"], 256)

    def test_fallback_prefers_lean(self):
        self.assertEqual(fallback_id(["full", "lean", "standard"]), "lean")
        self.assertEqual(fallback_id(["full", "standard"]), "standard")

    def test_parse_json_fence_and_word(self):
        allowed = ["lean", "standard", "full"]
        self.assertEqual(
            parse_choice('```json\n{"profile":"full","reason":"code task"}\n```', allowed),
            ("full", "code task"),
        )
        self.assertEqual(parse_choice("use full now", allowed), ("full", ""))
        self.assertIsNone(parse_choice('{"profile":"huge"}', allowed))
        self.assertIsNone(parse_choice('{"profile":"full"}', ["lean", "standard"]))

    def test_pack_collapses_reason(self):
        out = pack("lean", "  timeout \n now ")
        self.assertEqual(out["reason"], "timeout now")
        self.assertEqual(out["max_out"], PROFILES["lean"]["max_out"])


class ChooseProfileTests(unittest.TestCase):
    def test_cloud_profile_is_mapped_not_tuned(self):
        async def cloud(_messages):
            self.assertIn('"has_code":true', _messages[-1]["content"])
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

    def test_timeout_is_lean(self):
        async def cloud(_messages):
            await asyncio.sleep(1)
            return '{"profile":"full","reason":"too late"}'

        out = asyncio.run(route_mod.choose_profile(_signals(), cloud, 0.05))
        self.assertEqual(out, pack("lean", "timeout"))

    def test_cloud_error_and_garbage_stay_lean(self):
        async def boom(_messages):
            raise RuntimeError("down")

        err = asyncio.run(route_mod.choose_profile(_signals(), boom, 1.0))
        self.assertEqual(err["profile"], "lean")
        self.assertEqual(err["reason"], "cloud_unavailable")

        async def garbage(_messages):
            return "no idea"

        bad = asyncio.run(route_mod.choose_profile(_signals(), garbage, 1.0))
        self.assertEqual(bad["reason"], "invalid_profile")
        self.assertEqual(bad["profile"], "lean")


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

    def test_no_project_returns_lean(self):
        res = self.client.post(
            "/v1/route",
            headers={"Authorization": "Bearer route-test-token"},
            json=_signals(),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["profile"], "lean")
        self.assertEqual(res.json()["reason"], "cloud_unavailable")
        self.assertEqual(res.json()["system"], "lean")

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

        async def fake_cloud(_pool, _redis, _project, messages):
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

    def test_slow_cloud_returns_lean_200(self):
        async def fake_project(_pool, _caller):
            return {"id": "p", "default_tier": "fast", "cache_ttl_seconds": 0, "allow_system_keys": True}

        async def slow(_pool, _redis, _project, _messages):
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
        self.assertEqual(res.json()["profile"], "lean")
        self.assertEqual(res.json()["reason"], "timeout")
        self.assertEqual(res.json()["keep_messages"], 2)


if __name__ == "__main__":
    unittest.main()
