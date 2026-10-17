"""Tokens de servicio (tokens:write) y tokens de usuario externo. Sin red ni BD."""
import os
import unittest
import uuid
from datetime import datetime, timezone

os.environ.setdefault("ASKBOX_MASTER_KEY", "00" * 32)

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import http_error, validation_error  # noqa: E402
from app.project_auth import (  # noqa: E402
    DELEGATED_SCOPES,
    SERVICE_SCOPE,
    parse_delegated_scopes,
    parse_external_user_id,
    parse_scopes,
    has_scope,
)
from app.routers import access_tokens, delegated_tokens, projects  # noqa: E402
from app.security import token_hash  # noqa: E402

PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")
SERVICE = "ab_live_service-token-not-a-user-key"


class Row(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakePool:
    def __init__(self, service_scopes=None, project_id=PROJECT):
        self.calls = []
        self.service = None
        if service_scopes is not None:
            self.service = Row(
                id=uuid.uuid4(),
                project_id=project_id,
                scopes=service_scopes,
                user_id=uuid.uuid4(),
                revoked_at=None,
            )
        self.list_rows = []
        self.execute_result = "UPDATE 1"

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "JOIN projects" in sql:
            return self.service
        if "INSERT INTO project_access_tokens" in sql:
            now = datetime.now(timezone.utc)
            return Row(
                id=uuid.uuid4(),
                label=args[1],
                prefix=args[3],
                last4=args[6],
                scopes=args[4],
                external_user_id=args[5],
                created_at=now,
                last_used_at=None,
            )
        return None

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.list_rows

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return self.execute_result


def _app(pool) -> FastAPI:
    app = FastAPI()
    app.include_router(delegated_tokens.router)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.state.pool = pool
    return app


def _client(pool) -> TestClient:
    return TestClient(_app(pool))


class ScopeTests(unittest.TestCase):
    def test_owner_can_mint_service_scope(self):
        self.assertEqual(parse_scopes(["tokens:write"]), ["tokens:write"])

    def test_delegated_defaults_and_rejects_privilege(self):
        self.assertEqual(parse_delegated_scopes(None), ["ask:read"])
        self.assertEqual(parse_delegated_scopes(["usage:read", "ask:read"]), ["ask:read", "usage:read"])
        self.assertTrue(DELEGATED_SCOPES.isdisjoint({SERVICE_SCOPE, "keys:write", "prompts:write"}))
        for bad in (["tokens:write"], ["keys:write"], ["usage:read"], ["ask:read", "keys:write"], []):
            with self.assertRaises(HTTPException) as ctx:
                parse_delegated_scopes(bad)
            self.assertEqual(ctx.exception.detail["code"], "invalid_scopes")

    def test_service_scope_is_not_ask_or_keys(self):
        scopes = [SERVICE_SCOPE]
        self.assertFalse(has_scope(scopes, "ask:read"))
        self.assertFalse(has_scope(scopes, "keys:write"))
        self.assertFalse(has_scope(scopes, "usage:read"))

    def test_external_user_id(self):
        self.assertEqual(parse_external_user_id(" user-42 "), "user-42")
        parse_external_user_id("550e8400-e29b-41d4-a716-446655440000")
        with self.assertRaises(HTTPException):
            parse_external_user_id("tiene espacios")
        with self.assertRaises(HTTPException):
            parse_external_user_id("../etc")


class ExternalTokenApiTests(unittest.TestCase):
    def _auth(self, client, method, path, **kw):
        headers = {"Authorization": f"Bearer {SERVICE}"}
        headers.update(kw.pop("headers", {}))
        return getattr(client, method)(path, headers=headers, **kw)

    def test_create_returns_secret_once_and_only_user_scopes(self):
        pool = FakePool(["tokens:write"])
        client = _client(pool)
        res = self._auth(
            client,
            "post",
            f"/v1/projects/{PROJECT}/external-tokens",
            json={"external_user_id": "user-42", "label": "snake-os"},
        )
        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()
        token = body["token"]
        self.assertTrue(token.startswith("ab_live_"))
        self.assertNotEqual(token, SERVICE)
        self.assertEqual(body["scopes"], ["ask:read"])
        self.assertEqual(body["external_user_id"], "user-42")
        self.assertEqual(body["last4"], token[-4:])
        self.assertIn(token[:16], body["prefix"])
        self.assertIn("no se puede volver", body["note"])
        insert = [c for c in pool.calls if c[0] == "fetchrow" and "INSERT" in c[1]]
        self.assertEqual(len(insert), 1)
        sql, args = insert[0][1], insert[0][2]
        self.assertNotIn("api_keys", sql)
        self.assertNotIn(token, sql)
        self.assertEqual(args[2], token_hash(token))
        self.assertEqual(list(args[4]), ["ask:read"])
        self.assertEqual(args[5], "user-42")
        self.assertEqual(args[6], token[-4:])
        self.assertNotIn(token, sql)

    def test_optional_usage_scope_and_rejected_scopes(self):
        pool = FakePool(["tokens:write"])
        client = _client(pool)
        ok = self._auth(
            client,
            "post",
            f"/v1/projects/{PROJECT}/external-tokens",
            json={"external_user_id": "user-42", "scopes": ["usage:read", "ask:read"]},
        )
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["scopes"], ["ask:read", "usage:read"])
        for scopes in (["tokens:write"], ["keys:write"], ["prompts:write"], ["usage:read"]):
            bad = self._auth(
                client,
                "post",
                f"/v1/projects/{PROJECT}/external-tokens",
                json={"external_user_id": "user-42", "scopes": scopes},
            )
            self.assertEqual(bad.status_code, 422, scopes)
            self.assertEqual(bad.json()["error"]["code"], "invalid_scopes")

    def test_list_hides_secret(self):
        secret = "ab_live_this-must-not-appear-in-list"
        pool = FakePool(["tokens:write"])
        pool.list_rows = [
            Row(
                id=uuid.uuid4(),
                label="snake-os",
                prefix="ab_live_abcd1234…",
                last4="wxyz",
                scopes=["ask:read"],
                external_user_id="user-42",
                created_at=datetime.now(timezone.utc),
                last_used_at=None,
                revoked_at=None,
            )
        ]
        client = _client(pool)
        res = self._auth(client, "get", f"/v1/projects/{PROJECT}/external-tokens?external_user_id=user-42")
        self.assertEqual(res.status_code, 200, res.text)
        item = res.json()[0]
        self.assertEqual(item["prefix"], "ab_live_abcd1234…")
        self.assertEqual(item["last4"], "wxyz")
        self.assertNotIn("token", item)
        self.assertNotIn("token_hash", item)
        self.assertNotIn(secret, res.text)
        sql = pool.calls[-1][1]
        self.assertIn("external_user_id = $2", sql)
        self.assertNotIn("token_hash", sql)

    def test_list_all_delegated_excludes_service_rows(self):
        pool = FakePool(["tokens:write"])
        client = _client(pool)
        res = self._auth(client, "get", f"/v1/projects/{PROJECT}/external-tokens")
        self.assertEqual(res.status_code, 200, res.text)
        sql = [c for c in pool.calls if c[0] == "fetch"][-1][1]
        self.assertIn("external_user_id IS NOT NULL", sql)

    def test_revoke_cannot_target_rows_without_external_user(self):
        pool = FakePool(["tokens:write"])
        client = _client(pool)
        tid = uuid.uuid4()
        res = self._auth(client, "delete", f"/v1/projects/{PROJECT}/external-tokens/{tid}")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"ok": True})
        sql, args = [(c[1], c[2]) for c in pool.calls if c[0] == "execute"][-1]
        self.assertIn("external_user_id IS NOT NULL", sql)
        self.assertNotIn("api_keys", sql)
        self.assertEqual(args[0], tid)
        pool.execute_result = "UPDATE 0"
        missing = self._auth(client, "delete", f"/v1/projects/{PROJECT}/external-tokens/{tid}")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "not_found")

    def test_auth_and_project_binding(self):
        path = f"/v1/projects/{PROJECT}/external-tokens"
        open_client = _client(FakePool(None))
        self.assertEqual(open_client.get(path).status_code, 401)
        self.assertEqual(open_client.get(path, headers={"Authorization": "Bearer session-token"}).status_code, 401)
        cookie = open_client.get(path, cookies={"askbox_session": "web-session"})
        self.assertEqual(cookie.status_code, 401)

        user_token = _client(FakePool(["ask:read"]))
        forbidden = user_token.get(path, headers={"Authorization": f"Bearer {SERVICE}"})
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["error"]["code"], "forbidden")

        other = _client(FakePool(["tokens:write"], project_id=OTHER))
        mismatch = other.get(path, headers={"Authorization": f"Bearer {SERVICE}"})
        self.assertEqual(mismatch.status_code, 403)

        revoked = _client(FakePool(None))
        unknown = revoked.get(path, headers={"Authorization": f"Bearer {SERVICE}"})
        self.assertEqual(unknown.status_code, 401)


class ProviderKeysStayOnSessionTests(unittest.TestCase):
    def test_ab_live_bearer_cannot_read_or_write_provider_keys(self):
        pool = FakePool(["tokens:write", "keys:write"])
        app = FastAPI()
        app.include_router(projects.router)
        app.include_router(access_tokens.router)
        app.add_exception_handler(HTTPException, http_error)
        app.add_exception_handler(RequestValidationError, validation_error)
        app.state.pool = pool
        client = TestClient(app)
        pid = str(PROJECT)
        headers = {"Authorization": f"Bearer {SERVICE}"}
        secret = "provider-secret-value-not-stored"
        listed = client.get(f"/v1/projects/{pid}/keys", headers=headers)
        self.assertEqual(listed.status_code, 401)
        created = client.post(
            f"/v1/projects/{pid}/keys",
            headers=headers,
            json={"provider": "gemini", "key": secret},
        )
        self.assertEqual(created.status_code, 401)
        escalated = client.post(
            f"/v1/projects/{pid}/access-tokens",
            headers=headers,
            json={"label": "escalate", "scopes": ["tokens:write", "keys:write"]},
        )
        self.assertEqual(escalated.status_code, 401)
        joined = " ".join(c[1] for c in pool.calls)
        self.assertNotIn("api_keys", joined)
        self.assertNotIn(secret, joined)
        self.assertNotIn("ciphertext", joined)
        self.assertNotIn("INSERT INTO project_access_tokens", joined)


class RouteRegistrationTests(unittest.TestCase):
    def test_routes_on_app(self):
        from app.main import app

        paths = {getattr(r, "path", None) for r in app.routes}
        self.assertIn("/v1/projects/{project_id}/external-tokens", paths)
        self.assertIn("/v1/projects/{project_id}/external-tokens/{token_id}", paths)


if __name__ == "__main__":
    unittest.main()
