"""Tests de adjuntos / visión (sin red ni BD)."""
import base64
import os
import unittest
import unittest.mock
from io import BytesIO
from unittest.mock import AsyncMock, patch

os.environ["ASKBOX_MASTER_KEY"] = "00" * 32

from app.llm import attachments, cache, catalog  # noqa: E402
from app.llm.attachments import (  # noqa: E402
    MAX_ATTACHMENT_BYTES,
    VISION_MAX_EDGE,
    infer_task,
    normalize_attachments,
    preprocess_image_for_provider,
    sniff_mime,
    temperature_for_task,
)

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[misc, assignment]


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


def _png_image_bytes(width: int, height: int) -> bytes:
    assert Image is not None
    img = Image.new("RGB", (width, height), color=(40, 80, 120))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    assert Image is not None
    with Image.open(BytesIO(data)) as img:
        return img.size


class ImagePreprocessTests(unittest.TestCase):
    @unittest.skipIf(Image is None, "Pillow no instalado")
    def test_large_png_becomes_smaller_jpeg_within_max_edge(self):
        raw = _png_image_bytes(3200, 800)
        out, mime = preprocess_image_for_provider(raw, "image/png")
        self.assertEqual(mime, "image/jpeg")
        w, h = _jpeg_dimensions(out)
        self.assertLessEqual(max(w, h), VISION_MAX_EDGE)
        self.assertEqual(max(w, h), VISION_MAX_EDGE)

    @unittest.skipIf(Image is None, "Pillow no instalado")
    def test_small_image_not_upscaled(self):
        raw = _png_image_bytes(120, 90)
        out, mime = preprocess_image_for_provider(raw, "image/png")
        self.assertEqual(mime, "image/jpeg")
        w, h = _jpeg_dimensions(out)
        self.assertEqual((w, h), (120, 90))

    def test_pdf_skips_pillow(self):
        pdf = b"%PDF-1.4\n1 0 obj\nendobj\n"
        with patch("PIL.Image.open") as mock_open:
            out, mime = preprocess_image_for_provider(pdf, "application/pdf")
        mock_open.assert_not_called()
        self.assertEqual(out, pdf)
        self.assertEqual(mime, "application/pdf")

    @unittest.skipIf(Image is None, "Pillow no instalado")
    def test_normalize_uses_compressed_bytes_for_digest(self):
        raw = _png_image_bytes(2400, 600)
        b64 = base64.b64encode(raw).decode()
        norm = normalize_attachments([{"mime": "image/png", "data_base64": b64}])
        self.assertEqual(norm[0].mime, "image/jpeg")
        self.assertLess(norm[0].size, len(raw))


class CatalogVisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_models_query_includes_vision_filter(self):
        pool = unittest.mock.MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        await catalog._models_for_class(pool, "gemini", "M", 3, require_vision=True)
        query = pool.fetch.call_args[0][0]
        self.assertIn("supports_vision", query)


if __name__ == "__main__":
    unittest.main()
