"""Tests del motor OCR y de POST /v1/ocr.

Sin red ni BD. Los tests con binarios reales (tesseract / poppler) se saltan si no están
instalados (sí lo están en la imagen Docker de la API). El resto simula los subprocesos.
"""
import base64
import io
import os
import shutil
import subprocess
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ASKBOX_MASTER_KEY"] = "00" * 32

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from app.deps import AskAuth, ask_auth  # noqa: E402
from app.ocr import engine  # noqa: E402
from app.ocr.engine import OcrError, PageResult, Word  # noqa: E402


def _tsv(*rows):
    """rows: (level, left, top, width, height, conf, text)."""
    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    out = [head]
    for level, left, top, width, height, conf, text in rows:
        out.append(f"{level}\t1\t1\t1\t1\t1\t{left}\t{top}\t{width}\t{height}\t{conf}\t{text}")
    return "\n".join(out) + "\n"


def _png(color=(255, 255, 255), size=(300, 200), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def _text_png(text="FACTURA 12345 ALBARAN", size=(900, 200)) -> bytes:
    img = Image.new("RGB", size, "white")
    try:
        font = ImageDraw.ImageDraw(img).getfont()
        font = font.font_variant(size=64)  # Pillow >= 10.1 con freetype
    except Exception:  # noqa: BLE001
        font = None
    ImageDraw.Draw(img).text((20, 50), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _text_pdf(text="Albaran de entrega numero uno proveedor cliente referencia cantidad descripcion articulo " * 3) -> bytes:
    stream = f"BT /F1 11 Tf 40 780 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


class TsvGroupingTests(unittest.TestCase):
    def test_words_skip_header_non_word_levels_and_blank_text(self):
        tsv = _tsv(
            (4, 0, 0, 500, 20, -1, ""),
            (5, 10, 100, 40, 20, 91.5, "ALBARAN"),
            (5, 60, 100, 30, 20, 88, "  "),
            (5, 100, 100, 30, 20, 77, "N-12"),
        )
        words = engine.tsv_words(tsv)
        self.assertEqual([w.text for w in words], ["ALBARAN", "N-12"])
        self.assertEqual(words[0].conf, 91.5)
        self.assertEqual((words[0].left, words[0].top, words[0].width, words[0].height), (10, 100, 40, 20))

    def test_filters_conf_and_height(self):
        tsv = _tsv(
            (5, 10, 100, 40, 20, 10, "limite"),    # conf == 10 -> fuera (se exige > 10)
            (5, 60, 100, 40, 4, 90, "chico"),      # height == 4 -> fuera (se exige > 4)
            (5, 110, 100, 40, 20, 11, "valida"),
        )
        self.assertEqual(engine.tsv_lines(tsv), [{"text": "valida", "segments": ["valida"]}])

    def test_empty(self):
        self.assertEqual(engine.tsv_lines(_tsv()), [])
        self.assertEqual(engine.group_lines([]), [])

    def test_rows_by_055_h_and_segments_by_25_h(self):
        # h (mediana) = 20 -> mismo renglón si |dy| <= 11; hueco de segmento si > 50.
        tsv = _tsv(
            # renglón 1: centro y = 110
            (5, 10, 100, 60, 20, 90, "COD123"),
            (5, 90, 104, 100, 20, 90, "TORNILLO"),     # hueco 20  -> mismo segmento
            (5, 300, 98, 40, 20, 90, "10,00"),         # hueco 100 -> segmento nuevo
            # renglón 2: centro y = 140 (dy 30 > 11)
            (5, 10, 130, 60, 20, 90, "COD999"),
            (5, 100, 130, 80, 20, 90, "TUERCA"),       # hueco 30 <= 50 -> mismo segmento
        )
        lines = engine.tsv_lines(tsv)
        self.assertEqual(
            lines,
            [
                {"text": "COD123 TORNILLO 10,00", "segments": ["COD123 TORNILLO", "10,00"]},
                {"text": "COD999 TUERCA", "segments": ["COD999 TUERCA"]},
            ],
        )

    def test_gap_exactly_25h_is_same_segment(self):
        # h = 20; right = 10+40 = 50; left 100 -> hueco 50 == 2.5*h -> no se parte (estricto >).
        tsv = _tsv((5, 10, 100, 40, 20, 90, "A"), (5, 100, 100, 40, 20, 90, "B"))
        self.assertEqual(engine.tsv_lines(tsv), [{"text": "A B", "segments": ["A B"]}])

    def test_min_h_is_8_and_vertical_threshold_055(self):
        # alturas 6 -> h = max(8, 6) = 8; umbral vertical 4.4; centros 103 y 107.5 (dy 4.5) -> renglones distintos.
        tsv = _tsv((5, 10, 100, 40, 6, 90, "uno"), (5, 60, 104, 40, 7, 90, "dos"))
        # centros: 103 y 107.5 -> dy = 4.5 > 4.4
        self.assertEqual([l["text"] for l in engine.tsv_lines(tsv)], ["uno", "dos"])
        tsv = _tsv((5, 10, 100, 40, 6, 90, "uno"), (5, 60, 100, 40, 7, 90, "dos"))
        # centros 103 y 103.5 -> mismo renglón
        self.assertEqual([l["text"] for l in engine.tsv_lines(tsv)], ["uno dos"])

    def test_running_mean_of_row_center(self):
        # La media móvil del centro del renglón evita encadenar derivas: h = 20 (umbral 11).
        # centros 110, 120 (dy 10 -> entra, y=115), 126 (dy 11 -> entra, y=118.67), 131 (dy 12.33 -> fila nueva)
        tsv = _tsv(
            (5, 10, 100, 20, 20, 90, "a"),
            (5, 40, 110, 20, 20, 90, "b"),
            (5, 70, 116, 20, 20, 90, "c"),
            (5, 100, 121, 20, 20, 90, "d"),
        )
        self.assertEqual([l["text"] for l in engine.tsv_lines(tsv)], ["a b c", "d"])

    def test_quality_score(self):
        tsv = _tsv(
            (5, 0, 0, 10, 20, 95, "ALBARAN"),   # cuenta
            (5, 0, 0, 10, 20, 59, "FACTURA"),   # conf < 60
            (5, 0, 0, 10, 20, 90, "12"),        # < 3 letras
            (5, 0, 0, 10, 20, 70, "Ñoño"),      # cuenta (letras unicode)
        )
        self.assertEqual(engine.quality(tsv), 165.0)


class PageCountTests(unittest.TestCase):
    def test_pages_count_max(self):
        data = b"<< /Type /Pages /Kids [] /Count 3 >> << /Type /Pages /Count 14 >>"
        self.assertEqual(engine.pdf_page_count(data), 14)

    def test_fallback_counts_page_objects(self):
        data = b"<< /Type /Page /X 1>> << /Type /Pages /Kids >> << /Type /Page >>"
        self.assertEqual(engine.pdf_page_count(data), 2)

    def test_no_pages_raises(self):
        with patch.object(engine, "_run", side_effect=OcrError("ocr_failed", "x")):
            with self.assertRaises(OcrError) as ctx:
                engine.pdf_page_count(b"%PDF-1.4 nada")
        self.assertEqual(ctx.exception.code, "no_pages")


class ValidationTests(unittest.TestCase):
    def test_mime_mismatch_and_invalid(self):
        with self.assertRaises(OcrError) as ctx:
            engine.validate_input(_png(), "image/jpeg")
        self.assertEqual(ctx.exception.code, "mime_mismatch")
        with self.assertRaises(OcrError) as ctx:
            engine.validate_input(b"hola", "text/plain")
        self.assertEqual(ctx.exception.code, "invalid_mime")
        with self.assertRaises(OcrError) as ctx:
            engine.validate_input(b"", "image/png")
        self.assertEqual(ctx.exception.code, "empty_file")

    def test_kinds(self):
        self.assertEqual(engine.validate_input(_png(), "image/png"), "png")
        self.assertEqual(engine.validate_input(b"%PDF-1.7\n", "application/pdf; charset=binary"), "pdf")

    def test_size_limit(self):
        with patch.object(engine.settings, "ocr_max_bytes", 10):
            with self.assertRaises(OcrError) as ctx:
                engine.validate_input(_png(), "image/png")
        self.assertEqual(ctx.exception.status, 413)

    def test_lang(self):
        self.assertEqual(engine.validate_lang("spa+eng"), "spa+eng")
        for bad in ("", "spa;rm", "../x", "a", "spa eng"):
            with self.assertRaises(OcrError):
                engine.validate_lang(bad)


class ImageOpsTests(unittest.TestCase):
    def test_blank_vs_text(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            white = os.path.join(d, "w.png")
            Image.new("RGB", (600, 400), "white").save(white)
            self.assertTrue(engine.is_blank_file(white))
            # Gris suave (min canal >= 160) no cuenta como tinta
            soft = os.path.join(d, "s.png")
            Image.new("RGB", (600, 400), (200, 200, 200)).save(soft)
            self.assertTrue(engine.is_blank_file(soft))
            ink = os.path.join(d, "i.png")
            img = Image.new("L", (600, 400), 255)
            ImageDraw.Draw(img).rectangle((50, 50, 400, 120), fill=0)
            img.save(ink)
            self.assertFalse(engine.is_blank_file(ink))
            # Imagen ilegible = en blanco (mismo criterio que el motor original).
            bad = os.path.join(d, "b.png")
            open(bad, "wb").write(b"no es png")
            self.assertTrue(engine.is_blank_file(bad))

    def test_max_channel_gray_erases_yellow_and_blue_ink(self):
        img = Image.new("RGB", (3, 1))
        img.putdata([(255, 255, 0), (0, 0, 200), (0, 0, 0)])  # subrayado amarillo, boli azul, texto negro
        gray = engine.max_channel_gray(img)
        self.assertEqual(gray.mode, "L")
        self.assertEqual(list(gray.getdata()), [255, 200, 0])

    def test_gray_input_untouched_and_alpha_on_white(self):
        g = Image.new("L", (2, 2), 17)
        self.assertIs(engine.max_channel_gray(g), g)
        rgba = Image.new("RGBA", (1, 1), (0, 0, 0, 0))  # transparente -> blanco
        self.assertEqual(list(engine.max_channel_gray(rgba).getdata()), [255])

    def test_rotation_is_clockwise(self):
        img = Image.new("L", (4, 2), 255)
        img.putpixel((0, 0), 0)  # arriba-izquierda
        cw90 = engine._rotate_clockwise(img, 90)
        self.assertEqual(cw90.size, (2, 4))
        self.assertEqual(cw90.getpixel((1, 0)), 0)  # arriba-derecha
        cw180 = engine._rotate_clockwise(img, 180)
        self.assertEqual(cw180.getpixel((3, 1)), 0)  # abajo-derecha
        cw270 = engine._rotate_clockwise(img, 270)
        self.assertEqual(cw270.getpixel((0, 3)), 0)  # abajo-izquierda
        self.assertIs(engine._rotate_clockwise(img, 0), img)


def _fake_run_factory(*, tsv_by_image=None, osd="", pdftotext="", calls=None):
    """_run falso: despacha por binario. tsv_by_image: función(path)->tsv."""

    def fake(cmd, timeout, deadline, cwd):
        if calls is not None:
            calls.append(list(cmd))
        name = os.path.basename(cmd[0])
        if name == "pdftotext":
            return pdftotext, ""
        if name == "tesseract":
            if "--psm" in cmd and cmd[cmd.index("--psm") + 1] == "0":
                return osd, ""
            return tsv_by_image(cmd[1]), ""
        raise AssertionError(f"binario inesperado {name}")

    return fake


class ReadPageFlowTests(unittest.TestCase):
    def test_blank_image_skips_tesseract(self):
        calls = []
        with patch.object(engine, "_run", _fake_run_factory(calls=calls)):
            res = engine.read_page(_png(), "image/png")
        self.assertEqual((res.source, res.rotation, res.lines, res.page, res.page_count), ("blank", 0, [], 1, 1))
        self.assertEqual(calls, [])

    def test_image_osd_zero_means_no_rotation_and_params(self):
        calls = []
        tsv = _tsv((5, 10, 100, 60, 20, 90, "HOLA"), (5, 90, 100, 60, 20, 80, "MUNDO"))
        fake = _fake_run_factory(
            tsv_by_image=lambda p: tsv,
            osd="Page number: 0\nOrientation in degrees: 0\nRotate: 0\nOrientation confidence: 9.5\n",
            calls=calls,
        )
        with patch.object(engine, "_run", fake):
            res = engine.read_page(_text_png(), "image/png", lang="spa+eng", psm=6)
        self.assertEqual(res.source, "tesseract")
        self.assertEqual(res.rotation, 0)
        self.assertEqual(res.lines, [{"text": "HOLA MUNDO", "segments": ["HOLA MUNDO"]}])
        self.assertEqual([w.text for w in res.words], ["HOLA", "MUNDO"])
        final = calls[-1]
        self.assertEqual(final[2], "stdout")
        self.assertEqual(final[final.index("-l") + 1], "spa+eng")
        self.assertEqual(final[final.index("--psm") + 1], "6")
        self.assertIn("tessedit_create_tsv=1", final)
        self.assertNotIn("user_defined_dpi=300", final)  # imágenes: sin dpi forzado

    def test_osd_rotation_confident_then_quality_check(self):
        # OSD propone 90 con confianza alta; la lectura girada es mucho mejor -> rotation 90.
        seen = []

        def tsv_for(path):
            seen.append(os.path.basename(path))
            if os.path.basename(path) == "check-turned.png":
                return _tsv((5, 0, 0, 10, 20, 95, "ALBARAN"), (5, 0, 0, 10, 20, 95, "FACTURA"))
            if os.path.basename(path) == "ocr.png":
                return _tsv((5, 10, 100, 60, 20, 90, "TEXTO"))
            return _tsv((5, 0, 0, 10, 20, 30, "xx"))

        fake = _fake_run_factory(tsv_by_image=tsv_for, osd="Rotate: 90\nOrientation confidence: 7.0\n")
        with patch.object(engine, "_run", fake):
            res = engine.read_page(_text_png(), "image/png")
        self.assertEqual(res.rotation, 90)
        self.assertIn("check-turned.png", seen)

    def test_osd_low_confidence_returns_zero_without_extra_passes(self):
        seen = []

        def tsv_for(path):
            seen.append(os.path.basename(path))
            return _tsv((5, 10, 100, 60, 20, 90, "TEXTO"))

        fake = _fake_run_factory(tsv_by_image=tsv_for, osd="Rotate: 180\nOrientation confidence: 1.5\n")
        with patch.object(engine, "_run", fake):
            res = engine.read_page(_text_png(), "image/png")
        self.assertEqual(res.rotation, 0)
        self.assertNotIn("check-turned.png", seen)

    def test_no_osd_tries_270_and_requires_30_percent_better(self):
        def tsv_for(path):
            name = os.path.basename(path)
            if name == "check-turned.png":  # 130 vs 100 -> no supera 1.3x (estricto)
                return _tsv((5, 0, 0, 10, 20, 65, "ALBARAN"), (5, 0, 0, 10, 20, 65, "FACTURA"))
            if name == "check.png":
                return _tsv((5, 0, 0, 10, 20, 50, "ALBARAN"), (5, 0, 0, 10, 20, 50, "FACTURA"))
            return _tsv((5, 10, 100, 60, 20, 90, "TEXTO"))

        # Con 50/50 -> quality 0 (conf < 60) y turned 130 > 0 => gira 270.
        fake = _fake_run_factory(tsv_by_image=tsv_for, osd="")
        with patch.object(engine, "_run", fake):
            res = engine.read_page(_text_png(), "image/png")
        self.assertEqual(res.rotation, 270)

        def tsv_equal(path):
            name = os.path.basename(path)
            if name in ("check-turned.png", "check.png"):
                return _tsv((5, 0, 0, 10, 20, 100, "ALBARAN"))
            return _tsv((5, 10, 100, 60, 20, 90, "TEXTO"))

        with patch.object(engine, "_run", _fake_run_factory(tsv_by_image=tsv_equal, osd="")):
            res = engine.read_page(_text_png(), "image/png")
        self.assertEqual(res.rotation, 0)

    def test_image_page_out_of_range(self):
        with self.assertRaises(OcrError) as ctx:
            engine.read_page(_png(), "image/png", page=2)
        self.assertEqual(ctx.exception.code, "page_out_of_range")

    def test_pdf_text_source_and_segments(self):
        text = (
            "ALBARAN   0012345      FECHA 01/02/2024\n"
            "\n"
            "Linea de prueba con bastante texto para superar el umbral de letras minimo   10,00   25,50\n"
            "otra linea con mas letras todavia para estar seguros de que hay texto real en la pagina\n"
        )
        calls = []
        with patch.object(engine, "_run", _fake_run_factory(pdftotext=text, calls=calls)):
            res = engine.read_page(b"%PDF-1.4\n<< /Type /Pages /Count 2 >>", "application/pdf", page=2)
        self.assertEqual((res.source, res.page, res.page_count, res.rotation), ("pdf_text", 2, 2, 0))
        self.assertEqual(res.lines[0], {"text": "ALBARAN 0012345 FECHA 01/02/2024", "segments": ["ALBARAN", "0012345", "FECHA 01/02/2024"]})
        self.assertEqual(len(res.lines), 3)
        self.assertEqual(res.lines[1]["segments"][-2:], ["10,00", "25,50"])
        self.assertEqual(calls[0][1:6], ["-layout", "-f", "2", "-l", "2"])

    def test_pdf_few_letters_falls_back_to_ocr_branch(self):
        calls = []

        def fake(cmd, timeout, deadline, cwd):
            calls.append(os.path.basename(cmd[0]))
            if cmd[0] == "pdftotext":
                return "12 34\n", ""
            if cmd[0] == "pdftoppm":
                dpi = cmd[cmd.index("-r") + 1]
                Image.new("L" if "-gray" in cmd else "RGB", (400, 300), 255).save(cmd[-1] + ".png")
                calls.append(("pdftoppm", dpi, "-gray" in cmd))
                return "", ""
            raise AssertionError(cmd)

        with patch.object(engine, "_run", fake):
            res = engine.read_page(b"%PDF-1.4\n<< /Type /Pages /Count 1 >>", "application/pdf")
        self.assertEqual(res.source, "blank")  # el render a 150 ppp en gris es blanco
        self.assertIn(("pdftoppm", "150", True), calls)


class TimeoutAndToolsTests(unittest.TestCase):
    def test_deadline_cap(self):
        d = engine.Deadline(100)
        self.assertLessEqual(d.cap(5), 5)
        d.end = 0
        with self.assertRaises(engine.OcrTimeout):
            d.cap(5)

    def test_missing_binary_is_503(self):
        d = engine.Deadline(10)
        with patch("app.ocr.engine.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(OcrError) as ctx:
                engine._run(["tesseract"], 5, d, "/tmp")
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("ocr_unavailable", 503))

    def test_subprocess_timeout_and_env(self):
        d = engine.Deadline(10)
        with patch("app.ocr.engine.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            with self.assertRaises(engine.OcrTimeout):
                engine._run(["tesseract"], 5, d, "/tmp")
        proc = MagicMock(returncode=0, stdout=b"ok", stderr=b"")
        with patch("app.ocr.engine.subprocess.run", return_value=proc) as run:
            self.assertEqual(engine._run(["x"], 5, d, "/tmp"), ("ok", ""))
        kw = run.call_args.kwargs
        self.assertEqual(kw["env"]["OMP_THREAD_LIMIT"], "1")
        self.assertIs(kw["stdin"], subprocess.DEVNULL)
        self.assertLessEqual(kw["timeout"], 5)

    def test_nonzero_exit_with_stdout_is_accepted(self):
        d = engine.Deadline(10)
        with patch("app.ocr.engine.subprocess.run", return_value=MagicMock(returncode=1, stdout=b"algo", stderr=b"e")):
            self.assertEqual(engine._run(["x"], 5, d, "/tmp")[0], "algo")
        with patch("app.ocr.engine.subprocess.run", return_value=MagicMock(returncode=1, stdout=b" \n", stderr=b"e")):
            with self.assertRaises(OcrError):
                engine._run(["x"], 5, d, "/tmp")


class SemaphoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrency_limited_to_one(self):
        import threading
        import time

        engine._semaphore = None
        active = {"now": 0, "max": 0}
        lock = threading.Lock()

        def work():
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1
            return 1

        import asyncio

        with patch.object(engine.settings, "ocr_concurrency", 1):
            engine._semaphore = None
            res = await asyncio.gather(*[engine.run_limited(work) for _ in range(4)])
        engine._semaphore = None
        self.assertEqual(res, [1, 1, 1, 1])
        self.assertEqual(active["max"], 1)

    async def test_busy_when_no_slot(self):
        import asyncio

        engine._semaphore = asyncio.Semaphore(1)
        await engine._semaphore.acquire()
        try:
            with patch.object(engine.settings, "ocr_timeout", 0.05):
                with self.assertRaises(OcrError) as ctx:
                    await engine.run_limited(lambda: 1)
            self.assertEqual((ctx.exception.code, ctx.exception.status), ("ocr_busy", 503))
        finally:
            engine._semaphore.release()
            engine._semaphore = None


class RouterTests(unittest.TestCase):
    def setUp(self):
        from app.main import app

        self.app = app
        auth = AskAuth(mode="access_token", user_id="u1", project_id="p1", access_token_id="t1", scopes=["ask:read"])
        app.dependency_overrides[ask_auth] = lambda: auth
        self.pool = MagicMock()
        self.pool.fetchval = AsyncMock(return_value="00000000-0000-0000-0000-000000000001")
        app.state.pool = self.pool
        app.state.redis = None
        self.client = TestClient(app)

    def tearDown(self):
        self.app.dependency_overrides.clear()

    def _post(self, **over):
        body = {"data_base64": base64.b64encode(_png()).decode(), "mime": "image/png"}
        body.update(over)
        return self.client.post("/v1/ocr", json=body)

    def test_route_registered_in_openapi(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertIn("/v1/ocr", paths)

    def test_requires_auth(self):
        self.app.dependency_overrides.clear()
        r = self.client.post("/v1/ocr", json={"data_base64": "AA==", "mime": "image/png"})
        self.assertEqual(r.status_code, 401)

    def test_blank_image_contract(self):
        r = self._post()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"page": 1, "page_count": 1, "source": "blank", "rotation": 0, "lines": []})
        args = self.pool.fetchval.call_args.args
        self.assertIn("INSERT INTO requests", args[0])
        self.assertNotIn(base64.b64encode(_png()).decode(), str(args))  # nunca el contenido

    def test_tesseract_result_with_words(self):
        res = PageResult(
            1, 1, "tesseract", 90,
            [{"text": "A B", "segments": ["A", "B"]}],
            [Word("A", 91.0, 1, 2, 3, 4), Word("B", 80.0, 10, 2, 3, 4)],
        )
        with patch.object(engine, "read_page", return_value=res):
            r = self._post(words=True, psm=6, lang="eng")
            self.assertEqual(r.status_code, 200)
            j = r.json()
            self.assertEqual(j["lines"], [{"text": "A B", "segments": ["A", "B"]}])
            self.assertEqual(j["words"][0], {"text": "A", "conf": 91.0, "bbox": {"left": 1, "top": 2, "width": 3, "height": 4}})
            self.assertEqual(j["rotation"], 90)
            self.assertNotIn("words", self._post().json())

    def test_errors(self):
        r = self._post(mime="image/jpeg")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "mime_mismatch"))
        r = self._post(mime="text/plain")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "invalid_mime"))
        r = self._post(lang="spa;rm -rf")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "invalid_lang"))
        r = self._post(page=3)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "page_out_of_range"))
        self.assertEqual(self._post(psm=99).status_code, 422)
        self.assertEqual(self._post(page=0).status_code, 422)
        r = self._post(data_base64="")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "empty_file"))

    def test_too_large(self):
        with patch.object(engine.settings, "ocr_max_bytes", 100):
            r = self._post(data_base64=base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 200).decode())
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (413, "file_too_large"))

    def test_rate_limit(self):
        from fastapi import HTTPException

        with patch("app.routers.ocr.ratelimit.check", AsyncMock(side_effect=HTTPException(429, {"code": "rate_limited", "message": "x"}))):
            self.assertEqual(self._post().status_code, 429)

    def test_trace_failure_does_not_break_response(self):
        self.pool.fetchval = AsyncMock(side_effect=RuntimeError("db caída"))
        self.assertEqual(self._post().status_code, 200)


@unittest.skipUnless(
    shutil.which("tesseract") and shutil.which("pdftoppm") and shutil.which("pdftotext"),
    "requiere tesseract y poppler-utils",
)
class RealBinariesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
        cls.lang = "spa" if "spa" in langs.split() else "eng"
        cls.osd = "osd" in langs.split()

    def test_image_text(self):
        res = engine.read_page(_text_png(), "image/png", lang=self.lang)
        self.assertEqual(res.source, "tesseract")
        joined = " ".join(l["text"] for l in res.lines).upper()
        self.assertIn("FACTURA", joined)
        self.assertIn("12345", joined)

    def test_rotated_image_is_corrected(self):
        if not self.osd:
            self.skipTest("sin osd.traineddata")
        # OSD necesita texto natural y suficiente: una página con varios renglones en minúsculas.
        page = Image.new("RGB", (1240, 1400), "white")
        draw = ImageDraw.Draw(page)
        font = ImageDraw.ImageDraw(page).getfont().font_variant(size=40)
        frases = [
            "Albaran de entrega numero uno del proveedor",
            "Referencia del articulo y descripcion completa",
            "Cantidad recibida segun el pedido de compra",
            "La mercancia se entrega en el almacen central",
            "Fecha de entrega prevista para la semana proxima",
            "Observaciones generales sobre el transporte",
        ]
        for i in range(14):
            draw.text((40, 30 + i * 90), frases[i % len(frases)], fill="black", font=font)
        for ccw, expected in ((0, 0), (90, 90), (270, 270), (180, 180)):
            buf = io.BytesIO()
            page.rotate(ccw, expand=True, fillcolor="white").save(buf, "PNG")
            res = engine.read_page(buf.getvalue(), "image/png", lang=self.lang)
            # Girada ccw grados => hay que girarla ccw grados en sentido contrario (horario = ccw).
            self.assertEqual(res.rotation, expected, f"ccw={ccw}")
            self.assertIn("ENTREGA", " ".join(line["text"] for line in res.lines).upper())

    def test_blank_pdf_scan(self):
        buf = io.BytesIO()
        Image.new("RGB", (1240, 1754), "white").save(buf, "PDF", resolution=150)
        res = engine.read_page(buf.getvalue(), "application/pdf", lang=self.lang)
        self.assertEqual((res.source, res.lines, res.page_count), ("blank", [], 1))

    def test_scanned_pdf_goes_through_tesseract(self):
        buf = io.BytesIO()
        Image.open(io.BytesIO(_text_png())).save(buf, "PDF", resolution=150)
        res = engine.read_page(buf.getvalue(), "application/pdf", lang=self.lang)
        self.assertEqual(res.source, "tesseract")
        self.assertIn("FACTURA", " ".join(l["text"] for l in res.lines).upper())

    def test_text_pdf_uses_pdftotext(self):
        res = engine.read_page(_text_pdf(), "application/pdf", lang=self.lang)
        self.assertEqual((res.source, res.page_count), ("pdf_text", 1))
        self.assertIn("Albaran", res.lines[0]["text"])

    def test_render_page_png(self):
        png = engine.render_page(_text_pdf(), 1, 72)
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.format, "PNG")
        self.assertGreater(img.width, 100)
        with self.assertRaises(OcrError):
            engine.render_page(_text_pdf(), 2, 72)


if __name__ == "__main__":
    unittest.main()
