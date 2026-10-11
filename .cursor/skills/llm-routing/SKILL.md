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
- Clave de proyecto cifrada o `OPENCODE_ZEN_API_KEY` de sistema; modelos **free** permiten peer anónimo sin clave de proyecto.
- Catálogo: migración `010_opencode_zen_provider.sql`; slugs según [docs Zen](https://opencode.ai/docs/zen/).

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

## Antes de terminar

Ejecutar desde la raíz del repo:

```bash
.cursor/skills/llm-routing/scripts/assert-no-hardcoded-models.sh
```

Corregir cualquier slug de modelo fuera de migraciones/tests permitidos.
