"""Comparar modelos free publicados (Zen, OpenRouter) con model_catalog y sondear API externa."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.llm.providers import opencode_zen, openrouter

ZEN_MODELS_URL = "https://opencode.ai/zen/v1/models"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Slugs free en docs Zen que no llevan sufijo -free.
ZEN_FREE_EXTRA = frozenset({"big-pickle"})

# Respuestas que indican política «solo app OpenCode», no fallo transitorio.
APP_ONLY_NEEDLES = (
    "only available in opencode",
    "only available on opencode",
    "only in the opencode",
    "only in opencode",
    "opencode app",
    "use opencode",
    "download opencode",
)

PROBE_PROMPT = [{"role": "user", "content": "Reply with exactly: ok"}]
PROBE_MAX_TOKENS = 8


def is_zen_free_slug(slug: str) -> bool:
    s = slug.strip().lower()
    return s.endswith("-free") or s in ZEN_FREE_EXTRA


def is_openrouter_free_slug(slug: str) -> bool:
    return ":free" in slug


def is_app_only_message(message: str) -> bool:
    low = (message or "").lower()
    return any(n in low for n in APP_ONLY_NEEDLES)


def is_insufficient_funds_message(message: str) -> bool:
    low = (message or "").lower()
    return "insufficient" in low and "fund" in low


@dataclass
class CatalogRow:
    provider: str
    slug: str
    tier: str
    enabled: bool
    priority: int


@dataclass
class SyncReport:
    zen_free_remote: list[str] = field(default_factory=list)
    openrouter_free_remote: list[str] = field(default_factory=list)
    catalog_rows: list[CatalogRow] = field(default_factory=list)
    not_in_catalog: list[dict[str, str]] = field(default_factory=list)
    enabled_in_catalog: list[dict[str, str]] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    gemini_note: str = (
        "Gemini: revisar manualmente release notes / AI Studio; no hay listado free fiable en este comando."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "zen_free_remote": self.zen_free_remote,
            "openrouter_free_remote_count": len(self.openrouter_free_remote),
            "openrouter_free_remote_sample": self.openrouter_free_remote[:20],
            "not_in_catalog": self.not_in_catalog,
            "enabled_in_catalog": self.enabled_in_catalog,
            "probes": self.probes,
            "errors": self.errors,
            "gemini_note": self.gemini_note,
            "catalog_opencode_zen": [
                {"slug": r.slug, "tier": r.tier, "enabled": r.enabled, "priority": r.priority}
                for r in self.catalog_rows
                if r.provider == "opencode_zen"
            ],
            "catalog_openrouter_free": [
                {"slug": r.slug, "tier": r.tier, "enabled": r.enabled}
                for r in self.catalog_rows
                if r.provider == "openrouter" and is_openrouter_free_slug(r.slug)
            ],
        }


async def fetch_zen_model_ids(client: httpx.AsyncClient) -> list[str]:
    resp = await client.get(ZEN_MODELS_URL, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("Zen /models: formato inesperado")
    out: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            out.append(str(item["id"]))
    return sorted(set(out))


async def fetch_openrouter_free_ids(client: httpx.AsyncClient) -> list[str]:
    resp = await client.get(OPENROUTER_MODELS_URL, timeout=45.0)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("OpenRouter /models: formato inesperado")
    out: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "")
        if is_openrouter_free_slug(mid):
            out.append(mid)
    return sorted(set(out))


async def load_catalog_rows(pool) -> list[CatalogRow]:
    rows = await pool.fetch(
        "SELECT provider, slug, tier, enabled, priority FROM model_catalog ORDER BY provider, tier, priority, slug"
    )
    return [
        CatalogRow(
            provider=r["provider"],
            slug=r["slug"],
            tier=r["tier"],
            enabled=bool(r["enabled"]),
            priority=int(r["priority"]),
        )
        for r in rows
    ]


def _slug_set(rows: list[CatalogRow], provider: str) -> set[str]:
    return {r.slug for r in rows if r.provider == provider}


def _enabled_free_rows(rows: list[CatalogRow]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in rows:
        if not r.enabled:
            continue
        if r.provider == "opencode_zen" and is_zen_free_slug(r.slug):
            out.append({"provider": r.provider, "slug": r.slug, "tier": r.tier})
        elif r.provider == "openrouter" and is_openrouter_free_slug(r.slug):
            out.append({"provider": r.provider, "slug": r.slug, "tier": r.tier})
    return out


async def _probe_zen(slug: str, api_key: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"provider": "opencode_zen", "slug": slug}
    try:
        await opencode_zen.complete(
            PROBE_PROMPT,
            api_key=api_key,
            model=slug,
            max_tokens=PROBE_MAX_TOKENS,
            temperature=0,
        )
        entry["probe"] = "ok"
    except Exception as exc:  # noqa: BLE001 — informe operador
        msg = str(exc)
        entry["probe"] = "fail"
        entry["message"] = msg[:280]
        if is_app_only_message(msg):
            entry["policy"] = "app_only"
        elif is_insufficient_funds_message(msg):
            entry["policy"] = "insufficient_funds"
    return entry


async def _probe_openrouter(slug: str, api_key: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"provider": "openrouter", "slug": slug}
    try:
        await openrouter.complete(
            PROBE_PROMPT,
            api_key=api_key,
            model=slug,
            max_tokens=PROBE_MAX_TOKENS,
            temperature=0,
        )
        entry["probe"] = "ok"
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        entry["probe"] = "fail"
        entry["message"] = msg[:280]
    return entry


async def run_sync_check(pool, *, probe: bool = True, probe_limit: int = 5) -> SyncReport:
    report = SyncReport()
    rows = await load_catalog_rows(pool)
    report.catalog_rows = rows
    zen_catalog = _slug_set(rows, "opencode_zen")
    or_catalog = _slug_set(rows, "openrouter")

    async with httpx.AsyncClient() as client:
        try:
            zen_ids = await fetch_zen_model_ids(client)
            report.zen_free_remote = sorted(s for s in zen_ids if is_zen_free_slug(s))
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"zen_models: {type(exc).__name__}: {exc}")

        try:
            report.openrouter_free_remote = await fetch_openrouter_free_ids(client)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"openrouter_models: {type(exc).__name__}: {exc}")

    for slug in report.zen_free_remote:
        if slug not in zen_catalog:
            report.not_in_catalog.append({"provider": "opencode_zen", "slug": slug, "kind": "zen_free"})

    for slug in report.openrouter_free_remote:
        if slug not in or_catalog:
            report.not_in_catalog.append({"provider": "openrouter", "slug": slug, "kind": "openrouter_free"})

    report.enabled_in_catalog = _enabled_free_rows(rows)

    if not probe:
        return report

    zen_key = (settings.system_opencode_zen_key or "").strip()
    or_key = (settings.system_openrouter_key or "").strip()

    zen_probe_slugs: list[str] = []
    for item in report.not_in_catalog:
        if item["provider"] == "opencode_zen":
            zen_probe_slugs.append(item["slug"])
    for item in report.enabled_in_catalog:
        if item["provider"] == "opencode_zen":
            zen_probe_slugs.append(item["slug"])
    zen_probe_slugs = list(dict.fromkeys(zen_probe_slugs))[:probe_limit]

    if zen_key and zen_probe_slugs:
        for slug in zen_probe_slugs:
            report.probes.append(await _probe_zen(slug, zen_key))
    elif zen_probe_slugs:
        report.errors.append("zen_probe_skipped: sin OPENCODE_ZEN_API_KEY")

    or_probe_slugs = [s for s in report.openrouter_free_remote if s not in or_catalog][:probe_limit]
    if or_key and or_probe_slugs:
        for slug in or_probe_slugs:
            report.probes.append(await _probe_openrouter(slug, or_key))
    elif or_probe_slugs:
        report.errors.append("openrouter_probe_skipped: sin OPENROUTER_API_KEY")

    return report


def format_report_text(report: SyncReport) -> str:
    lines: list[str] = []
    lines.append("=== catalog-sync-check ===")
    lines.append(f"Zen free en API ({len(report.zen_free_remote)}): " + ", ".join(report.zen_free_remote[:12]))
    if len(report.zen_free_remote) > 12:
        lines.append(f"  … +{len(report.zen_free_remote) - 12} más")
    lines.append(f"OpenRouter :free en API: {len(report.openrouter_free_remote)} modelos")
    if report.not_in_catalog:
        lines.append("\nCandidatos free NO en model_catalog:")
        for item in report.not_in_catalog[:30]:
            lines.append(f"  - {item['provider']} / {item['slug']}")
        if len(report.not_in_catalog) > 30:
            lines.append(f"  … +{len(report.not_in_catalog) - 30} más")
    else:
        lines.append("\nSin candidatos free nuevos fuera del catálogo (Zen/OR).")

    if report.enabled_in_catalog:
        lines.append("\nFree habilitados en catálogo (revisar política API):")
        for item in report.enabled_in_catalog:
            lines.append(f"  - {item['provider']} / {item['slug']} (tier {item['tier']})")
    else:
        lines.append("\nNingún modelo free habilitado en catálogo (Zen/OR).")

    if report.probes:
        lines.append("\nSondeos (máx. por proveedor):")
        for p in report.probes:
            extra = ""
            if p.get("policy"):
                extra = f" [{p['policy']}]"
            elif p.get("message"):
                extra = f" — {p['message'][:120]}"
            lines.append(f"  {p['provider']} / {p['slug']}: {p.get('probe')}{extra}")

    if report.errors:
        lines.append("\nAvisos:")
        for e in report.errors:
            lines.append(f"  - {e}")

    lines.append(f"\n{report.gemini_note}")
    lines.append(
        "\nChecklist: solo enabled=true tras probe ok; Zen app-only → enabled=false; "
        "migración SQL idempotente; https://opencode.ai/docs/zen/ y consola Models."
    )
    return "\n".join(lines)


def report_dir() -> Path:
    base = (settings.repo_path or "").strip()
    if base:
        return Path(base) / ".askbox"
    return Path("/tmp/askbox-catalog-reports")


def save_report(report: SyncReport) -> Path | None:
    """Persiste informe JSON (sin secretos). No modifica model_catalog."""
    try:
        dest_dir = report_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        payload = report.to_dict()
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        path = dest_dir / "catalog-sync-latest.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
    except OSError:
        return None
