"""Fase 2: tokens de proyecto y plantillas (sin BD)."""
import os
import unittest

os.environ.setdefault("ASKBOX_MASTER_KEY", "00" * 32)

from app.project_auth import TOKEN_PREFIX, new_access_token, parse_scopes  # noqa: E402
from app.prompt_render import PromptRenderError, extract_variables, render_template  # noqa: E402
from app.security import token_hash  # noqa: E402


class AccessTokenTests(unittest.TestCase):
    def test_prefix_and_hash(self):
        token, prefix = new_access_token()
        self.assertTrue(token.startswith(TOKEN_PREFIX))
        self.assertIn("…", prefix)
        self.assertNotEqual(token, token_hash(token))

    def test_scopes(self):
        from fastapi import HTTPException

        self.assertEqual(parse_scopes(["ask:read"]), ["ask:read"])
        with self.assertRaises(HTTPException) as ctx:
            parse_scopes(["nope"])
        self.assertEqual(ctx.exception.detail["code"], "invalid_scopes")


class PromptRenderTests(unittest.TestCase):
    def test_variables(self):
        self.assertEqual(extract_variables("Hola {{name}}"), {"name"})
        self.assertEqual(render_template("Hola {{name}}", {"name": "Ada"}), "Hola Ada")

    def test_missing(self):
        with self.assertRaises(PromptRenderError):
            render_template("{{x}}", {})


if __name__ == "__main__":
    unittest.main()
