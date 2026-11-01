"""Motor TTS con Piper (open source, en local). Sin red ni claves de proveedor.

- Voces .onnx en ASKBOX_PIPER_DIR (por defecto /voices): es_ES-davefx-medium, en_US-lessac-medium.
- Carga perezosa (o en segundo plano al arrancar con ASKBOX_TTS_WARM=true), una síntesis a la vez
  y caché LRU de los últimos resultados.
- style="snake": timbre propio de Snake OS (tono algo más alto, leve coro y sala). Solo numpy.
"""
from __future__ import annotations

import io
import logging
import os
import re
import threading
import wave
from collections import OrderedDict
from typing import Any

log = logging.getLogger("askbox.tts")

VOICE_DIR = os.environ.get("ASKBOX_PIPER_DIR", "/voices")
VOICES = {"es": "es_ES-davefx-medium", "en": "en_US-lessac-medium"}
STYLES = ("plain", "snake")
MAX_CHARS = int(os.environ.get("ASKBOX_TTS_MAX_CHARS", "600") or 600)

_voices: dict[str, Any] = {}
_errors: dict[str, str] = {}
_load_lock = threading.Lock()
_synth_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: "OrderedDict[tuple[str, str, str], bytes]" = OrderedDict()
_CACHE_MAX = 200
_PITCH = 1.07


def norm_lang(lang: str | None) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "es"


def clean(text: str) -> str:
    t = re.sub(r"<[^>]{0,200}>", " ", str(text or ""))
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>|~\[\]{}]", " ", t)
    return " ".join(t.split())[:MAX_CHARS]


def voice(lang: str):
    v = _voices.get(lang)
    if v is not None:
        return v
    with _load_lock:
        v = _voices.get(lang)
        if v is None:
            from piper import PiperVoice  # import perezoso: la API arranca aunque falte Piper

            path = os.path.join(VOICE_DIR, VOICES[lang] + ".onnx")
            if not os.path.exists(path):
                _errors[lang] = "voice_missing"
                raise FileNotFoundError(path)
            v = PiperVoice.load(path)
            _voices[lang] = v
            _errors.pop(lang, None)
    return v


def status() -> dict:
    return {
        "ready": all(l in _voices for l in VOICES),
        "voices": VOICES,
        "loaded": sorted(_voices),
        "errors": dict(_errors),
        "styles": list(STYLES),
        "max_chars": MAX_CHARS,
    }


def _snake_signature(wav_bytes: bytes) -> bytes:
    import numpy as np

    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        sr, n = r.getframerate(), r.getnframes()
        x = np.frombuffer(r.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    t = np.arange(0, len(x) - 1, _PITCH)
    y = np.interp(t, np.arange(len(x)), x)
    i = np.arange(len(y))
    d = (0.009 + 0.0015 * np.sin(2 * np.pi * 0.6 * i / sr)) * sr
    y = y + 0.16 * np.interp(i - d, i, y, left=0.0)
    y = y + 0.22 * np.concatenate(([0.0], np.diff(y)))
    for ms, g in ((23, 0.10), (41, 0.06)):
        k = int(sr * ms / 1000)
        y[k:] += g * y[:-k]
    peak = float(np.max(np.abs(y))) or 1.0
    y = np.clip(y * (0.89 / peak), -1, 1)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    return out.getvalue()


def synth(lang: str, text: str, style: str = "plain") -> bytes:
    """Devuelve un WAV mono 16 bit. Bloqueante: llamar con asyncio.to_thread."""
    key = (lang, style, text)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    v = voice(lang)
    raw = io.BytesIO()
    with _synth_lock:
        with wave.open(raw, "wb") as w:
            if style == "snake":
                try:
                    from piper import SynthesisConfig

                    v.synthesize_wav(text, w, syn_config=SynthesisConfig(
                        length_scale=_PITCH * 0.97, noise_scale=0.6, noise_w_scale=0.75))
                except Exception:
                    v.synthesize_wav(text, w)
            else:
                v.synthesize_wav(text, w)
    data = raw.getvalue()
    if style == "snake":
        try:
            data = _snake_signature(data)
        except Exception:  # sin numpy o fallo de DSP: WAV de Piper sin retocar
            pass
    with _cache_lock:
        _cache[key] = data
        if len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return data


def warm() -> None:
    """Carga las voces en segundo plano (la primera carga tarda ~15 s)."""

    def run():
        for lang in VOICES:
            try:
                voice(lang)
            except Exception as exc:
                _errors.setdefault(lang, type(exc).__name__)
                log.warning("tts: voz %s no disponible: %s", lang, type(exc).__name__)
                return
        log.info("tts: voces listas: %s", ", ".join(VOICES.values()))

    threading.Thread(target=run, name="tts-warm", daemon=True).start()
