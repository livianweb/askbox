"""Lectura de model_catalog (PostgreSQL) para construir cadenas por tier."""
from __future__ import annotations

# Clases de potencia del catálogo (S|M|L) a recorrer según el tier pedido (orden de preferencia).
TIER_CLASSES = {
    "fast": ["S", "M"],
    "balanced": ["M", "S"],
    "strong": ["L", "M", "S"],
}


async def _models_for_class(pool, provider: str, power_class: str, limit: int) -> list[dict]:
    rows = await pool.fetch(
        "SELECT slug, tier, max_output_tokens, priority FROM model_catalog "
        "WHERE enabled AND provider = $1 AND tier = $2 ORDER BY priority, slug LIMIT $3",
        provider,
        power_class,
        limit,
    )
    return [
        {"slug": r["slug"], "max_output_tokens": r["max_output_tokens"], "priority": int(r["priority"])}
        for r in rows
    ]


def order_peer_batch(provider_order: tuple[str, ...], batch: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """Misma clase de potencia: prioridad global, desempate por orden de proveedor."""
    idx = {p: i for i, p in enumerate(provider_order)}
    ordered = sorted(batch, key=lambda item: (item[1]["priority"], idx.get(item[0], 99), item[1]["slug"]))
    return [(p, {k: v for k, v in m.items() if k != "priority"}) for p, m in ordered]


async def peer_chain(pool, tier: str, providers: tuple[str, ...], limit_per_provider: int) -> list[tuple[str, dict]]:
    """Peers por clase de potencia (horizontal entre proveedores) antes de bajar de clase."""
    out: list[tuple[str, dict]] = []
    for power_class in TIER_CLASSES[tier]:
        batch: list[tuple[str, dict]] = []
        for provider in providers:
            for m in await _models_for_class(pool, provider, power_class, limit_per_provider):
                batch.append((provider, m))
        out.extend(order_peer_batch(providers, batch))
    return out


async def chain(pool, tier: str, provider: str, limit: int) -> list[dict]:
    """Cadena solo dentro de un proveedor (compat / tests)."""
    classes = TIER_CLASSES[tier]
    rows = await pool.fetch(
        "SELECT slug, tier, max_output_tokens FROM model_catalog "
        "WHERE enabled AND provider = $1 AND tier = ANY($2::text[]) ORDER BY priority, slug",
        provider,
        classes,
    )
    ordered = sorted(rows, key=lambda r: classes.index(r["tier"]))
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
