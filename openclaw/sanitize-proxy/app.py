"""openclaw-sanitize-proxy: OpenAI-compat -> (masking) -> askBox /v1/ask | Ollama /v1.

Lean local: si el destino es Ollama/allin, recorta system/prompt (~1k tok) y al abortar
intenta unload del modelo en Ollama (UI abort no cancela sola la inferencia).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

ASKBOX = os.environ.get("ASKBOX_UPSTREAM", "http://api:8000").rstrip("/")
OLLAMA = os.environ.get("OLLAMA_UPSTREAM", "http://ollama:11434").rstrip("/")
TOKEN = os.environ.get("ASKBOX_TOKEN", "").strip()
MODE = (os.environ.get("ASKBOX_MODE", "allin").strip() or "allin")
TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "180"))
DEFAULT_MAX_OUT = int(os.environ.get("ASKBOX_MAX_OUTPUT_TOKENS", "512"))
LEAN_SYSTEM_CHARS = int(os.environ.get("LEAN_SYSTEM_CHARS", "500"))
LEAN_PROMPT_CHARS = int(os.environ.get("LEAN_PROMPT_CHARS", "1800"))
LEAN_MAX_OUT = int(os.environ.get("LEAN_MAX_OUTPUT_TOKENS", "128"))
DEFAULT_LOCAL_MODEL = os.environ.get("LEAN_OLLAMA_MODEL", "qwen2.5-coder:14b").strip() or "qwen2.5-coder:14b"
# Lean v2 (2026-11-02): prefijo estable para cache KV de Ollama.
LEAN_KEEP_MESSAGES = int(os.environ.get("LEAN_KEEP_MESSAGES", "2"))
LEAN_SYSTEM = os.environ.get(
    "LEAN_SYSTEM",
    "Eres un asistente en OpenClaw. Quien escribe es el usuario; "
    "nunca digas que tú eres él ni copies su nombre como propio. "
    "Responde en su idioma, breve y directo. Si piden un formato, cúmplelo exacto. No inventes datos.",
).strip()
LEAN_STRIP_ENVELOPE = os.environ.get("LEAN_STRIP_ENVELOPE", "1") not in ("0", "false", "no")
LEAN_DEBUG = os.environ.get("LEAN_DEBUG", "0") in ("1", "true", "yes")

# Fase 2 (2026-11-02): cloud router askBox /v1/route decide perfil de contexto por turno.
# Desactivado por defecto: con ROUTE_ENABLED=0 el comportamiento es idéntico a lean v2.
ROUTE_ENABLED = os.environ.get("ROUTE_ENABLED", "0") in ("1", "true", "yes")
ROUTE_URL = (os.environ.get("ROUTE_URL", "").strip() or f"{ASKBOX}/v1/route")
ROUTE_TIMEOUT_MS = int(os.environ.get("ROUTE_TIMEOUT_MS", "700"))
ROUTE_FASTPATH_CHARS = int(os.environ.get("ROUTE_FASTPATH_CHARS", "200"))
ROUTE_LAST_USER_CHARS = int(os.environ.get("ROUTE_LAST_USER_CHARS", "1500"))
ROUTE_FALLBACK = os.environ.get("ROUTE_FALLBACK", "lean").strip() or "lean"
ROUTE_FULL_SYSTEM_CHARS = int(os.environ.get("ROUTE_FULL_SYSTEM_CHARS", "2400"))
ROUTE_SUMMARY_SYSTEM_CHARS = int(os.environ.get("ROUTE_SUMMARY_SYSTEM_CHARS", "700"))
# Perfiles fijos (prefijos estables → caché KV de Ollama). La respuesta del cloud se CLAMPEA a estos topes.
PROFILES: dict[str, dict[str, Any]] = {
    "lean": {"keep_min": 4, "keep_max": 4, "keep": 4, "systems": ("lean",), "max_out": 128, "prompt_chars": LEAN_PROMPT_CHARS},
    "standard": {"keep_min": 3, "keep_max": 4, "keep": 4, "systems": ("lean", "summary"), "max_out": 128, "prompt_chars": int(os.environ.get("ROUTE_STANDARD_PROMPT_CHARS", "3200"))},
    "full": {"keep_min": 8, "keep_max": 12, "keep": 8, "systems": ("full", "summary"), "max_out": 256, "prompt_chars": int(os.environ.get("ROUTE_FULL_PROMPT_CHARS", "9000"))},
}
_CODE_RX = re.compile(r"```|^\s*(?:def |class |import |from \S+ import |function |const |let |SELECT |#include)|[{};]\s*$|Traceback \(most recent", re.M | re.I)
# Split (2026-11-02, decisión Livian): local SOLO código; chat general → cloud askBox.
SPLIT_ENABLED = os.environ.get("SPLIT_ENABLED", "1") not in ("0", "false", "no")
CHAT_CLOUD_MODEL = (os.environ.get("CHAT_CLOUD_MODEL", "").strip() or os.environ.get("CLOUD_CHAT_MODEL", "").strip() or "cloud-fast")
ROUTE_ON_CHAT = os.environ.get("ROUTE_ON_CHAT", "0") in ("1", "true", "yes")
_CODE_KW_RX = re.compile(
    r"(?i)\b(?:c[oó]digo|code|funci[oó]n|function|script|snippet|bug|debug|depura\w*|refactor\w*|compila\w*|stack ?trace|traceback|"
    r"regex|python|javascript|typescript|node(?:js)?|php|java|golang|rust|bash|shell|sql|html|css|react|vue|docker(?:file)?|"
    r"yaml|json|api|endpoint|clase|class|m[eé]todo|variable|commit|git|pull request|npm|pip|dependenc\w*)\b"
    r"|\.(?:py|js|ts|tsx|jsx|php|go|rs|sh|sql|ya?ml|json|html|css|java|c|cpp|h)\b"
)


def _strip_model_prefix(model: str) -> str:
    if "/" in model:
        prefix, rest = model.split("/", 1)
        if prefix.lower() in ("askbox", "ollama", "ollama-safe") and rest.strip():
            return rest.strip()
    return model


def _is_code_turn(body: dict[str, Any]) -> tuple[bool, str]:
    """Código solo si el ÚLTIMO user lo pide (fences/_CODE_RX/keywords). Historial con fences no pega chat a local."""
    feats = _route_features(body.get("messages") or [], bool(body.get("tools")))
    last = feats["last_user"] or ""
    if _CODE_RX.search(last) or "```" in last:
        return True, "has_code"
    if _CODE_KW_RX.search(last):
        return True, "keyword"
    return False, "chat"


log = logging.getLogger("sanitize")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S), "[PRIVATE_KEY_REDACTED]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/:@]+:[^\s/@]+@"), r"\1[CREDS_REDACTED]@"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "[JWT_REDACTED]"),
    (re.compile(r"\b(?:ab_live_|ab_test_|sk-|sk_live_|xai-|AIza|ghp_|gho_|github_pat_|xox[baprs]-|AKIA)[A-Za-z0-9_\-]{8,}\b"), "[KEY_REDACTED]"),
    (re.compile(r"(?i)\b(?:sage|x3|syracuse)[\w.\-]*(?:key|token|secret|pass(?:word)?|pwd|client[_-]?secret)\b\s*[:=]\s*\S+"), "[X3_SECRET_REDACTED]"),
    (re.compile(r"(?i)\b(pass(?:word|wd)?|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|bearer)\b(\s*[:=]\s*|\s+)(?!\[)[\"']?[^\s\"',;]{6,}"), r"\1\2[SECRET_REDACTED]"),
    (re.compile(r"(?i)(authorization\s*:\s*)(bearer|basic|token)\s+\S+"), r"\1\2 [SECRET_REDACTED]"),
    (re.compile(r"\b[ABCDEFGHJNPQRSUVW]-?\d{7}-?[0-9A-J]\b", re.I), "[CIF_REDACTED]"),
    (re.compile(r"\b(?:\d{8}|[XYZ]\d{7})-?[TRWAGMYFPDXBNJZSQVHLCKE]\b", re.I), "[NIF_REDACTED]"),
    (re.compile(r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2})\b"), "[IP_REDACTED]"),
]


def mask_text(s: str) -> tuple[str, int]:
    n = 0
    for rx, repl in PATTERNS:
        s, k = rx.subn(repl, s)
        n += k
    return s, n


def mask_obj(o: Any, counter: list[int]) -> Any:
    if isinstance(o, str):
        out, n = mask_text(o)
        counter[0] += n
        return out
    if isinstance(o, list):
        return [mask_obj(x, counter) for x in o]
    if isinstance(o, dict):
        return {k: mask_obj(v, counter) for k, v in o.items()}
    return o


def _tail(s: str, n: int) -> str:
    if n <= 0 or len(s) <= n:
        return s
    return "…[trunc]…\n" + s[-n:]


def _head_tail(s: str, n: int) -> str:
    if n <= 0 or len(s) <= n:
        return s
    head = max(200, n // 3)
    tail = n - head - 20
    if tail < 100:
        return _tail(s, n)
    return s[:head] + "\n…[trunc]…\n" + s[-tail:]


_ENV_BLOCK = re.compile(r"(?is)^[^\n]{0,80}\(untrusted[^)]*\):?\s*```(?:json)?\s*.*?```\s*", re.M)
_ENV_TS = re.compile(r"(?m)^\s*\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)?[^\]\n]{0,8}\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?[^\]\n]{0,24}\]\s*")
_ENV_RUNTIME = re.compile(r"^\s*Runtime: agent=\S+ \| session=")
_ENV_KEYS = ("idempotencyKey", "__openclaw", "message_id", "messageId", "sessionKey", "runId", "timestamp", "sender_id", "conversation_label")


def _strip_envelope(text: str) -> str:
    """Quita el envoltorio OpenClaw (metadata JSON, timestamps, ids) del texto de usuario."""
    if not LEAN_STRIP_ENVELOPE or not text:
        return text
    t = text
    st = t.strip()
    if st.startswith("{") and st.endswith("}"):
        try:
            o = json.loads(st)
            if isinstance(o, dict) and any(k in o for k in _ENV_KEYS):
                for k in ("message", "text", "content", "body"):
                    if isinstance(o.get(k), str):
                        t = o[k]
                        break
        except ValueError:
            pass
    t = _ENV_BLOCK.sub("", t)
    lines = [
        ln for ln in t.splitlines()
        if not any(f'"{k}"' in ln for k in _ENV_KEYS) and not _ENV_RUNTIME.match(ln)
    ]
    t = "\n".join(lines)
    t = _ENV_TS.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t or text.strip()


def _default_route() -> dict[str, Any]:
    return {"profile": "default", "keep_messages": LEAN_KEEP_MESSAGES, "system": "lean", "max_out": LEAN_MAX_OUT,
            "prompt_chars": LEAN_PROMPT_CHARS, "source": "disabled", "reason": "", "route_ms": 0}


def _profile_route(name: str, source: str, reason: str = "", ms: int = 0) -> dict[str, Any]:
    p = PROFILES.get(name) or PROFILES["lean"]
    name = name if name in PROFILES else "lean"
    return {"profile": name, "keep_messages": p["keep"], "system": p["systems"][0], "max_out": p["max_out"],
            "prompt_chars": p["prompt_chars"], "source": source, "reason": reason, "route_ms": ms}


def _clamp_route(resp: dict[str, Any], ms: int) -> dict[str, Any]:
    """Valida/clampa la respuesta del cloud a los perfiles fijos (nunca se fía de topes externos)."""
    name = str(resp.get("profile") or "").strip().lower()
    if name not in PROFILES:
        return _profile_route(ROUTE_FALLBACK, "invalid", f"profile={name!r}", ms)
    p = PROFILES[name]
    r = _profile_route(name, "route", str(resp.get("reason") or "")[:120], ms)
    try:
        r["keep_messages"] = max(p["keep_min"], min(p["keep_max"], int(resp.get("keep_messages") or p["keep"])))
    except (TypeError, ValueError):
        pass
    sysm = str(resp.get("system") or "").strip().lower()
    if sysm in p["systems"]:
        r["system"] = sysm
    try:
        r["max_out"] = max(32, min(p["max_out"], int(resp.get("max_out") or p["max_out"])))
    except (TypeError, ValueError):
        pass
    return r


def _route_features(messages: list[dict[str, Any]], has_tools: bool) -> dict[str, Any]:
    other = [m for m in messages if m.get("role", "user") not in ("system", "developer")]
    last_user = next((_content_text(m.get("content")) for m in reversed(other) if m.get("role") == "user"), "")
    last_user = _strip_envelope(last_user) if last_user else ""
    texts = [_content_text(m.get("content")) for m in other]
    has_code = bool(_CODE_RX.search(last_user)) or any("```" in t for t in texts[-4:])
    has_tools = has_tools or any(m.get("role") == "tool" or m.get("tool_calls") for m in other)
    # mask_text de nuevo: defensa en profundidad (body ya viene enmascarado).
    lu, _ = mask_text(last_user[-ROUTE_LAST_USER_CHARS:])
    return {"last_user": lu, "n_messages": len(other), "approx_chars": sum(len(t) for t in texts),
            "has_code": has_code, "has_tools": has_tools, "profiles": list(PROFILES)}


_route_client: httpx.AsyncClient | None = None


async def _decide_route(body: dict[str, Any]) -> dict[str, Any]:
    """Decide perfil de contexto. Nunca lanza; cualquier fallo → ROUTE_FALLBACK."""
    if not ROUTE_ENABLED:
        return _default_route()
    t0 = time.perf_counter()
    feats = _route_features(body.get("messages") or [], bool(body.get("tools")))
    if feats["n_messages"] <= 1 and len(feats["last_user"]) <= ROUTE_FASTPATH_CHARS and not feats["has_code"]:
        return _profile_route("lean", "fastpath", "trivial")
    global _route_client
    if _route_client is None:
        _route_client = httpx.AsyncClient(timeout=httpx.Timeout(ROUTE_TIMEOUT_MS / 1000, connect=0.3))
    try:
        r = await asyncio.wait_for(_route_client.post(ROUTE_URL, headers=_auth(), json=feats), ROUTE_TIMEOUT_MS / 1000)
        ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            return _profile_route(ROUTE_FALLBACK, "http_error", str(r.status_code), ms)
        data = r.json()
        if not isinstance(data, dict):
            return _profile_route(ROUTE_FALLBACK, "invalid", "non-dict", ms)
        return _clamp_route(data, ms)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return _profile_route(ROUTE_FALLBACK, "timeout", "", int((time.perf_counter() - t0) * 1000))
    except Exception as e:  # noqa: BLE001
        return _profile_route(ROUTE_FALLBACK, "error", type(e).__name__, int((time.perf_counter() - t0) * 1000))


def _route_system(route: dict[str, Any], sys0: str) -> str:
    mode = route.get("system", "lean")
    _rs = (str(route.get("reason") or "") + " " + str(route.get("source") or "")).lower()
    if "timeout" in _rs or "unavailable" in _rs:
        mode = "lean"  # timeout fallback: conserva keep/max_out, system lean para recortar prefill
        route["system"] = "lean"
    if mode == "full" and sys0:
        return _head_tail(sys0, ROUTE_FULL_SYSTEM_CHARS)
    if mode == "summary" and sys0:
        # Resumen local barato: LEAN_SYSTEM + cabeza/cola del system original (sin llamar al cloud con el system).
        return (LEAN_SYSTEM + "\n\n" if LEAN_SYSTEM else "") + _head_tail(sys0, ROUTE_SUMMARY_SYSTEM_CHARS)
    return LEAN_SYSTEM if LEAN_SYSTEM else _head_tail(sys0, LEAN_SYSTEM_CHARS)


def _log_route(route: dict[str, Any]) -> None:
    if route.get("source") != "disabled":
        log.info("route profile=%s src=%s keep=%s sys=%s max_out=%s ms=%s reason=%s", route["profile"], route["source"],
                 route["keep_messages"], route["system"], route["max_out"], route["route_ms"], route.get("reason", ""))


def _keep_last_messages(other: list[dict[str, Any]], keep: int) -> list[dict[str, Any]]:
    """Recorte por mensajes enteros: últimos `keep`, garantizando el último mensaje de usuario."""
    if keep <= 0 or len(other) <= keep:
        return list(other)
    kept = other[-keep:]
    if not any(m.get("role") == "user" for m in kept):
        last_user = next((m for m in reversed(other) if m.get("role") == "user"), None)
        if last_user is not None:
            kept = [last_user] + kept
    return kept


async def _ollama_stop(model: str | None) -> None:
    """Abort note only. Do NOT unload: KEEP_ALIVE=-1 keeps qwen warm; unload → ~2min cold load."""
    m = (model or DEFAULT_LOCAL_MODEL).strip()
    log.info("ollama abort noted model=%s (no unload; KEEP_ALIVE=-1)", m)


app = FastAPI(title="openclaw-sanitize-proxy", docs_url=None, redoc_url=None, openapi_url=None)

CLOUD_TIERS = {"cloud": "fast", "cloud-fast": "fast", "cloud-balanced": "balanced", "cloud-strong": "strong"}
ASKBOX_MODELS = ["cloud", "cloud-fast", "cloud-balanced", "cloud-strong", "allin", "auto", "qwen2.5-coder:14b", "deepseek-r1:14b", "llama3.1:8b", "mistral-nemo:12b"]


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "askbox_token": bool(TOKEN), "mode": MODE, "lean_system": len(LEAN_SYSTEM) or LEAN_SYSTEM_CHARS, "lean_prompt": LEAN_PROMPT_CHARS, "lean_keep_messages": LEAN_KEEP_MESSAGES, "lean_max_out": LEAN_MAX_OUT, "route_enabled": ROUTE_ENABLED, "route_timeout_ms": ROUTE_TIMEOUT_MS, "split": SPLIT_ENABLED, "local_model": DEFAULT_LOCAL_MODEL, "chat_cloud_model": CHAT_CLOUD_MODEL}


@app.post("/sanitize")
async def sanitize_preview(request: Request) -> dict[str, Any]:
    data = await request.json()
    c = [0]
    return {"masked": mask_obj(data, c), "redactions": c[0]}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "askbox"} for m in ASKBOX_MODELS]}


def _content_text(c: Any) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
    return "" if c is None else str(c)


def _to_askin(body: dict[str, Any], route: dict[str, Any] | None = None) -> dict[str, Any]:
    route = route or _default_route()
    model = str(body.get("model") or "allin")
    # OpenClaw manda provider/model (askbox/…, ollama/…, ollama-safe/…). Quitar prefijo.
    model = _strip_model_prefix(model)
    out: dict[str, Any] = {
        "max_output_tokens": int(body.get("max_tokens") or body.get("max_completion_tokens") or DEFAULT_MAX_OUT),
    }
    if model in CLOUD_TIERS:
        out["tier"] = CLOUD_TIERS[model]
    elif model in ("allin", "askbox-allin"):
        out["mode"] = MODE
    elif model in ("auto", "ollama"):
        out["provider"] = "ollama"
    else:
        out["provider"] = "ollama"
        out["model"] = model
    lean = "tier" not in out

    system_parts: list[str] = []
    other: list[dict[str, Any]] = []
    for m in body.get("messages", []):
        if m.get("role", "user") in ("system", "developer"):
            system_parts.append(_content_text(m.get("content")))
        else:
            other.append(m)
    n_in = len(other)
    if lean:
        other = _keep_last_messages(other, int(route["keep_messages"]))

    def render(m: dict[str, Any]) -> str:
        role = m.get("role", "user")
        text = _content_text(m.get("content"))
        if role == "tool":
            return f"[tool result]\n{text}"
        if lean and role == "user":
            text = _strip_envelope(text)
        if m.get("tool_calls"):
            text += "\n" + json.dumps(m["tool_calls"], ensure_ascii=False)
        return f"[{role}]\n{text}"

    convo = [render(m) for m in other]
    sys0 = "\n\n".join(system_parts)
    if not lean:
        out["prompt"] = "\n\n".join(convo)[-199000:] or "(vacío)"
        if sys0:
            out["system"] = sys0[-49000:]
        return out

    # Seguridad: si los mensajes enteros exceden el tope, se sueltan los más antiguos;
    # solo si queda uno y sigue grande se recorta su cola.
    pchars = int(route["prompt_chars"])
    while len(convo) > 1 and sum(len(c) for c in convo) > pchars:
        convo = convo[1:]
    prompt = "\n\n".join(convo)
    if len(prompt) > pchars:
        prompt = _tail(prompt, pchars)
    out["prompt"] = prompt or "(vacío)"
    out["system"] = _route_system(route, sys0)
    out["max_output_tokens"] = min(int(out["max_output_tokens"]), int(route["max_out"]))
    approx = (len(out["system"]) + len(out["prompt"])) // 4
    log.info(
        "lean-trim profile=%s model=%s sys=%d→%d msgs=%d→%d prompt→%d approx_tok≈%d max_out=%d",
        route["profile"], model, len(sys0), len(out["system"]), n_in, len(convo), len(out["prompt"]), approx, out["max_output_tokens"],
    )
    if LEAN_DEBUG:
        log.info("lean-debug prompt=%r", out["prompt"][:400])
    return out


def _auth() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _lean_ollama_messages(body: dict[str, Any], route: dict[str, Any] | None = None) -> dict[str, Any]:
    route = route or _default_route()
    msgs = body.get("messages") or []
    system, other = [], []
    for m in msgs:
        if m.get("role") in ("system", "developer"):
            system.append(m)
        else:
            other.append(m)
    sys_text = _route_system(route, "\n\n".join(_content_text(m.get("content")) for m in system))
    other = _keep_last_messages(other, int(route["keep_messages"]))
    pchars = int(route["prompt_chars"])
    other = [
        ({**m, "content": _strip_envelope(_content_text(m.get("content")))} if m.get("role") == "user" else m)
        for m in other
    ]
    while other and sum(len(_content_text(m.get("content"))) for m in other) > pchars:
        if len(other) == 1:
            m = dict(other[0])
            m["content"] = _tail(_content_text(m.get("content")), pchars)
            other = [m]
            break
        other = other[1:]
    new_msgs: list[dict[str, Any]] = []
    if sys_text:
        new_msgs.append({"role": "system", "content": sys_text})
    new_msgs.extend(other)
    out = dict(body)
    out["messages"] = new_msgs
    if "max_tokens" in out:
        try:
            out["max_tokens"] = min(int(out["max_tokens"]), int(route["max_out"]))
        except Exception:
            out["max_tokens"] = int(route["max_out"])
    approx = sum(len(_content_text(m.get("content"))) for m in new_msgs) // 4
    log.info("lean-ollama-passthrough profile=%s msgs=%d approx_tok≈%d", route["profile"], len(new_msgs), approx)
    return out


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    raw = await request.json()
    c = [0]
    body = mask_obj(raw, c)
    route = None
    model_in = str(body.get("model") or "allin")
    model_bare = _strip_model_prefix(model_in)
    split_reason = "explicit_cloud" if model_bare in CLOUD_TIERS else "off"
    if SPLIT_ENABLED and model_bare not in CLOUD_TIERS:
        is_code, why = _is_code_turn(body)
        split_reason = f"code:{why}" if is_code else "chat"
        model_bare = DEFAULT_LOCAL_MODEL if is_code else _strip_model_prefix(CHAT_CLOUD_MODEL)
    body = {**body, "model": model_bare}
    if model_bare not in CLOUD_TIERS or ROUTE_ON_CHAT:
        route = await _decide_route(body)
        _log_route(route)
    _sr, _, _why = split_reason.partition(":")
    log.info("split reason=%s why=%s model_in=%s model_chosen=%s", _sr, _why or "-", model_in, model_bare)
    askin = _to_askin(body, route)
    local_model = None
    if askin.get("provider") == "ollama":
        local_model = str(askin.get("model") or DEFAULT_LOCAL_MODEL)
    elif askin.get("mode"):
        local_model = DEFAULT_LOCAL_MODEL
    log.info(
        "chat->askbox model=%s mode=%s redactions=%d",
        body.get("model"),
        askin.get("mode") or askin.get("provider") or ("tier:" + str(askin.get("tier"))),
        c[0],
    )
    if not TOKEN:
        return JSONResponse({"error": {"message": "ASKBOX_TOKEN no configurado en sanitize-proxy", "type": "config"}}, status_code=503)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT, connect=10.0)) as client:
            try:
                r = await client.post(f"{ASKBOX}/v1/ask", headers=_auth(), json=askin)
            except Exception as e:  # noqa: BLE001
                return JSONResponse({"error": {"message": f"askbox upstream: {type(e).__name__}", "type": "upstream"}}, status_code=502)
    except Exception:
        if local_model:
            await _ollama_stop(local_model)
        raise
    if await request.is_disconnected() and local_model:
        await _ollama_stop(local_model)
        return JSONResponse({"error": {"message": "client aborted", "type": "aborted"}}, status_code=499)
    if r.status_code != 200:
        return JSONResponse({"error": {"message": f"askbox {r.status_code}: {r.text[:300]}", "type": "upstream"}}, status_code=r.status_code)
    a = r.json()
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    model = a.get("model_used") or body.get("model") or "allin"
    usage = {
        "prompt_tokens": a.get("input_tokens", 0),
        "completion_tokens": a.get("output_tokens", 0),
        "total_tokens": a.get("input_tokens", 0) + a.get("output_tokens", 0),
    }
    text = a.get("text", "")
    if body.get("stream"):
        def sse():
            base = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model}
            yield "data: " + json.dumps({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}) + "\n\n"
            yield "data: " + json.dumps({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream")
    return JSONResponse(
        {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": usage,
            "askbox": {"provider": a.get("provider"), "request_id": a.get("request_id")},
        }
    )


@app.api_route("/ollama/v1/{path:path}", methods=["GET", "POST"])
async def ollama_passthrough(path: str, request: Request) -> Response:
    url = f"{OLLAMA}/v1/{path}"
    raw = await request.body()
    c = [0]
    model = DEFAULT_LOCAL_MODEL
    if raw:
        try:
            obj = mask_obj(json.loads(raw), c)
            if isinstance(obj, dict) and ("chat/completions" in path):
                model = str(obj.get("model") or DEFAULT_LOCAL_MODEL)
                route = await _decide_route(obj)
                _log_route(route)
                obj = _lean_ollama_messages(obj, route)
            raw = json.dumps(obj).encode()
        except ValueError:
            t, n = mask_text(raw.decode("utf-8", "replace"))
            raw, c[0] = t.encode(), n
    log.info("ollama %s %s redactions=%d", request.method, path, c[0])
    client = httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT, connect=10.0))
    req = client.build_request(request.method, url, content=raw or None, headers={"Content-Type": "application/json"})
    r = await client.send(req, stream=True)

    async def body_iter():
        try:
            async for chunk in r.aiter_raw():
                if await request.is_disconnected():
                    await _ollama_stop(model)
                    break
                yield chunk
        except Exception:
            await _ollama_stop(model)
            raise
        finally:
            await r.aclose()
            await client.aclose()

    return StreamingResponse(body_iter(), status_code=r.status_code, media_type=r.headers.get("content-type"))
