"""Motor OCR genérico: PDF/imagen -> renglones visuales ``{text, segments}``.

Pipeline por página (mismo orden y umbrales que el motor de producción en PHP que se portó):

1. PDF con texto real  -> ``pdftotext -layout`` (``source = "pdf_text"``) si hay >= 80 letras.
2. Si no, render a 150 ppp en gris; si es una hoja en blanco -> ``source = "blank"`` y ``lines = []``.
3. Orientación: OSD de Tesseract (``--psm 0``) con confianza mínima 2.0; si no hay OSD o no es
   fiable, se prueba a girar 270° y se compara la calidad (suma de ``conf`` de palabras con
   ``conf >= 60`` y >= 3 letras) con un factor 1.3.
4. Render a 300 ppp en color (PDF) o la imagen original, gris = máximo de R, G y B (canal B de HSB:
   borra subrayado amarillo / boli azul y deja el texto negro) y giro.
5. ``tesseract --psm N -l LANG`` en TSV y agrupación de palabras en renglones:
   ``height > 4`` y ``conf > 10``; altura mediana ``h = max(8, mediana)``; mismo renglón si
   ``|yc - y| <= 0.55 * h``; nuevo segmento si el hueco horizontal ``> 2.5 * h``.

Todo es síncrono y bloqueante: el router lo ejecuta con ``run_limited`` (threadpool + semáforo).
Subprocesos con timeout y ``OMP_THREAD_LIMIT=1``. No hay reglas de negocio de ningún cliente aquí.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from PIL import Image, ImageChops

from app.config import settings

T = TypeVar("T")

OCR_DPI = 300
CHECK_DPI = 150
CHECK_MAX_LONG_EDGE = 1754  # A4 a 150 ppp
OSD_MIN_CONFIDENCE = 2.0
PDF_TEXT_MIN_LETTERS = 80
MAX_IMAGE_PIXELS = 120_000_000

# Timeouts por subproceso (s); además los limita el techo total ``ASKBOX_OCR_TIMEOUT``.
T_PDFTOTEXT = 30
T_PDFTOPPM = 60
T_OSD = 20
T_TESSERACT = 45
T_PDFINFO = 15

ALLOWED_MIMES = ("application/pdf", "image/png", "image/jpeg", "image/webp")

_PHP_TRIM = " \t\n\r\0\x0b"
_LANG_RE = re.compile(r"^[A-Za-z0-9_]{2,24}(\+[A-Za-z0-9_]{2,24}){0,3}$")


class OcrError(Exception):
    """Error controlado del motor; el router lo traduce a ``{"error": {code, message}}``."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class OcrTimeout(OcrError):
    def __init__(self, message: str = "La lectura OCR ha tardado demasiado."):
        super().__init__("ocr_timeout", message, 504)


class Deadline:
    """Techo total por petición (monotónico)."""

    def __init__(self, seconds: float):
        self.end = time.monotonic() + max(1.0, seconds)

    def remaining(self) -> float:
        return self.end - time.monotonic()

    def expired(self) -> bool:
        return self.remaining() <= 0

    def cap(self, seconds: float) -> float:
        left = self.remaining()
        if left <= 0:
            raise OcrTimeout()
        return min(float(seconds), left)


@dataclass
class Word:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int


@dataclass
class PageResult:
    page: int
    page_count: int
    source: str  # pdf_text | tesseract | blank
    rotation: int = 0
    lines: list[dict[str, Any]] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Subprocesos
# ---------------------------------------------------------------------------

def _tool_path(name: str) -> str:
    return name  # resuelto por PATH (poppler-utils y tesseract-ocr en la imagen Docker)


def _run(cmd: list[str], timeout: float, deadline: Deadline, cwd: str) -> tuple[str, str]:
    """Ejecuta sin shell. Falla si el código != 0 y stdout vacío (como el motor original)."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": cwd,
        "OMP_THREAD_LIMIT": "1",
        "LC_ALL": "C",
    }
    if settings.ocr_tessdata_prefix:
        env["TESSDATA_PREFIX"] = settings.ocr_tessdata_prefix
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=deadline.cap(timeout),
            cwd=cwd,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise OcrTimeout() from None
    except FileNotFoundError:
        raise OcrError("ocr_unavailable", f"{os.path.basename(cmd[0])} no está disponible en el servidor.", 503) from None
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0 and stdout.strip(_PHP_TRIM) == "":
        raise OcrError("ocr_failed", "La lectura OCR ha fallado.", 422)
    return stdout, stderr


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def sniff_kind(data: bytes) -> str | None:
    """'pdf' | 'png' | 'jpeg' | 'webp' | None según los magic bytes."""
    if b"%PDF-" in data[:1024]:  # la especificación tolera basura antes de la cabecera
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


_MIME_KIND = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp"}


def validate_input(data: bytes, mime: str) -> str:
    """Valida mime declarado contra el contenido. Devuelve el 'kind'."""
    declared = _MIME_KIND.get((mime or "").split(";")[0].strip().lower())
    if declared is None:
        raise OcrError("invalid_mime", "mime debe ser application/pdf, image/png, image/jpeg o image/webp.", 422)
    if not data:
        raise OcrError("empty_file", "El archivo está vacío.", 422)
    if len(data) > settings.ocr_max_bytes:
        raise OcrError("file_too_large", f"Archivo demasiado grande (máx {settings.ocr_max_bytes} bytes).", 413)
    actual = sniff_kind(data)
    if actual != declared:
        raise OcrError("mime_mismatch", "El contenido no coincide con el mime declarado.", 422)
    return declared


def validate_lang(lang: str) -> str:
    if not _LANG_RE.match(lang or ""):
        raise OcrError("invalid_lang", "lang no válido (p. ej. spa, eng, spa+eng).", 422)
    return lang


def _letter_count(text: str) -> int:
    return sum(1 for ch in text if ch.isalpha())


def _open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_IMAGE_PIXELS:
            raise OcrError("image_too_large", "La imagen tiene demasiados píxeles.", 413)
        img.load()
        return img
    except OcrError:
        raise
    except Exception:  # noqa: BLE001  (PIL lanza tipos variados con datos corruptos)
        raise OcrError("invalid_image", "No se pudo abrir la imagen.", 422) from None


# ---------------------------------------------------------------------------
# Páginas y render
# ---------------------------------------------------------------------------

def pdf_page_count(data: bytes, *, deadline: Deadline | None = None, work: str | None = None) -> int:
    """Páginas de un PDF: ``/Type /Pages … /Count N`` (máximo); si no, nº de ``/Type /Page``.

    Mejora aditiva: si el PDF usa object streams y no hay marcas legibles, ``pdfinfo``.
    """
    count = 0
    counts = re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data, re.S)
    if counts:
        count = max(int(c) for c in counts)
    if count < 1:
        count = len(re.findall(rb"/Type\s*/Page\b(?!s)", data))
    if count < 1:
        count = _pdfinfo_pages(data, deadline or Deadline(settings.ocr_timeout), work)
    if count < 1:
        raise OcrError("no_pages", "No se han encontrado páginas en el PDF.", 422)
    return count


def _pdfinfo_pages(data: bytes, deadline: Deadline, work: str | None) -> int:
    def go(directory: str) -> int:
        path = os.path.join(directory, "count.pdf")
        with open(path, "wb") as fh:
            fh.write(data)
        try:
            out, _ = _run([_tool_path("pdfinfo"), path], T_PDFINFO, deadline, directory)
        except OcrError:
            return 0
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        return int(m.group(1)) if m else 0

    if work:
        return go(work)
    with tempfile.TemporaryDirectory(prefix="askbox-ocr-") as tmp:
        return go(tmp)


def _pdf_to_image(pdf_path: str, page: int, dpi: int, prefix: str, *, gray: bool, deadline: Deadline, cwd: str) -> str:
    cmd = [_tool_path("pdftoppm"), "-f", str(page), "-l", str(page), "-r", str(dpi)]
    if gray:
        cmd.append("-gray")
    cmd += ["-png", "-singlefile", pdf_path, prefix]
    _run(cmd, T_PDFTOPPM, deadline, cwd)
    out = prefix + ".png"
    if not os.path.isfile(out):
        raise OcrError("render_failed", "No se pudo convertir la página a imagen.", 422)
    return out


def render_page(
    pdf_bytes: bytes,
    page: int,
    dpi: int | None = None,
    *,
    gray: bool = False,
    timeout: float | None = None,
) -> bytes:
    """Rasteriza una página de PDF a PNG (bytes). Bloqueante: llamar desde threadpool.

    Pensado para reutilizarlo desde ``/v1/ask`` (PDF adjunto). Lanza ``OcrError``.
    """
    deadline = Deadline(timeout or settings.ocr_timeout)
    dpi = int(dpi or settings.pdf_dpi)
    if page < 1:
        raise OcrError("page_out_of_range", "Página fuera de rango.", 422)
    with tempfile.TemporaryDirectory(prefix="askbox-ocr-") as work:
        pdf = os.path.join(work, "input.pdf")
        with open(pdf, "wb") as fh:
            fh.write(pdf_bytes)
        count = pdf_page_count(pdf_bytes, deadline=deadline, work=work)
        if page > count:
            raise OcrError("page_out_of_range", "Página fuera de rango.", 422)
        out = _pdf_to_image(pdf, page, dpi, os.path.join(work, "render"), gray=gray, deadline=deadline, cwd=work)
        with open(out, "rb") as fh:
            return fh.read()


def pdf_text_lines(pdf_path: str, page: int, deadline: Deadline, cwd: str) -> list[dict[str, Any]] | None:
    """Texto real del PDF (documentos digitales). None si la página es un escaneo."""
    try:
        out, _ = _run(
            [_tool_path("pdftotext"), "-layout", "-f", str(page), "-l", str(page), pdf_path, "-"],
            T_PDFTOTEXT,
            deadline,
            cwd,
        )
    except OcrTimeout:
        raise
    except OcrError:
        return None
    if _letter_count(out) < PDF_TEXT_MIN_LETTERS:
        return None
    lines: list[dict[str, Any]] = []
    for row in re.split(r"\r\n|\n|\r", out):
        if row.strip(_PHP_TRIM) == "":
            continue
        parts = re.split(r"[ \t\n\r\f\v]{3,}", row.strip(_PHP_TRIM))
        segments = [p.strip(_PHP_TRIM) for p in parts]
        segments = [s for s in segments if s != ""]
        lines.append({"text": " ".join(segments), "segments": segments})
    return lines


# ---------------------------------------------------------------------------
# Imagen: blanco, giro, preproceso
# ---------------------------------------------------------------------------

def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        return bg.convert("RGB")
    return img.convert("RGB")


def _scaled_copy(source_img: Image.Image, max_long: int, dest: str) -> str:
    img = _to_rgb(source_img) if source_img.mode not in ("L", "RGB") else source_img
    long_edge = max(img.width, img.height)
    if max_long > 0 and long_edge > max_long:
        scale = max_long / long_edge
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.BILINEAR,
        )
    img.save(dest, "PNG")
    return dest


def is_blank_file(path: str) -> bool:
    """Hoja en blanco: < 0.3 % de píxeles oscuros (min(R,G,B) < 160) muestreando cada 3 px."""
    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            w, h = rgb.size
            raw = rgb.tobytes()
    except Exception:  # noqa: BLE001
        return True
    dark = 0
    seen = 0
    stride = w * 3
    for y in range(0, h, 3):
        row = raw[y * stride:(y + 1) * stride]
        r, g, b = row[0::9], row[1::9], row[2::9]
        n = len(r)
        seen += n
        dark += sum(1 for i in range(n) if min(r[i], g[i], b[i]) < 160)
    return seen == 0 or (dark / seen) < 0.003


def _rotate_clockwise(img: Image.Image, clockwise: int) -> Image.Image:
    clockwise %= 360
    if clockwise == 0:
        return img
    if clockwise == 90:
        return img.transpose(Image.ROTATE_270)
    if clockwise == 180:
        return img.transpose(Image.ROTATE_180)
    if clockwise == 270:
        return img.transpose(Image.ROTATE_90)
    fill = 255 if img.mode == "L" else (255, 255, 255)
    return img.rotate(-clockwise, expand=True, fillcolor=fill)


def rotate_file(source: str, clockwise: int, dest: str) -> str:
    try:
        with Image.open(source) as im:
            rotated = _rotate_clockwise(im if im.mode in ("L", "RGB") else _to_rgb(im), clockwise)
            rotated.save(dest, "PNG")
    except Exception:  # noqa: BLE001
        raise OcrError("rotate_failed", "No se pudo girar la página.", 422) from None
    return dest


def max_channel_gray(img: Image.Image) -> Image.Image:
    """Gris = máximo de los tres canales (canal B de HSB). Si ya es gris, se deja igual."""
    if img.mode == "L":
        return img
    if img.mode in ("1", "I;16", "I", "F"):
        return img.convert("L")
    r, g, b = _to_rgb(img).split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def prepare_for_ocr(source: str, clockwise: int, dest: str) -> str:
    try:
        with Image.open(source) as im:
            im.load()
            dpi = im.info.get("dpi")
            gray = max_channel_gray(im)
            gray = _rotate_clockwise(gray, clockwise)
            kwargs = {"dpi": dpi} if dpi else {}
            gray.save(dest, "PNG", **kwargs)
        return dest
    except Exception:  # noqa: BLE001
        return source if clockwise == 0 else rotate_file(source, clockwise, dest)


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------

def tesseract(
    image: str,
    fmt: str,
    dpi: int | None,
    *,
    lang: str,
    psm: int,
    deadline: Deadline,
    cwd: str,
) -> str:
    cmd = [_tool_path("tesseract"), image, "stdout", "-l", lang, "--psm", str(psm)]
    if dpi is not None:
        cmd += ["-c", f"user_defined_dpi={dpi}"]
    if fmt == "tsv":
        cmd += ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
    out, _ = _run(cmd, T_TESSERACT, deadline, cwd)
    return out


def tsv_words(tsv: str) -> list[Word]:
    words: list[Word] = []
    for i, row in enumerate(re.split(r"\r\n|\n|\r", tsv)):
        if i == 0 or row == "":
            continue
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip(_PHP_TRIM)
        if text == "":
            continue
        try:
            words.append(Word(text, float(cols[10]), int(cols[6]), int(cols[7]), int(cols[8]), int(cols[9])))
        except ValueError:
            continue
    return words


def quality(tsv: str) -> float:
    score = 0.0
    for w in tsv_words(tsv):
        if w.conf >= 60 and _letter_count(w.text) >= 3:
            score += w.conf
    return score


def usable_words(words: list[Word]) -> list[Word]:
    return [w for w in words if w.height > 4 and w.conf > 10]


def group_lines(words: list[Word]) -> list[dict[str, Any]]:
    """Agrupa palabras (ya filtradas) por altura en renglones y segmentos."""
    if not words:
        return []
    heights = sorted(w.height for w in words)
    h = max(8, heights[len(heights) // 2])
    ordered = sorted(words, key=lambda w: w.top + w.height / 2)

    rows: list[dict[str, Any]] = []
    for word in ordered:
        yc = word.top + word.height / 2
        if rows and abs(yc - rows[-1]["y"]) <= 0.55 * h:
            row = rows[-1]
            row["words"].append(word)
            row["y"] += (yc - row["y"]) / len(row["words"])
            continue
        rows.append({"y": yc, "words": [word]})

    lines: list[dict[str, Any]] = []
    for row in rows:
        row_words = sorted(row["words"], key=lambda w: w.left)
        segments: list[str] = []
        current: list[str] = []
        prev_right: float | None = None
        for word in row_words:
            if prev_right is not None and word.left - prev_right > 2.5 * h and current:
                segments.append(" ".join(current))
                current = []
            current.append(word.text)
            prev_right = word.left + word.width
        if current:
            segments.append(" ".join(current))
        lines.append({"text": " ".join(segments), "segments": segments})
    return lines


def tsv_lines(tsv: str) -> list[dict[str, Any]]:
    return group_lines(usable_words(tsv_words(tsv)))


def orientation(check_image: str, *, lang: str, psm: int, deadline: Deadline, cwd: str) -> int:
    """Grados en sentido horario que hay que girar la página (0, 90, 180, 270)."""
    proposed = 270
    try:
        out, err = _run([_tool_path("tesseract"), check_image, "stdout", "--psm", "0"], T_OSD, deadline, cwd)
        text = out + "\n" + err
        rot = re.search(r"Rotate:\s*(\d+)", text)
        conf = re.search(r"Orientation confidence:\s*([\d.]+)", text)
        if rot and conf:
            deg = int(rot.group(1)) % 360
            if deg == 0:
                return 0
            if float(conf.group(1)) < OSD_MIN_CONFIDENCE:
                return 0
            proposed = deg
    except OcrError:
        if deadline.expired():
            raise OcrTimeout() from None
    upright = quality(tesseract(check_image, "tsv", CHECK_DPI, lang=lang, psm=psm, deadline=deadline, cwd=cwd))
    turned = rotate_file(check_image, proposed, os.path.join(os.path.dirname(check_image), "check-turned.png"))
    rotated = quality(tesseract(turned, "tsv", CHECK_DPI, lang=lang, psm=psm, deadline=deadline, cwd=cwd))
    return proposed if rotated > upright * 1.3 else 0


# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------

def read_page(
    data: bytes,
    mime: str,
    page: int = 1,
    lang: str = "spa",
    psm: int = 4,
    *,
    timeout: float | None = None,
) -> PageResult:
    """Lee una página y devuelve renglones. Bloqueante: llamar desde threadpool."""
    kind = validate_input(data, mime)
    lang = validate_lang(lang)
    deadline = Deadline(timeout or settings.ocr_timeout)
    is_pdf = kind == "pdf"

    with tempfile.TemporaryDirectory(prefix="askbox-ocr-") as work:
        os.chmod(work, 0o700)
        if is_pdf:
            source = os.path.join(work, "input.pdf")
            with open(source, "wb") as fh:
                fh.write(data)
            count = pdf_page_count(data, deadline=deadline, work=work)
        else:
            source = os.path.join(work, "input." + kind)
            with open(source, "wb") as fh:
                fh.write(data)
            count = 1
        if page < 1 or page > count:
            raise OcrError("page_out_of_range", "Página fuera de rango.", 422)

        if is_pdf:
            text_lines = pdf_text_lines(source, page, deadline, work)
            if text_lines is not None:
                return PageResult(page, count, "pdf_text", 0, text_lines)
            check = _pdf_to_image(source, page, CHECK_DPI, os.path.join(work, "check"), gray=True, deadline=deadline, cwd=work)
        else:
            img = _open_image(data)
            check = _scaled_copy(img, CHECK_MAX_LONG_EDGE, os.path.join(work, "check.png"))
            img.close()

        if is_blank_file(check):
            return PageResult(page, count, "blank", 0, [])

        rotation = orientation(check, lang=lang, psm=psm, deadline=deadline, cwd=work)
        if is_pdf:
            image = _pdf_to_image(source, page, OCR_DPI, os.path.join(work, "page"), gray=False, deadline=deadline, cwd=work)
        else:
            image = source
        image = prepare_for_ocr(image, rotation, os.path.join(work, "ocr.png"))
        tsv = tesseract(image, "tsv", OCR_DPI if is_pdf else None, lang=lang, psm=psm, deadline=deadline, cwd=work)
        words = usable_words(tsv_words(tsv))
        return PageResult(page, count, "tesseract", rotation, group_lines(words), words)


def page_count(data: bytes, mime: str) -> int:
    kind = validate_input(data, mime)
    if kind == "pdf":
        return pdf_page_count(data)
    _open_image(data).close()
    return 1


# ---------------------------------------------------------------------------
# Concurrencia: threadpool + semáforo (no bloquear el event loop de FastAPI)
# ---------------------------------------------------------------------------

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, settings.ocr_concurrency))
    return _semaphore


async def run_limited(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Ejecuta ``fn`` en el threadpool con ``ASKBOX_OCR_CONCURRENCY`` huecos como máximo.

    Si no hay hueco en ``ASKBOX_OCR_TIMEOUT`` s -> ``OcrError(ocr_busy, 503)``.
    """
    from starlette.concurrency import run_in_threadpool

    sem = _get_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=settings.ocr_timeout)
    except asyncio.TimeoutError:
        raise OcrError("ocr_busy", "OCR ocupado; reintenta en unos segundos.", 503) from None
    try:
        return await run_in_threadpool(lambda: fn(*args, **kwargs))
    finally:
        sem.release()
