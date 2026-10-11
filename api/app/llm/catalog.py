"""Lectura de model_catalog (PostgreSQL) para construir cadenas por tier."""
from __future__ import annotations

# Clases de tier del catálogo a recorrer según el tier pedido (orden de preferencia).
TIER_CLASSES = {
    "fast": ["S", "M"],
    "balanced": ["M", "S"],
    "strong": ["L", "M", "S"],
}


async def chain(pool, tier: str, provider: str, limit: int) -> list[dict]:
    classes = TIER_CLASSES[tier]
    rows = await pool.fetch(
        "SELECT slug, tier, max_output_tokens FROM model_catalog "
        "WHERE enabled AND provider = $1 AND tier = ANY($2::text[]) ORDER BY priority, slug",
        provider,
        classes,
    )
    ordered = sorted(rows, key=lambda r: classes.index(r["tier"]))  # estable: respeta priority dentro de cada clase
    seen: set[str] = set()
    out: list[dict] = []
    for r in ordered:
        if r["slug"] in seen:
            continue
        seen.add(r["slug"])
        out.append({"slug": r["slug"], "max_output_tokens": r["max_output_tokens"]})
        if len(out) >= limit:
            break
    return out
