---
name: llm-routing
description: Añadir o modificar modelos, niveles (tiers), cadenas de fallback, keypool y caché en askbox. Usar al tocar api/app/llm/, model_catalog, rotación de claves o POST /v1/ask.
---

# Routing LLM (askbox)

## Cuándo usar esta skill

- Cambiar niveles `auto|fast|balanced|strong`, prioridades o cadenas de modelos.
- Añadir proveedor o modelo; ajustar cooldown de claves, caché o clasificación de errores.
- Portar patrones desde agentshop-x (`api/app/llm/`) sin copiar listas hardcodeadas a producción.

## Niveles de entrada (`tier`)

| Tier | Uso típico | Cadena (concepto) |
|------|------------|-------------------|
| `fast` | Clasificación, extracción, respuestas cortas | Clase S (luego M) entre **todos** los proveedores con clave |
| `balanced` | Redacción y resumen normales | Clase M (luego S), peers cross-provider |
| `strong` | Razonamiento, código, análisis largo | Clase L (luego M, S), peers cross-provider |
| `auto` | El sistema elige con señales baratas (longitud, `json_schema`, código en prompt, `max_output_tokens`, `default_tier` del proyecto) |

### Modo `auto` — escalada única

- Tras ejecutar en el tier inferido, si la respuesta está vacía o incumple `json_schema` pedido, **escalar una sola vez** al siguiente nivel.
- **Nunca** escalar dos veces en la misma petición (evita quemar cuota en prompts imposibles).

## Ajustar el catálogo (`model_catalog`)

El runtime **solo** lee filas con `enabled = true` (`catalog.peer_chain`, `cost_for_model`). No hay listas de modelos en `router.py`.

| Campo | Efecto |
|-------|--------|
| `enabled` | `false` → el modelo no entra en `peer_chain` (equivale a retirarlo de la cadena sin borrar la fila). |
| `priority` | Menor número = antes en la mezcla cross-provider (misma clase S\|M\|L). |
| `tier` | Clase de potencia S (rápido), M (equilibrado), L (fuerte); el `tier` del `POST /v1/ask` elige qué clases recorrer (`TIER_CLASSES`). |
| `cost_in_per_mtok` / `cost_out_per_mtok` | USD por millón de tokens (usage); no bloquean la llamada, pero deben reflejar la tarifa real del proveedor. |
| `provider` | `gemini`, `openrouter`, `opencode_zen` — solo participa si el proyecto tiene clave utilizable (Zen: ver abajo). |

**Pasos habituales**

1. Nueva migración SQL en `api/app/migrations/` (`NNN_descripcion.sql`); **no** editar migraciones ya aplicadas (`schema_migrations`).
2. `UPDATE` / `INSERT … ON CONFLICT` idempotentes para `enabled`, costes y `priority`.
3. Reiniciar API (`docker compose restart api`) o arranque normal: `run_migrations` aplica pendientes.
4. Comprobar: `SELECT slug, tier, enabled, priority, cost_in_per_mtok FROM model_catalog WHERE provider = '…' ORDER BY tier, priority;`
5. Ejecutar `scripts/assert-no-hardcoded-models.sh` si tocaste código.

## Añadir un modelo

1. `INSERT` en **`model_catalog`**: `provider`, `slug`, `tier` (S|M|L), costes, `max_output_tokens`, `enabled`, `priority`.
2. No añadir slugs `gemini-*` / `*:free` como constantes en `router.py` o providers para runtime de producción.
3. Migración o seed SQL documentada; opcionalmente datos de prueba solo en `tests/`.

## Orden obligatorio de decisión

Por cada `POST /v1/ask`, respetar este orden:

1. **Caché** — clave `sha256(project_id + prompt_rendered + tier + max_tokens + json_schema)` en Redis; TTL por proyecto.
2. **Cooldown de clave** — excluir filas `api_keys` con `cooldown_until > now`; pool por proyecto.
3. **Tier** — respetar el pedido o heurística en `auto`.
4. **Peers (provider-agnostic)** — `catalog.peer_chain`: por cada clase S|M|L del tier pedido, mezclar modelos de `gemini`, `openrouter`, `opencode_zen` (solo proveedores con clave utilizable), orden `priority` y desempate `PROVIDER_ORDER`.
5. **Bajada de potencia** — si se agotan todos los peers del tier pedido, `prev_tier` (`strong`→`balanced`→`fast`) y repetir. No hay lógica «si Gemini → …».
6. **Modo `auto`** — si sigue fallando tras bajadas, **una** escalada al tier superior (`next_tier`).

### Proveedor OpenCode Zen

- `provider` en BD/UI: `opencode_zen`.
- Cliente: `api/app/llm/providers/opencode_zen.py` (OpenAI-compatible).
- Clave de proyecto cifrada o `OPENCODE_ZEN_API_KEY` de sistema.
- **Free en consola OpenCode ≠ usable vía gateway askbox:** los slugs `*-free`, `big-pickle`, etc. suelen rechazarse fuera de la app OpenCode («solo desde OpenCode»). Mantenerlos con `enabled = false` en `model_catalog` (migración `011_opencode_zen_catalog_adjust.sql`) hasta que Zen permita API externa o uses modelos de pago con créditos.
- Modelos de pago: costes según [pricing Zen](https://opencode.ai/docs/zen/); activar con `enabled = true` cuando la cuenta tenga fondos (p. ej. `deepseek-v4-flash`, `glm-5.3-flash` sembrados deshabilitados).
- Catálogo inicial: `010_opencode_zen_provider.sql`; ajustes operativos: `011_…`.

## Keypool y cooldown

- Elegir clave `active` con `cooldown_until` vencido; preferir menor uso reciente.
- En error de cuota / 429 / mensaje reintentable: incrementar `consecutive_failures` y fijar  
  `cooldown_until = now + min(900, max(60, 2^failures * 60))` segundos.
- 401/403 por clave inválida → `status = invalid`, notificar; no reintentar con la misma clave.
- Si todas las claves del proyecto están en cooldown, clave de sistema solo si el proyecto lo permite (patrón snake-os `""` = sistema, pero **sin** exponerla al cliente).

## Errores reintentables

Heredar el estilo de agentshop-x `gemini.py`:

- Códigos HTTP: `403, 404, 429, 500, 502, 503, 504`.
- Needles en el mensaje (case-insensitive), p. ej. `_RETRY_NEEDLES`: `quota`, `rate limit`, `resource exhausted`, `high demand`, `unavailable`, `too many requests`, `try again`, `not found`, `no longer available`.
- No confiar solo en el código HTTP (Google a veces devuelve 403 por cuota).

## Archivos clave (previstos)

| Archivo | Responsabilidad |
|---------|-----------------|
| `api/app/llm/router.py` | Orquestación tier → cadena → respuesta unificada |
| `api/app/llm/catalog.py` | `peer_chain`, `TIER_CLASSES`, orden cross-provider |
| `api/app/llm/providers/` | Gemini, OpenRouter, OpenCode Zen |
| `api/app/llm/keypool.py` | Selección de clave, cooldown, fallos consecutivos |
| `api/app/llm/cache.py` | Lectura/escritura Redis de respuestas |

## Revisar modelos free nuevos (proceso repetible)

### ¿Manual o automático?

| Modo | Cuándo | Qué hace |
|------|--------|----------|
| **Automático (por defecto)** | **Cada día 06:00 UTC** | El worker **arq** ejecuta `catalog_free_models_check`: diff Zen/OpenRouter vs `model_catalog`, sondeos ligeros (límite `ASKBOX_CATALOG_SYNC_PROBE_LIMIT`, default 3), log resumido y archivo `.askbox/catalog-sync-latest.json`. **No** pone `enabled=true` ni existe `--apply`. |
| **Manual on-demand** | Cuando quieras (p. ej. tras un deploy o noticia de modelos) | Mismo check vía CLI/script abajo. |
| **Extra** | Tras `docker compose up --build api worker` | Revisar migraciones y, si hace falta, lanzar el script una vez. |

Se registra **automáticamente** al levantar el servicio `worker` (`docker compose up`); no uses crontab del host. Desactivar: `ASKBOX_CATALOG_SYNC_CRON=false` (o `0`) en `.env` y `docker compose up -d worker`.

### Comando manual

Desde la raíz del repo:

```bash
chmod +x scripts/check-free-models.sh   # una vez
./scripts/check-free-models.sh
./scripts/check-free-models.sh --json
./scripts/check-free-models.sh --no-probe
docker compose exec -T api python -m app.cli catalog-sync-check --probe-limit 3
```

Forzar el mismo job que el cron (sin esperar al run diario):

```bash
docker compose exec -T worker arq app.worker.WorkerSettings --watch  # no — mejor:
docker compose exec -T worker python -c "
import asyncio
from app.db import create_pool, run_migrations
from app.catalog_sync_check import run_sync_check, format_report_text, save_report
from app.config import settings
async def main():
    pool = await create_pool()
    await run_migrations(pool)
    r = await run_sync_check(pool, probe=True, probe_limit=settings.catalog_sync_probe_limit)
    save_report(r)
    print(format_report_text(r))
    await pool.close()
asyncio.run(main())
"
```

(En operación normal basta con `./scripts/check-free-models.sh`.)

- **Zen:** `GET https://opencode.ai/zen/v1/models` — candidatos `*-free` y `big-pickle`. Docs: [Zen](https://opencode.ai/docs/zen/); consola app → Models.
- **OpenRouter:** `GET https://openrouter.ai/api/v1/models` — ids con `:free`.
- **Gemini:** sin listado free fiable aquí; revisar release notes / AI Studio.

### Checklist antes de `enabled=true`

1. Leer `.askbox/catalog-sync-latest.json` o salida del script (probe `ok`, no `app_only`).
2. Candidato **nuevo** → migración SQL (`enabled=false` por defecto).
3. **Sondeo** con el mismo cliente que `POST /v1/ask`.
4. Mensaje **solo app OpenCode** → `enabled=false`.
5. Modelo de pago → costes reales; `enabled=true` solo con créditos.
6. **Nunca** auto-enable en worker/CLI; activación = migración o `UPDATE` humano.

Implementación: `api/app/catalog_sync_check.py`, CLI `catalog-sync-check`, cron en `api/app/worker.py`.

## Antes de terminar

Ejecutar desde la raíz del repo:

```bash
.cursor/skills/llm-routing/scripts/assert-no-hardcoded-models.sh
```

Corregir cualquier slug de modelo fuera de migraciones/tests permitidos.
