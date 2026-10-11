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


async def cost_for_model(pool, provider: str, slug: str) -> tuple[float, float]:
    """Coste USD por millón de tokens (entrada, salida); 0 si no hay fila."""
    row = await pool.fetchrow(
        "SELECT cost_in_per_mtok, cost_out_per_mtok FROM model_catalog "
        "WHERE enabled AND provider = $1 AND slug = $2 ORDER BY priority LIMIT 1",
        provider,
        slug,
    )
    if not row:
        return 0.0, 0.0
    cin = float(row["cost_in_per_mtok"] or 0)
    cout = float(row["cost_out_per_mtok"] or 0)
    return cin, cout


def estimate_cost_usd(input_tokens: int, output_tokens: int, cin: float, cout: float) -> float:
    return (input_tokens * cin + output_tokens * cout) / 1_000_000.0
