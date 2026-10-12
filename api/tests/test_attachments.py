"""Tests de adjuntos / visión (sin red ni BD)."""
import base64
import os
import unittest
import unittest.mock
from unittest.mock import AsyncMock

os.environ["ASKBOX_MASTER_KEY"] = "00" * 32

from app.llm import attachments, cache, catalog  # noqa: E402
from app.llm.attachments import (  # noqa: E402
    MAX_ATTACHMENT_BYTES,
    infer_task,
    normalize_attachments,
    sniff_mime,
    temperature_for_task,
)


def _png_bytes(extra: bytes = b"") -> bytes:
    return b"\x89PNG\r\n\x1a\n" + extra


def _jpeg_bytes(extra: bytes = b"") -> bytes:
    return b"\xff\xd8\xff\xe0" + extra


class AttachmentMimeTests(unittest.TestCase):
    def test_sniff_png_jpeg(self):
        self.assertEqual(sniff_mime(_png_bytes()), "image/png")
        self.assertEqual(sniff_mime(_jpeg_bytes()), "image/jpeg")
        self.assertIsNone(sniff_mime(b"not-a-file"))

    def test_reject_mime_mismatch(self):
        b64 = base64.b64encode(_png_bytes()).decode()
        with self.assertRaises(Exception) as ctx:
            normalize_attachments([{"mime": "image/jpeg", "data_base64": b64}])
        self.assertEqual(ctx.exception.status_code, 422)

    def test_reject_disallowed_mime(self):
        b64 = base64.b64encode(b"%PDF-1.4\n").decode()
        with self.assertRaises(Exception) as ctx:
            normalize_attachments([{"mime": "text/plain", "data_base64": b64}])
        self.assertEqual(ctx.exception.detail["code"], "attachment_invalid_mime")

    def test_per_file_size_limit(self):
        big = _jpeg_bytes(b"x" * (MAX_ATTACHMENT_BYTES + 1))
        b64 = base64.b64encode(big).decode()
        with self.assertRaises(Exception) as ctx:
            normalize_attachments([{"mime": "image/jpeg", "data_base64": b64}])
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertEqual(ctx.exception.detail["code"], "prompt_too_large")


class AttachmentCacheAndTaskTests(unittest.TestCase):
    def test_cache_key_changes_with_attachment_digest(self):
        a = cache.cache_key("p", "prompt", "fast", 512, "", "")
        b = cache.cache_key("p", "prompt", "fast", 512, "", "abc123")
        self.assertNotEqual(a, b)

    def test_different_images_different_digest(self):
        n1 = normalize_attachments(
            [{"mime": "image/png", "data_base64": base64.b64encode(_png_bytes(b"a")).decode()}]
        )
        n2 = normalize_attachments(
            [{"mime": "image/png", "data_base64": base64.b64encode(_png_bytes(b"b")).decode()}]
        )
        d1 = attachments.attachments_cache_digest(n1)
        d2 = attachments.attachments_cache_digest(n2)
        self.assertNotEqual(d1, d2)

    def test_infer_task_heuristics(self):
        self.assertEqual(infer_task("hola", json_schema=None, task_requested="extract"), "extract")
        self.assertEqual(infer_task("hola", json_schema={"type": "object"}, task_requested=None), "extract")
        self.assertEqual(infer_task("Transcribe el texto literal", json_schema=None, task_requested=None), "transcribe")
        self.assertEqual(infer_task("Describe la escena", json_schema=None, task_requested=None), "interpret")

    def test_temperature_transcribe_zero(self):
        self.assertEqual(temperature_for_task("transcribe"), 0.0)
        self.assertEqual(temperature_for_task("interpret"), 0.4)


class CatalogVisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_models_query_includes_vision_filter(self):
        pool = unittest.mock.MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        await catalog._models_for_class(pool, "gemini", "M", 3, require_vision=True)
        query = pool.fetch.call_args[0][0]
        self.assertIn("supports_vision", query)


if __name__ == "__main__":
    unittest.main()
