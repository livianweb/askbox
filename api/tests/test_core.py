"""Tests sin red ni BD: python -m unittest discover -s tests (con PYTHONPATH=.)."""
import os
import unittest

os.environ["ASKBOX_MASTER_KEY"] = "00" * 32  # clave ficticia solo para tests

from app import crypto  # noqa: E402
from app.crypto import CryptoError  # noqa: E402
from app.llm.errors import classify  # noqa: E402
from app.llm.keypool import KeyHandle  # noqa: E402
from app.llm import cache  # noqa: E402
from app.llm import catalog  # noqa: E402
from app.llm.router import infer_tier_from_text, next_tier, prev_tier  # noqa: E402
from app.ask_exec import _response_from_res  # noqa: E402
from app.llm.schema_check import response_matches_schema  # noqa: E402
from app.security import hash_password, verify_password  # noqa: E402


class CryptoTests(unittest.TestCase):
    def test_roundtrip_and_aad(self):
        ct, nonce, ver = crypto.encrypt_secret("secret-value-1234", "proj-1", "gemini")
        self.assertNotIn(b"secret-value", ct)
        self.assertEqual(crypto.decrypt_secret(ct, nonce, ver, "proj-1", "gemini"), "secret-value-1234")
        with self.assertRaises(CryptoError):
            crypto.decrypt_secret(ct, nonce, ver, "proj-2", "gemini")

    def test_nonce_unique(self):
        a = crypto.encrypt_secret("x" * 10, "p", "gemini")
        b = crypto.encrypt_secret("x" * 10, "p", "gemini")
        self.assertNotEqual(a[1], b[1])


class AuthTests(unittest.TestCase):
    def test_confirm_mismatch_422(self):
        from fastapi import HTTPException

        from app.routers.auth import _check_confirm

        _check_confirm("abcdefgh", "abcdefgh")
        with self.assertRaises(HTTPException) as ctx:
            _check_confirm("abcdefgh", "abcdefgX")
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail["code"], "password_mismatch")


class MiscTests(unittest.TestCase):
    def test_password(self):
        h = hash_password("long-password-" * 10)
        self.assertTrue(verify_password("long-password-" * 10, h))
        self.assertFalse(verify_password("otra-password", h))
        self.assertFalse(verify_password("x", None))

    def test_classify(self):
        self.assertEqual(classify(429, "slow down"), "quota")
        self.assertEqual(classify(403, "Quota exceeded"), "quota")
        self.assertEqual(classify(400, "API key not valid"), "auth")
        self.assertEqual(classify(401, "User not found."), "auth")
        self.assertEqual(classify(503, "overloaded"), "retry")
        self.assertEqual(classify(400, "bad request"), "fatal")

    def test_tier_auto(self):
        self.assertEqual(infer_tier_from_text("hola", 256), "fast")
        self.assertEqual(infer_tier_from_text("```py\nx\n```", 256), "balanced")
        self.assertEqual(infer_tier_from_text("a" * 7000, 4096), "strong")
        self.assertEqual(infer_tier_from_text("hola", 256, "strong"), "strong")
        self.assertEqual(infer_tier_from_text("hola", 256, json_schema={"type": "object"}), "balanced")
        self.assertEqual(next_tier("fast"), "balanced")
        self.assertIsNone(next_tier("strong"))
        self.assertEqual(prev_tier("balanced"), "fast")
        self.assertIsNone(prev_tier("fast"))

    def test_peer_order_cross_provider(self):
        batch = [
            ("openrouter", {"slug": "b", "max_output_tokens": 4096, "priority": 20}),
            ("gemini", {"slug": "a", "max_output_tokens": 8192, "priority": 10}),
            ("opencode_zen", {"slug": "c", "max_output_tokens": 8192, "priority": 10}),
        ]
        ordered = catalog.order_peer_batch(("gemini", "openrouter", "opencode_zen"), batch)
        slugs = [(p, m["slug"]) for p, m in ordered]
        self.assertEqual(slugs[0], ("gemini", "a"))
        self.assertEqual(slugs[1][0], "opencode_zen")
        self.assertEqual(slugs[2], ("openrouter", "b"))

    def test_schema_and_cache_key(self):
        schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
        self.assertTrue(response_matches_schema('{"ok": true}', schema))
        self.assertFalse(response_matches_schema('{"ok": "yes"}', schema))
        a = cache.cache_key("p", "prompt", "fast", 512, "")
        b = cache.cache_key("p", "prompt", "fast", 512, '{"type":"object"}')
        self.assertNotEqual(a, b)

    def test_handle_repr_hides_secret(self):
        self.assertNotIn("topsecret", repr(KeyHandle("gemini", "topsecret", None, "system")))

    def test_stream_response_shape(self):
        res = {
            "text": "hola",
            "provider": "gemini",
            "model_used": "m",
            "tier_used": "fast",
            "cache_hit": False,
            "input_tokens": 1,
            "output_tokens": 2,
            "estimated_cost_usd": 0.001,
        }
        out = _response_from_res(res, "rid-1", 42)
        self.assertEqual(out["request_id"], "rid-1")
        self.assertEqual(out["latency_ms"], 42)
        self.assertEqual(out["text"], "hola")


if __name__ == "__main__":
    unittest.main()
