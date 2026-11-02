"""Heurísticas y orquestación: Ollama local (sin nube) frente a mode=allin (híbrido)."""
from __future__ import annotations

import ast
import inspect
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ASKBOX_MASTER_KEY", "00" * 32)

from pydantic import ValidationError  # noqa: E402

from app.config import settings  # noqa: E402
from app.llm import ollama_local  # noqa: E402
from app.llm.attachments import NormalizedAttachment  # noqa: E402
from app.llm.errors import LLMError  # noqa: E402
from app.llm.heuristics import (  # noqa: E402
    allin_needs_report,
    allin_shape,
    allin_stages,
    complex_development,
    is_high_complexity,
    is_long_context,
    needs_code,
    needs_web,
    ollama_auto_kind,
)
from app.llm.keypool import KeyHandle  # noqa: E402
from app.llm.local_models import all_enabled_prefs  # noqa: E402
from app.llm.ollama_route import parse_explicit_provider  # noqa: E402
from app.llm.providers import gemini, openrouter  # noqa: E402
from app.llm.router import AskFailure, ask  # noqa: E402
from app.routers.ask import AskIn  # noqa: E402


class HeuristicTests(unittest.TestCase):
    def test_web_is_conservative(self):
        self.assertTrue(needs_web("Busca en Google la última versión de FastAPI"))
        self.assertTrue(needs_web("search the web for the changelog"))
        self.assertTrue(needs_web("latest version of postgres"))
        self.assertTrue(needs_web("versión más reciente del driver"))
        self.assertTrue(needs_web("mira la documentación reciente de Redis"))
        self.assertTrue(needs_web("recent docs of the SDK"))
        self.assertTrue(needs_web("fuente https://example.com/docs/v2"))
        self.assertFalse(needs_web("Di hola en una frase"))
        self.assertFalse(needs_web("cómo configurar google cloud storage"))
        self.assertFalse(needs_web("escribe una función que sume dos enteros"))
        self.assertFalse(needs_web("explica el interbloqueo de dos transacciones"))

    def test_complexity_and_code_are_separate(self):
        self.assertTrue(is_high_complexity("Explica el interbloqueo de dos transacciones"))
        self.assertTrue(is_high_complexity("module architecture of the checkout"))
        self.assertTrue(is_high_complexity("hay un bug complejo intermitente"))
        self.assertTrue(is_high_complexity("razona la lógica abstracta del planificador"))
        self.assertTrue(is_high_complexity("race condition en el worker"))
        self.assertFalse(is_high_complexity("Di hola en una frase"))
        self.assertFalse(is_high_complexity("SELECT name FROM users WHERE active = 1"))
        self.assertFalse(is_high_complexity("maquetación del header en PrestaShop"))

        self.assertTrue(needs_code("Escribe una función que sume dos enteros"))
        self.assertTrue(needs_code("SELECT name FROM users WHERE active = 1"))
        self.assertTrue(needs_code("consulta sql de los pedidos"))
        self.assertTrue(needs_code("corrige la sintaxis de este hook de PrestaShop"))
        self.assertTrue(needs_code("maqueta la ficha de producto"))
        self.assertFalse(needs_code("select the option from the menu and explain it"))
        self.assertFalse(needs_code("Explica el interbloqueo de dos transacciones que se esperan"))
        self.assertFalse(needs_code("Di hola en una frase"))

    def test_ollama_auto_kinds(self):
        self.assertEqual(ollama_auto_kind("Di hola en una frase"), "code")
        self.assertEqual(ollama_auto_kind("SELECT id FROM orders"), "code")
        self.assertEqual(ollama_auto_kind("hook de prestashop displayHeader"), "code")
        self.assertEqual(
            ollama_auto_kind("Explica el interbloqueo de dos transacciones que se esperan mutuamente"),
            "reason",
        )
        self.assertEqual(
            ollama_auto_kind("Hay un deadlock y un bug complejo. Escribe el código SQL que lo corrige."),
            "pipeline",
        )
        self.assertTrue(
            complex_development("Hay un deadlock y un bug complejo. Escribe el código SQL que lo corrige.")
        )

    def test_four_local_routes(self):
        self.assertEqual(
            ollama_auto_kind("Refactoriza este módulo PHP y el Dockerfile del worker"),
            "code",
        )
        self.assertEqual(ollama_auto_kind("escribe una consulta SQL de los pedidos"), "code")
        self.assertEqual(
            ollama_auto_kind("Depura paso a paso el deadlock de Sage X3 entre dos transacciones"),
            "reason",
        )
        self.assertEqual(
            ollama_auto_kind("Explica el algoritmo complejo de detección de interbloqueos"),
            "reason",
        )
        self.assertEqual(
            ollama_auto_kind(
                "Clasifica este ticket de PrestaShop y categoriza la intención del cliente"
            ),
            "nlp",
        )
        self.assertEqual(
            ollama_auto_kind("Resume el log largo del worker y dime de qué trata el texto"),
            "nlp",
        )
        self.assertEqual(
            ollama_auto_kind("Clasifica el ticket y escribe una función PHP que lo resuelva"),
            "code",
        )
        manual = _pad(
            "Lee el manual de referencia y estas fuentes técnicas.\n\nFuente 1: alpha\nFuente 2: beta\n",
            2500,
        )
        self.assertTrue(is_long_context(manual))
        self.assertEqual(ollama_auto_kind(manual), "longctx")
        self.assertEqual(ollama_auto_kind("x" * (8000 * 4)), "longctx")
        self.assertFalse(is_long_context("Consulta el manual de referencia de Redis"))
        self.assertNotEqual(ollama_auto_kind("Consulta el manual de referencia de Redis"), "longctx")
        huge_code = _pad("Hay un deadlock. Escribe el código SQL que lo corrige. ", 9000)
        self.assertEqual(ollama_auto_kind(huge_code), "pipeline")
        self.assertEqual(
            ollama_local.planned_models("Clasifica este ticket de soporte"),
            [settings.ollama_nlp_model],
        )
        self.assertEqual(ollama_local.planned_models(manual), [settings.ollama_longctx_model])
        self.assertEqual(
            ollama_local.planned_models("Refactoriza este módulo PHP"),
            [settings.allin_code_model],
        )

    def test_nlp_default_is_llama_31(self):
        if os.getenv("OLLAMA_NLP_MODEL") or os.getenv("ALLIN_NLP_MODEL"):
            self.skipTest("el entorno pisa el default de NLP")
        if os.getenv("OLLAMA_LONGCTX_MODEL") or os.getenv("ALLIN_LONGCTX_MODEL"):
            self.skipTest("el entorno pisa el default de contexto largo")
        self.assertEqual(settings.ollama_nlp_model, "llama3.1:8b")
        self.assertEqual(settings.allin_nlp_model, "llama3.1:8b")
        self.assertEqual(settings.ollama_longctx_model, "mistral-nemo:12b")
        self.assertEqual(settings.allin_longctx_model, "mistral-nemo:12b")
        self.assertNotIn("llama3.3", settings.ollama_nlp_model)

    def test_allin_stage_selection(self):
        simple = allin_stages("Di hola", has_attachments=False, web_needed=False, doc_and_web=False)
        self.assertEqual(simple, {"shape": "simple", "single": "reason", "context": None, "code": False})
        self.assertFalse(allin_needs_report(
            shape="simple", has_extracted=False, has_web=False, has_plan=False, has_code_stage=False,
        ))

        code = allin_stages(
            "Escribe una función que sume",
            has_attachments=False,
            web_needed=False,
            doc_and_web=False,
        )
        self.assertEqual(code["shape"], "simple")
        self.assertEqual(code["single"], "code")

        nlp = allin_stages(
            "Clasifica este ticket de soporte",
            has_attachments=False,
            web_needed=False,
            doc_and_web=False,
        )
        self.assertEqual(nlp["shape"], "simple")
        self.assertEqual(nlp["single"], "nlp")
        self.assertFalse(allin_needs_report(
            shape="simple", has_extracted=False, has_web=False, has_plan=False, has_code_stage=False,
        ))

        reason = allin_stages(
            "qué dice",
            has_attachments=True,
            web_needed=False,
            doc_and_web=False,
        )
        self.assertEqual(reason["shape"], "reason")
        self.assertEqual(reason["context"], "reason")
        self.assertFalse(reason["code"])

        long_read = allin_stages(
            "qué dice el documento",
            has_attachments=True,
            web_needed=False,
            doc_and_web=False,
            context_tokens=8000,
        )
        self.assertEqual(long_read["context"], "longctx")
        self.assertFalse(long_read["code"])
        self.assertTrue(allin_needs_report(
            shape="reason", has_extracted=True, has_web=False, has_plan=False, has_code_stage=False,
        ))

        plan = allin_stages(
            "Hay un deadlock. Escribe el código SQL.",
            has_attachments=False,
            web_needed=True,
            doc_and_web=False,
        )
        self.assertEqual(plan["shape"], "plan_code")
        self.assertEqual(plan["context"], "reason")
        self.assertTrue(plan["code"])
        self.assertTrue(allin_needs_report(
            shape="plan_code", has_extracted=False, has_web=True, has_plan=True, has_code_stage=True,
        ))
        self.assertFalse(allin_needs_report(
            shape="reason", has_extracted=False, has_web=False, has_plan=True, has_code_stage=False,
        ))

    def test_collapse_repeated_slug_to_one_answer(self):
        from app.llm.allin import collapse_resolved_stages

        qwen = "qwen2.5-coder:14b"
        deepseek = "deepseek-r1:14b"
        llama = "llama3.1:8b"
        self.assertEqual(
            collapse_resolved_stages([("plan", qwen), ("code", qwen), ("report", qwen)]),
            [("answer", qwen)],
        )
        self.assertEqual(
            collapse_resolved_stages([("plan", deepseek), ("code", qwen), ("report", llama)]),
            [("plan", deepseek), ("code", qwen), ("report", llama)],
        )
        self.assertEqual(
            collapse_resolved_stages([("plan", deepseek), ("code", qwen), ("report", qwen)]),
            [("plan", deepseek), ("answer", qwen)],
        )
        self.assertEqual(
            collapse_resolved_stages([("plan", qwen), ("code", qwen), ("report", llama)]),
            [("code", qwen), ("report", llama)],
        )
        self.assertEqual(
            collapse_resolved_stages([("read", qwen), ("report", qwen)]),
            [("answer", qwen)],
        )
        self.assertEqual(collapse_resolved_stages([("plan", None), ("code", None)]), [])
        self.assertEqual(
            ollama_local.dedupe_steps([qwen, qwen]),
            [qwen],
        )
        self.assertEqual(
            ollama_local.dedupe_steps([deepseek, qwen]),
            [deepseek, qwen],
        )

    def test_allin_simple_shape_skips_chain(self):
        self.assertEqual(
            allin_shape("Di hola", has_attachments=False, web_needed=False, doc_and_web=False),
            "simple",
        )
        self.assertEqual(
            allin_shape(
                "Escribe una función que sume",
                has_attachments=False,
                web_needed=False,
                doc_and_web=False,
            ),
            "simple",
        )
        self.assertEqual(
            allin_shape("qué dice", has_attachments=True, web_needed=False, doc_and_web=False),
            "reason",
        )
        self.assertEqual(
            allin_shape(
                "Hay un deadlock. Escribe el código SQL.",
                has_attachments=False,
                web_needed=True,
                doc_and_web=False,
            ),
            "plan_code",
        )
        self.assertEqual(
            allin_shape("hola", has_attachments=True, web_needed=True, doc_and_web=True),
            "reason",
        )


class BoundaryTests(unittest.TestCase):
    def test_parse_does_not_treat_allin_as_ollama(self):
        with self.assertRaises(ValueError):
            parse_explicit_provider("allin", None)

    def test_askin_aliases_and_priority(self):
        by_mode = AskIn(prompt="hola", mode="Allin")
        self.assertEqual(by_mode.mode, "allin")
        self.assertIsNone(by_mode.provider)
        self.assertIsNone(by_mode.model)

        by_provider = AskIn(prompt="hola", provider="allin", model="qwen2.5-coder:14b")
        self.assertEqual(by_provider.mode, "allin")
        self.assertIsNone(by_provider.provider)
        self.assertIsNone(by_provider.model)

        both = AskIn(prompt="hola", mode="allin", provider="ollama", model="llama3.2:3b")
        self.assertEqual(both.mode, "allin")
        self.assertIsNone(both.provider)

        forced = AskIn(prompt="hola", provider="ollama", model="deepseek-r1:14b")
        self.assertIsNone(forced.mode)
        self.assertEqual((forced.provider, forced.model), ("ollama", "deepseek-r1:14b"))

        with self.assertRaises(ValidationError):
            AskIn(prompt="hola", mode="turbo")

        body = AskIn(
            prompt="lee",
            mode="allin",
            attachments=[{"mime": "image/png", "data_base64": "aaaa", "filename": "a.png"}],
        )
        self.assertEqual(len(body.attachments or []), 1)
        self.assertIsNone(body.provider)

    def test_ollama_local_module_has_no_cloud_imports(self):
        tree = ast.parse(inspect.getsource(ollama_local))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
                names.extend(alias.name for alias in node.names)
        blob = " ".join(names).lower()
        self.assertNotIn("gemini", blob)
        self.assertNotIn("openrouter", blob)
        self.assertNotIn("keypool", blob)
        self.assertNotIn("allin", blob)


def _project(**overrides) -> dict:
    """Proyecto de tests de ruta. Por defecto los cuatro slots locales están activos,
    para seguir comprobando código / razón / NLP / contexto largo cuando el proyecto los enciende.
    """
    base = {
        "id": "p1",
        "cache_ttl_seconds": 0,
        "default_tier": "auto",
        "allow_system_keys": True,
        "ollama_models": all_enabled_prefs(),
    }
    base.update(overrides)
    return base


def _ok(model: str, text: str = "ok") -> dict:
    return {"text": text, "model": model, "input_tokens": 1, "output_tokens": 2}


class _RecordingComplete:
    def __init__(self, result=None, error=None):
        self.calls: list[tuple[str, list[dict]]] = []
        self._result = result
        self._error = error

    async def __call__(self, messages, *, model, max_tokens, temperature=0.4):
        self.calls.append((model, messages))
        if self._error:
            raise self._error
        text = self._result
        if text is None:
            text = f"out:{model}"
        return _ok(model, text)


class OllamaLocalRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_simple_prompt_uses_qwen_and_never_cloud(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("la cascada no debe ejecutarse"))
        web = AsyncMock(side_effect=AssertionError("ollama no busca en la web"))
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
            patch("app.llm.allin.web_summary", web),
            patch("app.llm.providers.gemini.complete", AsyncMock(side_effect=AssertionError("gemini"))),
            patch("app.llm.providers.openrouter.complete", AsyncMock(side_effect=AssertionError("openrouter"))),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "Di hola en una frase"}],
                tier="auto",
                max_tokens=64,
                provider="ollama",
            )
        cascade.assert_not_called()
        web.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.allin_code_model])
        self.assertEqual(res["provider"], "ollama")
        self.assertEqual(res["key_source"], "local")
        self.assertEqual(res["models_chain"], [f"ollama:{settings.allin_code_model}"])
        self.assertEqual(res["estimated_cost_usd"], 0.0)

    async def test_complex_logic_uses_deepseek_only(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("sin nube"))
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{
                    "role": "user",
                    "content": "Explica el interbloqueo de dos transacciones que se esperan mutuamente",
                }],
                tier="fast",
                max_tokens=128,
                provider="ollama",
                model="auto",
            )
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.ollama_reason_model])
        self.assertEqual(res["provider"], "ollama")
        self.assertEqual(res["models_chain"], [f"ollama:{settings.ollama_reason_model}"])

    async def test_nlp_prompt_uses_llama_and_never_cloud(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("sin nube"))
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{
                    "role": "user",
                    "content": "Clasifica este ticket y categoriza la intención del cliente",
                }],
                tier="fast",
                max_tokens=64,
                provider="ollama",
                model="auto",
            )
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.ollama_nlp_model])
        self.assertEqual(res["provider"], "ollama")
        self.assertEqual(res["key_source"], "local")
        self.assertEqual(res["models_chain"], [f"ollama:{settings.ollama_nlp_model}"])

    async def test_long_context_uses_mistral_nemo_only(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("sin nube"))
        manual = _pad("Lee el manual de referencia.\n\nFuente 1: alpha\nFuente 2: beta\n", 2500)
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": manual}],
                tier="fast",
                max_tokens=128,
                provider="ollama",
            )
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.ollama_longctx_model])
        self.assertEqual(res["provider"], "ollama")
        self.assertEqual(res["models_chain"], [f"ollama:{settings.ollama_longctx_model}"])

    async def test_complex_development_is_local_pipeline(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("sin nube"))
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{
                    "role": "user",
                    "content": "Hay un deadlock y un bug complejo. Escribe el código SQL que lo corrige.",
                }],
                tier="fast",
                max_tokens=256,
                provider="ollama",
            )
        cascade.assert_not_called()
        self.assertEqual(
            [c[0] for c in complete.calls],
            [settings.ollama_reason_model, settings.allin_code_model],
        )
        self.assertIn("Plan de diseño:", complete.calls[1][1][-1]["content"])
        self.assertEqual(
            res["models_chain"],
            [f"ollama:{settings.ollama_reason_model}", f"ollama:{settings.allin_code_model}"],
        )
        self.assertEqual(res["provider"], "ollama")
        self.assertEqual(res["model_used"], settings.allin_code_model)

    async def test_explicit_model_is_a_single_call(self):
        complete = _RecordingComplete()
        with patch("app.llm.ollama_local.ollama.complete", complete):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{
                    "role": "user",
                    "content": "Hay un deadlock y un bug complejo. Escribe el código SQL que lo corrige.",
                }],
                tier="fast",
                max_tokens=64,
                provider="ollama",
                model="llama3.2:3b",
            )
        self.assertEqual([c[0] for c in complete.calls], ["llama3.2:3b"])
        self.assertEqual(res["models_chain"], ["ollama:llama3.2:3b"])

    async def test_local_failure_does_not_call_cloud(self):
        complete = _RecordingComplete(error=LLMError("timeout", kind="retry"))
        cascade = AsyncMock(side_effect=AssertionError("sin fallback"))
        web = AsyncMock(side_effect=AssertionError("sin web"))
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
            patch("app.llm.allin.web_summary", web),
        ):
            with self.assertRaises(AskFailure) as ctx:
                await ask(
                    None,
                    None,
                    project=_project(),
                    messages=[{"role": "user", "content": "Di hola en una frase"}],
                    tier="fast",
                    max_tokens=32,
                    provider="ollama",
                )
        cascade.assert_not_called()
        web.assert_not_called()
        self.assertEqual(ctx.exception.code, "provider_failed")
        self.assertEqual(len(complete.calls), 1)


class AllinRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_simple_logic_is_one_deepseek_call(self):
        complete = _RecordingComplete()
        extract = AsyncMock(side_effect=AssertionError("sin adjuntos no hay ocr"))
        web = AsyncMock(side_effect=AssertionError("sin web"))
        cascade = AsyncMock(side_effect=AssertionError("sin fallback"))
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.extract_documents", extract),
            patch("app.llm.allin.web_summary", web),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "Di hola en una frase"}],
                tier="auto",
                max_tokens=32,
                mode="allin",
            )
        extract.assert_not_called()
        web.assert_not_called()
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.allin_reason_model])
        self.assertEqual(res["provider"], "allin")
        self.assertEqual(res["models_chain"], [f"ollama:{settings.allin_reason_model}"])
        self.assertNotIn("allin:ocr", res["models_chain"])
        self.assertNotIn("allin:web", res["models_chain"])

    async def test_simple_code_is_one_qwen_call(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("sin fallback"))
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "Escribe una función que sume dos enteros"}],
                tier="fast",
                max_tokens=64,
                provider="allin",
            )
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.allin_code_model])
        self.assertEqual(res["provider"], "allin")

    async def test_attachments_run_ocr_then_reason(self):
        complete = _RecordingComplete()
        extract = AsyncMock(return_value="[a.png p1]\nFACTURA 10")
        web = AsyncMock(side_effect=AssertionError("este prompt no pide web"))
        att = NormalizedAttachment(mime="image/png", data=b"png", sha256="abc", size=3, filename="a.png")
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.extract_documents", extract),
            patch("app.llm.allin.web_summary", web),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "qué pone en el documento"}],
                tier="fast",
                max_tokens=64,
                mode="allin",
                normalized_attachments=[att],
            )
        extract.assert_awaited()
        web.assert_not_called()
        self.assertEqual(res["models_chain"][0], "allin:ocr")
        self.assertEqual(
            [c[0] for c in complete.calls],
            [settings.allin_reason_model, settings.allin_nlp_model],
        )
        self.assertIn("FACTURA 10", complete.calls[0][1][-1]["content"])
        self.assertIn("Resultado de la tarea:", complete.calls[1][1][-1]["content"])
        self.assertTrue(all("attachments" not in msg for msg in complete.calls[0][1]))
        self.assertEqual(res["model_used"], settings.allin_nlp_model)
        self.assertEqual(res["provider"], "allin")

    async def test_web_then_plan_then_code(self):
        complete = _RecordingComplete()
        web = AsyncMock(return_value={
            "text": "PostgreSQL 16 documenta lock_timeout",
            "models_chain": ["gemini:peer"],
            "attempts": 1,
            "input_tokens": 3,
            "output_tokens": 4,
            "estimated_cost_usd": 0.0,
            "key_id": None,
            "key_source": "system",
        })
        cascade = AsyncMock(side_effect=AssertionError("local respondió"))
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.web_summary", web),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{
                    "role": "user",
                    "content": (
                        "Busca en Google la documentación reciente de los deadlocks "
                        "y escribe el código SQL que evita el interbloqueo."
                    ),
                }],
                tier="balanced",
                max_tokens=200,
                mode="allin",
            )
        cascade.assert_not_called()
        web.assert_awaited()
        self.assertEqual(
            [c[0] for c in complete.calls],
            [settings.allin_reason_model, settings.allin_code_model, settings.allin_nlp_model],
        )
        self.assertIn("lock_timeout", complete.calls[0][1][-1]["content"])
        self.assertIn("Plan de diseño:", complete.calls[1][1][-1]["content"])
        self.assertIn("Resultado de la tarea:", complete.calls[2][1][-1]["content"])
        self.assertEqual(res["provider"], "allin")
        self.assertEqual(res["model_used"], settings.allin_nlp_model)
        self.assertEqual(
            res["models_chain"][:3],
            ["allin:web", "gemini:peer", f"ollama:{settings.allin_reason_model}"],
        )
        self.assertIn(f"ollama:{settings.allin_code_model}", res["models_chain"])
        self.assertIn(f"ollama:{settings.allin_nlp_model}", res["models_chain"])

    async def test_web_failure_still_answers_locally(self):
        complete = _RecordingComplete(result="sin web")
        web = AsyncMock(side_effect=LLMError("grounding caído", kind="retry"))
        cascade = AsyncMock(side_effect=AssertionError("no hace falta la cascada"))
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.web_summary", web),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "Busca en Google la latest version de Redis"}],
                tier="fast",
                max_tokens=64,
                mode="allin",
            )
        cascade.assert_not_called()
        self.assertEqual(res["text"], "sin web")
        self.assertNotIn("allin:web", res["models_chain"])
        self.assertEqual(res["provider"], "allin")

    async def test_simple_nlp_is_one_llama_call(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("sin fallback"))
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "Clasifica este ticket de soporte: no puede pagar"}],
                tier="fast",
                max_tokens=64,
                mode="allin",
            )
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.allin_nlp_model])
        self.assertEqual(res["provider"], "allin")
        self.assertNotIn("allin:ocr", res["models_chain"])

    async def test_massive_context_uses_mistral_then_report(self):
        complete = _RecordingComplete()
        extract = AsyncMock(return_value="pagina " * 20000)
        att = NormalizedAttachment(mime="image/png", data=b"png", sha256="abc", size=3, filename="manual.png")
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.extract_documents", extract),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "qué dice el manual"}],
                tier="fast",
                max_tokens=64,
                mode="allin",
                normalized_attachments=[att],
            )
        self.assertEqual(
            [c[0] for c in complete.calls],
            [settings.allin_longctx_model, settings.allin_nlp_model],
        )
        self.assertEqual(res["provider"], "allin")
        self.assertEqual(res["model_used"], settings.allin_nlp_model)
        self.assertEqual(res["models_chain"][0], "allin:ocr")

    async def test_report_failure_keeps_prior_answer(self):
        seen = {"n": 0}

        async def complete(messages, *, model, max_tokens, temperature=0.4):
            seen["n"] += 1
            if seen["n"] == 1:
                return _ok(model, "resolución local")
            raise LLMError("timeout", kind="retry")

        cascade = AsyncMock(side_effect=AskFailure(
            "cascada agotada", tier_used="fast", attempts=1, code="provider_failed",
        ))
        extract = AsyncMock(return_value="[a.png p1]\nFACTURA 10")
        att = NormalizedAttachment(mime="image/png", data=b"png", sha256="abc", size=3, filename="a.png")
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.extract_documents", extract),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "qué pone en el documento"}],
                tier="fast",
                max_tokens=64,
                mode="allin",
                normalized_attachments=[att],
            )
        cascade.assert_awaited()
        self.assertEqual(res["text"], "resolución local")
        self.assertEqual(res["provider"], "allin")
        self.assertEqual(res["model_used"], settings.allin_reason_model)

    async def test_local_failure_falls_back_to_cascade(self):
        complete = _RecordingComplete(error=LLMError("connection refused", kind="retry"))
        cascade = AsyncMock(return_value={
            "text": "desde la cascada",
            "provider": "gemini",
            "model_used": "gemini-test",
            "models_chain": ["gemini:gemini-test"],
            "attempts": 2,
            "input_tokens": 4,
            "output_tokens": 5,
            "estimated_cost_usd": 0.02,
            "key_id": None,
            "key_source": "system",
            "cache_hit": False,
            "tier_used": "fast",
        })
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "Di hola en una frase"}],
                tier="fast",
                max_tokens=32,
                mode="allin",
                provider="ollama",
            )
        cascade.assert_awaited()
        self.assertEqual(res["text"], "desde la cascada")
        self.assertEqual(res["provider"], "gemini")
        self.assertEqual(res["model_used"], "gemini-test")
        self.assertIn(f"ollama:{settings.allin_reason_model}", res["models_chain"])
        self.assertIn("gemini:gemini-test", res["models_chain"])
        self.assertGreaterEqual(res["attempts"], 2)

    async def test_schema_mismatch_also_falls_back(self):
        complete = _RecordingComplete(result="no soy json")
        cascade = AsyncMock(return_value={
            "text": '{"ok": true}',
            "provider": "openrouter",
            "model_used": "peer",
            "models_chain": ["openrouter:peer"],
            "attempts": 1,
            "input_tokens": 1,
            "output_tokens": 1,
            "estimated_cost_usd": 0.0,
            "key_id": "k1",
            "key_source": "project",
            "cache_hit": False,
            "tier_used": "fast",
        })
        schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(),
                messages=[{"role": "user", "content": "Di hola en una frase"}],
                tier="fast",
                max_tokens=32,
                mode="allin",
                json_schema=schema,
            )
        self.assertEqual(res["text"], '{"ok": true}')
        self.assertEqual(res["provider"], "openrouter")
        kwargs = cascade.await_args.kwargs
        self.assertEqual(kwargs["json_schema"], schema)
        self.assertEqual(kwargs["ttl"], 0)


def _disabled_locals() -> dict:
    from app.llm.local_models import slot_catalog

    return {
        "primary": "",
        "enabled": {item["slug"]: False for item in slot_catalog()},
    }


class LocalActivePolicyTests(unittest.IsolatedAsyncioTestCase):
    """Default de despliegue: solo el modelo de código está activo."""

    async def test_inactive_reason_uses_qwen_on_ollama_auto(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("hay un local activo"))
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models={}),
                messages=[{
                    "role": "user",
                    "content": "Depura paso a paso el deadlock de Sage X3 entre dos transacciones",
                }],
                tier="fast",
                max_tokens=64,
                provider="ollama",
                model="auto",
            )
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.allin_code_model])
        self.assertEqual(res["provider"], "ollama")
        self.assertEqual(res["models_chain"], [f"ollama:{settings.allin_code_model}"])
        self.assertEqual(res["model_used"], settings.allin_code_model)

    async def test_inactive_reason_uses_qwen_on_allin(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("hay un local activo"))
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models={}),
                messages=[{"role": "user", "content": "Di hola en una frase"}],
                tier="fast",
                max_tokens=32,
                mode="allin",
            )
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.allin_code_model])
        self.assertEqual(res["provider"], "allin")
        self.assertEqual(res["models_chain"], [f"ollama:{settings.allin_code_model}"])

    async def test_all_locals_inactive_falls_back_to_cloud(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(return_value={
            "text": "desde la nube",
            "provider": "gemini",
            "model_used": "gemini-test",
            "models_chain": ["gemini:gemini-test"],
            "attempts": 1,
            "input_tokens": 2,
            "output_tokens": 3,
            "estimated_cost_usd": 0.01,
            "key_id": None,
            "key_source": "system",
            "cache_hit": False,
            "tier_used": "fast",
        })
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models=_disabled_locals()),
                messages=[{"role": "user", "content": "Di hola en una frase"}],
                tier="fast",
                max_tokens=32,
                mode="allin",
            )
        self.assertEqual(complete.calls, [])
        cascade.assert_awaited()
        self.assertEqual(res["text"], "desde la nube")
        self.assertEqual(res["provider"], "gemini")
        self.assertEqual(res["models_chain"], ["gemini:gemini-test"])
        self.assertNotIn("ollama:", " ".join(res["models_chain"]))

    async def test_ollama_auto_with_no_active_local_uses_cloud(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(return_value={
            "text": "nube",
            "provider": "openrouter",
            "model_used": "peer",
            "models_chain": ["openrouter:peer"],
            "attempts": 1,
            "input_tokens": 1,
            "output_tokens": 1,
            "estimated_cost_usd": 0.0,
            "key_id": None,
            "key_source": "project",
            "cache_hit": False,
            "tier_used": "fast",
        })
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models=_disabled_locals()),
                messages=[{
                    "role": "user",
                    "content": "Explica el interbloqueo de dos transacciones que se esperan",
                }],
                tier="fast",
                max_tokens=32,
                provider="ollama",
            )
        self.assertEqual(complete.calls, [])
        cascade.assert_awaited()
        self.assertEqual(res["provider"], "openrouter")
        self.assertEqual(res["text"], "nube")

    async def test_enabled_deepseek_stays_deepseek(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("deepseek está activo"))
        prefs = {
            "primary": settings.allin_code_model,
            "enabled": {settings.ollama_reason_model: True, settings.allin_reason_model: True},
        }
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models=prefs),
                messages=[{
                    "role": "user",
                    "content": "Depura paso a paso el deadlock de Sage X3 entre dos transacciones",
                }],
                tier="fast",
                max_tokens=64,
                provider="ollama",
                model="auto",
            )
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [settings.ollama_reason_model])
        self.assertEqual(res["models_chain"], [f"ollama:{settings.ollama_reason_model}"])

        complete_allin = _RecordingComplete()
        with (
            patch("app.llm.allin.ollama.complete", complete_allin),
            patch("app.llm.router._cascade", cascade),
        ):
            res_allin = await ask(
                None,
                None,
                project=_project(ollama_models=prefs),
                messages=[{"role": "user", "content": "Di hola en una frase"}],
                tier="fast",
                max_tokens=32,
                mode="allin",
            )
        self.assertEqual([c[0] for c in complete_allin.calls], [settings.allin_reason_model])
        self.assertEqual(res_allin["provider"], "allin")

    async def test_inactive_nlp_longctx_and_pipeline_land_on_qwen(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("qwen sigue activo"))
        project = _project(ollama_models={})
        manual = _pad("Lee el manual de referencia.\n\nFuente 1: alpha\nFuente 2: beta\n", 2500)
        cases = [
            "Clasifica este ticket y categoriza la intención del cliente",
            manual,
            "Hay un deadlock y un bug complejo. Escribe el código SQL que lo corrige.",
        ]
        with (
            patch("app.llm.ollama_local.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            for prompt in cases:
                before = len(complete.calls)
                res = await ask(
                    None,
                    None,
                    project=project,
                    messages=[{"role": "user", "content": prompt}],
                    tier="fast",
                    max_tokens=64,
                    provider="ollama",
                    model="auto",
                )
                self.assertEqual(len(complete.calls) - before, 1)
                self.assertEqual(res["models_chain"], [f"ollama:{settings.allin_code_model}"])
        cascade.assert_not_called()
        self.assertTrue(complete.calls)
        self.assertTrue(all(model == settings.allin_code_model for model, _ in complete.calls))

    async def test_explicit_slug_ignores_inactive_flag(self):
        complete = _RecordingComplete()
        with patch("app.llm.ollama_local.ollama.complete", complete):
            res = await ask(
                None,
                None,
                project=_project(ollama_models=_disabled_locals()),
                messages=[{"role": "user", "content": "Di hola en una frase"}],
                tier="fast",
                max_tokens=32,
                provider="ollama",
                model=settings.ollama_reason_model,
            )
        self.assertEqual([c[0] for c in complete.calls], [settings.ollama_reason_model])
        self.assertEqual(res["provider"], "ollama")

    async def test_single_active_qwen_is_one_ollama_call(self):
        """Plan + código + informe, con solo Qwen activo, es una completion."""
        complete = _RecordingComplete(result="sql único")
        cascade = AsyncMock(side_effect=AssertionError("la respuesta local basta"))
        web = AsyncMock(side_effect=AssertionError("este prompt no pide web"))
        prompt = "Hay un deadlock y un bug complejo. Escribe el código SQL que lo corrige."
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.web_summary", web),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models={}),
                messages=[{"role": "user", "content": prompt}],
                tier="fast",
                max_tokens=128,
                mode="allin",
            )
        web.assert_not_called()
        cascade.assert_not_called()
        self.assertEqual(len(complete.calls), 1)
        self.assertEqual(complete.calls[0][0], settings.allin_code_model)
        self.assertIn(prompt, complete.calls[0][1][-1]["content"])
        self.assertNotIn("Plan de diseño:", complete.calls[0][1][-1]["content"])
        self.assertEqual(res["provider"], "allin")
        self.assertEqual(res["text"], "sql único")
        self.assertEqual(res["models_chain"], [f"ollama:{settings.allin_code_model}"])
        self.assertEqual(
            [item for item in res["models_chain"] if item.startswith("ollama:")],
            [f"ollama:{settings.allin_code_model}"],
        )

    async def test_single_active_qwen_web_failure_does_not_chain_locals(self):
        complete = _RecordingComplete(result="sin parciales")
        cascade = AsyncMock(side_effect=AssertionError("no relances la cascada si Qwen respondió"))
        web = AsyncMock(side_effect=LLMError("quota", kind="quota"))
        prompt = (
            "Busca en Google la documentación reciente de los deadlocks "
            "y escribe el código SQL que evita el interbloqueo."
        )
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.web_summary", web),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models={}),
                messages=[{"role": "user", "content": prompt}],
                tier="balanced",
                max_tokens=200,
                mode="allin",
            )
        web.assert_awaited()
        cascade.assert_not_called()
        self.assertEqual(len(complete.calls), 1)
        self.assertEqual(complete.calls[0][0], settings.allin_code_model)
        self.assertNotIn("allin:web", res["models_chain"])
        self.assertEqual(res["models_chain"], [f"ollama:{settings.allin_code_model}"])
        self.assertEqual(res["provider"], "allin")
        self.assertEqual(res["text"], "sin parciales")

    async def test_single_active_qwen_keeps_web_summary_in_one_call(self):
        complete = _RecordingComplete(result="con web")
        cascade = AsyncMock(side_effect=AssertionError("Qwen responde"))
        web = AsyncMock(return_value={
            "text": "PostgreSQL 16 documenta lock_timeout",
            "models_chain": ["gemini:peer"],
            "attempts": 1,
            "input_tokens": 3,
            "output_tokens": 4,
            "estimated_cost_usd": 0.0,
            "key_id": None,
            "key_source": "system",
        })
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.web_summary", web),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models={}),
                messages=[{
                    "role": "user",
                    "content": (
                        "Busca en Google la documentación reciente de los deadlocks "
                        "y escribe el código SQL que evita el interbloqueo."
                    ),
                }],
                tier="balanced",
                max_tokens=200,
                mode="allin",
            )
        cascade.assert_not_called()
        self.assertEqual(len(complete.calls), 1)
        self.assertIn("lock_timeout", complete.calls[0][1][-1]["content"])
        self.assertEqual(
            [item for item in res["models_chain"] if item.startswith("ollama:")],
            [f"ollama:{settings.allin_code_model}"],
        )
        self.assertEqual(res["models_chain"][0], "allin:web")
        self.assertIn("gemini:peer", res["models_chain"])
        self.assertEqual(res["provider"], "allin")

    async def test_single_active_qwen_local_failure_is_one_cloud_fallback(self):
        complete = _RecordingComplete(error=LLMError("timeout", kind="retry"))
        cascade = AsyncMock(return_value={
            "text": "desde la nube",
            "provider": "gemini",
            "model_used": "gemini-test",
            "models_chain": ["gemini:gemini-test"],
            "attempts": 1,
            "input_tokens": 2,
            "output_tokens": 3,
            "estimated_cost_usd": 0.01,
            "key_id": None,
            "key_source": "system",
            "cache_hit": False,
            "tier_used": "fast",
        })
        web = AsyncMock(side_effect=LLMError("quota", kind="quota"))
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.web_summary", web),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models={}),
                messages=[{
                    "role": "user",
                    "content": (
                        "Busca en Google la documentación reciente de los deadlocks "
                        "y escribe el código SQL que evita el interbloqueo."
                    ),
                }],
                tier="fast",
                max_tokens=64,
                mode="allin",
            )
        self.assertEqual(len(complete.calls), 1)
        self.assertEqual(cascade.await_count, 1)
        self.assertEqual(res["text"], "desde la nube")
        self.assertEqual(res["provider"], "gemini")
        self.assertEqual(
            [item for item in res["models_chain"] if item.startswith("ollama:")],
            [f"ollama:{settings.allin_code_model}"],
        )

    async def test_distinct_locals_skip_duplicate_report_slug(self):
        complete = _RecordingComplete()
        cascade = AsyncMock(side_effect=AssertionError("los dos locales responden"))
        prefs = {
            "primary": settings.allin_code_model,
            "enabled": {
                settings.allin_code_model: True,
                settings.ollama_model: True,
                settings.ollama_reason_model: True,
                settings.allin_reason_model: True,
                settings.ollama_nlp_model: False,
                settings.allin_nlp_model: False,
                settings.ollama_longctx_model: False,
                settings.allin_longctx_model: False,
            },
        }
        project = _project(ollama_models=prefs)
        from app.llm.local_models import resolve_local_model

        reason = resolve_local_model(settings.allin_reason_model, project)
        code = resolve_local_model(settings.allin_code_model, project)
        nlp = resolve_local_model(settings.allin_nlp_model, project)
        if reason == code:
            self.skipTest("razón y código son el mismo slug en este entorno")
        self.assertEqual(nlp, code)
        prompt = "Hay un deadlock y un bug complejo. Escribe el código SQL que lo corrige."
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.router._cascade", cascade),
        ):
            res = await ask(
                None,
                None,
                project=project,
                messages=[{"role": "user", "content": prompt}],
                tier="fast",
                max_tokens=128,
                mode="allin",
            )
        cascade.assert_not_called()
        self.assertEqual([c[0] for c in complete.calls], [reason, code])
        self.assertIn("Plan de diseño:", complete.calls[1][1][-1]["content"])
        self.assertEqual(
            res["models_chain"],
            [f"ollama:{reason}", f"ollama:{code}"],
        )
        self.assertEqual(res["models_chain"].count(f"ollama:{code}"), 1)

    async def test_single_active_qwen_ocr_is_one_local_call(self):
        complete = _RecordingComplete(result="factura")
        extract = AsyncMock(return_value="[a.png p1]\nFACTURA 10")
        att = NormalizedAttachment(mime="image/png", data=b"png", sha256="abc", size=3, filename="a.png")
        with (
            patch("app.llm.allin.ollama.complete", complete),
            patch("app.llm.allin.extract_documents", extract),
        ):
            res = await ask(
                None,
                None,
                project=_project(ollama_models={}),
                messages=[{"role": "user", "content": "qué pone en el documento"}],
                tier="fast",
                max_tokens=64,
                mode="allin",
                normalized_attachments=[att],
            )
        self.assertEqual(len(complete.calls), 1)
        self.assertEqual(complete.calls[0][0], settings.allin_code_model)
        self.assertIn("FACTURA 10", complete.calls[0][1][-1]["content"])
        self.assertEqual(res["models_chain"][0], "allin:ocr")
        self.assertEqual(
            [item for item in res["models_chain"] if item.startswith("ollama:")],
            [f"ollama:{settings.allin_code_model}"],
        )
        self.assertEqual(res["provider"], "allin")


class ExtractAndWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_joins_ocr_lines(self):
        from app.llm.allin import extract_documents

        class Page:
            lines = [{"text": "Hola"}, {"text": "mundo"}]

        att = NormalizedAttachment(mime="image/jpeg", data=b"\xff\xd8\xff", sha256="a", size=3, filename="scan.jpg")
        with patch("app.llm.allin.ocr_engine.run_limited", AsyncMock(return_value=Page())):
            text = await extract_documents([att])
        self.assertIn("Hola", text)
        self.assertIn("mundo", text)
        self.assertIn("scan.jpg", text)

    async def test_extract_failure_is_empty(self):
        from app.llm.allin import extract_documents

        att = NormalizedAttachment(mime="image/png", data=b"x", sha256="b", size=1, filename=None)
        with patch("app.llm.allin.ocr_engine.run_limited", AsyncMock(side_effect=RuntimeError("tesseract"))):
            text = await extract_documents([att])
        self.assertEqual(text, "")

    async def test_web_prefers_gemini_grounding(self):
        from app.llm.allin import web_summary

        handle = KeyHandle("gemini", "secret-value", None, "system")
        gemini_complete = AsyncMock(return_value={"text": "resumen tecnico", "model": "peer-g", "input_tokens": 2, "output_tokens": 3})
        or_complete = AsyncMock(side_effect=AssertionError("no debía llamar a OpenRouter"))

        async def candidates(pool, project, provider):
            return [handle] if provider == "gemini" else []

        async def peers(pool, tier, providers, limit, require_vision=False):
            return [(providers[0], {"slug": "peer-g", "max_output_tokens": 1024})]

        with (
            patch("app.llm.allin.keypool.candidates", candidates),
            patch("app.llm.allin.catalog.peer_chain", peers),
            patch("app.llm.allin.gemini.complete", gemini_complete),
            patch("app.llm.allin.openrouter.complete", or_complete),
            patch("app.llm.allin.catalog.cost_for_model", AsyncMock(return_value=(0.0, 0.0))),
            patch("app.llm.allin.keypool.mark_success", AsyncMock()),
        ):
            out = await web_summary(object(), _project(), "latest version of fastapi", max_tokens=100)
        self.assertEqual(out["text"], "resumen tecnico")
        self.assertTrue(gemini_complete.await_args.kwargs["grounding"])
        self.assertEqual(out["models_chain"], ["gemini:peer-g"])
        or_complete.assert_not_called()
        self.assertNotIn("secret-value", str(out))

    async def test_web_falls_back_to_openrouter_plugin(self):
        from app.llm.allin import web_summary

        handle_g = KeyHandle("gemini", "g", None, "system")
        handle_o = KeyHandle("openrouter", "o", "key-1", "project")

        async def candidates(pool, project, provider):
            return [handle_g] if provider == "gemini" else [handle_o]

        async def peers(pool, tier, providers, limit, require_vision=False):
            slug = "peer-g" if providers[0] == "gemini" else "peer-o"
            return [(providers[0], {"slug": slug, "max_output_tokens": 800})]

        gemini_complete = AsyncMock(side_effect=LLMError("sin grounding", kind="retry"))
        or_complete = AsyncMock(return_value={"text": "via openrouter", "model": "peer-o", "input_tokens": 1, "output_tokens": 1})
        with (
            patch("app.llm.allin.keypool.candidates", candidates),
            patch("app.llm.allin.catalog.peer_chain", peers),
            patch("app.llm.allin.gemini.complete", gemini_complete),
            patch("app.llm.allin.openrouter.complete", or_complete),
            patch("app.llm.allin.catalog.cost_for_model", AsyncMock(return_value=(1.0, 2.0))),
            patch("app.llm.allin.keypool.mark_success", AsyncMock()),
        ):
            out = await web_summary(object(), _project(), "busca en Google el changelog", max_tokens=80)
        self.assertEqual(out["text"], "via openrouter")
        self.assertTrue(or_complete.await_args.kwargs["web_search"])
        self.assertEqual(out["provider"], "openrouter")
        self.assertEqual(out["key_source"], "project")


class ProviderPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_grounding_is_opt_in(self):
        seen = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, json=None):
                seen["json"] = json
                return _FakeResp(200, {
                    "candidates": [{"content": {"parts": [{"text": "hola"}]}}],
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
                })

        with patch("app.llm.providers.gemini.httpx.AsyncClient", FakeClient):
            await gemini.complete(
                [{"role": "user", "content": "hola"}],
                api_key="k",
                model="m",
                max_tokens=16,
            )
            self.assertNotIn("tools", seen["json"])
            await gemini.complete(
                [{"role": "user", "content": "hola"}],
                api_key="k",
                model="m",
                max_tokens=16,
                grounding=True,
            )
        self.assertEqual(seen["json"]["tools"], [{"google_search": {}}])

    async def test_openrouter_web_plugin_is_opt_in(self):
        seen = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, json=None):
                seen["json"] = json
                return _FakeResp(200, {
                    "choices": [{"message": {"content": "hola"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                })

        with patch("app.llm.providers.openrouter.httpx.AsyncClient", FakeClient):
            await openrouter.complete(
                [{"role": "user", "content": "hola"}],
                api_key="k",
                model="m",
                max_tokens=16,
            )
            self.assertNotIn("plugins", seen["json"])
            await openrouter.complete(
                [{"role": "user", "content": "hola"}],
                api_key="k",
                model="m",
                max_tokens=16,
                web_search=True,
            )
        self.assertEqual(seen["json"]["plugins"], [{"id": "web", "max_results": 3}])
        self.assertEqual(seen["json"]["model"], "m")


def _pad(text: str, tokens: int) -> str:
    unit = "detalle técnico del procedimiento. "
    need = tokens * 4 - len(text)
    if need <= 0:
        return text
    return text + unit * (need // len(unit) + 1)


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload
