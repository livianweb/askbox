"""Política de modelos Ollama activos por proyecto. Sin red."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("ASKBOX_MASTER_KEY", "00" * 32)

from app.config import settings  # noqa: E402
from app.llm.local_models import (  # noqa: E402
    LocalPolicyError,
    all_enabled_prefs,
    apply_update,
    default_active_slugs,
    policy_fingerprint,
    public_view,
    resolve_local_model,
    slot_catalog,
)


class LocalModelPolicyTests(unittest.TestCase):
    def test_default_is_only_the_code_slot(self):
        if os.getenv("OLLAMA_DEFAULT_ACTIVE_MODELS"):
            self.skipTest("el entorno pisa el default de modelos activos")
        active = default_active_slugs()
        self.assertIn(settings.allin_code_model, active)
        self.assertNotIn(settings.ollama_reason_model, active)
        self.assertNotIn(settings.ollama_nlp_model, active)
        self.assertNotIn(settings.ollama_longctx_model, active)
        view = public_view({"ollama_models": {}})
        by_slug = {row["slug"]: row for row in view["models"]}
        self.assertTrue(by_slug[settings.allin_code_model]["enabled"])
        self.assertTrue(by_slug[settings.allin_code_model]["primary"])
        self.assertEqual(by_slug[settings.allin_code_model]["label"], "Código")
        self.assertFalse(by_slug[settings.ollama_reason_model]["enabled"])
        self.assertEqual(by_slug[settings.ollama_reason_model]["label"], "Razonamiento")
        self.assertEqual(view["primary"], settings.allin_code_model)
        self.assertEqual(
            resolve_local_model(settings.ollama_reason_model, {}),
            settings.allin_code_model,
        )
        self.assertEqual(
            resolve_local_model(settings.ollama_nlp_model, None),
            settings.allin_code_model,
        )
        self.assertEqual(
            resolve_local_model(settings.ollama_longctx_model, {"id": "p"}),
            settings.allin_code_model,
        )

    def test_enabled_deepseek_is_kept_and_primary_wins_when_requested_is_off(self):
        prefs = {
            "primary": settings.ollama_reason_model,
            "enabled": {
                settings.ollama_reason_model: True,
                settings.allin_code_model: False,
                settings.ollama_model: False,
            },
        }
        project = {"ollama_models": prefs}
        self.assertEqual(resolve_local_model(settings.ollama_reason_model, project), settings.ollama_reason_model)
        self.assertEqual(resolve_local_model(settings.allin_code_model, project), settings.ollama_reason_model)
        self.assertIsNone(resolve_local_model(settings.ollama_nlp_model, {"ollama_models": {
            "enabled": {item["slug"]: False for item in slot_catalog()},
        }}))

    def test_json_string_prefs_and_fingerprint(self):
        import json

        raw = json.dumps({"enabled": {settings.ollama_reason_model: True}})
        a = policy_fingerprint({"ollama_models": {}})
        b = policy_fingerprint({"ollama_models": raw})
        self.assertNotEqual(a, b)
        self.assertEqual(
            resolve_local_model(settings.ollama_reason_model, {"ollama_models": raw}),
            settings.ollama_reason_model,
        )

    def test_apply_update_roundtrip_and_rejections(self):
        stored = apply_update(
            {"ollama_models": {}},
            [
                {"slug": settings.allin_code_model, "enabled": True},
                {"slug": settings.ollama_reason_model, "enabled": True},
                {"slug": settings.ollama_nlp_model, "enabled": False},
                {"slug": settings.ollama_longctx_model, "enabled": False},
            ],
            settings.ollama_reason_model,
            primary_set=True,
        )
        self.assertEqual(stored["primary"], settings.ollama_reason_model)
        self.assertTrue(stored["enabled"][settings.ollama_reason_model])
        self.assertFalse(stored["enabled"][settings.ollama_nlp_model])
        view = public_view({"ollama_models": stored})
        self.assertEqual(view["primary"], settings.ollama_reason_model)

        with self.assertRaises(LocalPolicyError) as unknown:
            apply_update({"ollama_models": {}}, [{"slug": "no-such:model", "enabled": True}], None, primary_set=False)
        self.assertEqual(unknown.exception.code, "unknown_local_model")

        with self.assertRaises(LocalPolicyError) as inactive:
            apply_update(
                {"ollama_models": {}},
                [{"slug": settings.ollama_reason_model, "enabled": False}],
                settings.ollama_reason_model,
                primary_set=True,
            )
        self.assertEqual(inactive.exception.code, "primary_inactive")

        cleared = apply_update(
            {"ollama_models": all_enabled_prefs()},
            [{"slug": item["slug"], "enabled": False} for item in slot_catalog()],
            None,
            primary_set=True,
        )
        self.assertEqual(cleared["primary"], "")
        self.assertTrue(all(flag is False for flag in cleared["enabled"].values()))
        self.assertIsNone(resolve_local_model(settings.allin_code_model, {"ollama_models": cleared}))
