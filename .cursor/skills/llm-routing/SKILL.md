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
| `provider` | `gemini`, `openrouter` — solo participa si el proyecto tiene clave utilizable. |

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

## Ruta explícita a Ollama (fuera de la cascada, 100 % local)

`provider=ollama` o `model=ollama/<slug>` en `POST /v1/ask` y `/v1/ask/stream` llama a Ollama (`OLLAMA_BASE_URL`, default `http://ollama:11434`). No usa keypool, `model_catalog`, Gemini, OpenRouter ni búsqueda web, y un fallo de Ollama **no** cae a la cascada. Sin modelo (o con `auto`), elige en local: código → `OLLAMA_MODEL` / `ALLIN_CODE_MODEL`; razonamiento duro → `OLLAMA_REASON_MODEL`; texto/NLP → `OLLAMA_NLP_MODEL` (default `llama3.1:8b`; no existe `llama3.3:8b`); contexto largo → `OLLAMA_LONGCTX_MODEL` (`mistral-nemo:12b`); desarrollo complejo (plan y código) → razón y luego código, todo local (`models_chain`). Cada slug elegido pasa por `local_models.resolve_local_model`: si el proyecto lo tiene inactivo, se usa el local principal activo (default el de código, `qwen2.5-coder:14b`); si no hay ninguno activo, el auto local sigue por la cascada de nube. Un slug concreto es una sola llamada a ese modelo, aunque esté inactivo. Los adjuntos siguen en `422 ollama_no_attachments`. No añadir Ollama a `PROVIDER_ORDER` ni al `CHECK` de `model_catalog`. Preferencias en `projects.ollama_models` (`GET`/`PUT /v1/projects/{id}/ollama-models`); `OLLAMA_DEFAULT_ACTIVE_MODELS` vacío = solo el slot de código.

## `mode=allin` (híbrido; alias `provider=allin`)

No es la ruta Ollama. Puede extraer adjuntos, pedir un resumen web (Gemini con grounding u OpenRouter con plugin web, claves del keypool) y llamar a los mismos modelos locales: etapa 3 DeepSeek o Mistral Nemo, etapa 4 Qwen, etapa 5 Llama 3.1 si hubo trabajo intermedio (no en el atajo de una sola llamada). Esas llamadas pasan por el mismo filtro de modelos activos del proyecto (`resolve_local_model`): inactivo → principal activo; ninguno activo → esa etapa no llama a Ollama y cae a la cascada. Si Ollama falla, esa etapa también cae a la cascada de abajo. Sin `provider=ollama` ni `mode`/`provider=allin`, el orden de la cascada no cambia.

## Orden obligatorio de decisión

Por cada `POST /v1/ask` **sin** ruta explícita a Ollama y **sin** `mode=allin` / `provider=allin`, respetar este orden:

1. **Caché** — clave `sha256(project_id + prompt_rendered + tier + max_tokens + json_schema)` en Redis; TTL por proyecto.
2. **Cooldown de clave** — excluir filas `api_keys` con `cooldown_until > now`; pool por proyecto.
3. **Tier** — respetar el pedido o heurística en `auto`.
4. **Peers (provider-agnostic)** — `catalog.peer_chain`: por cada clase S|M|L del tier pedido, mezclar modelos de `gemini` y `openrouter` (solo proveedores con clave utilizable), orden `priority` y desempate `PROVIDER_ORDER`.
5. **Bajada de potencia** — si se agotan todos los peers del tier pedido, `prev_tier` (`strong`→`balanced`→`fast`) y repetir. No hay lógica «si Gemini → …».
6. **Modo `auto`** — si sigue fallando tras bajadas, **una** escalada al tier superior (`next_tier`).

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
| `api/app/llm/providers/` | Gemini, OpenRouter; Ollama solo si el cliente lo pide (`ollama.py`) |
| `api/app/llm/ollama_route.py` | Parseo de `provider` / `model` explícitos (`auto` no es un slug de Ollama) |
| `api/app/llm/ollama_local.py` | Ejecución local de `provider=ollama` (un fallo no cae a la nube) |
| `api/app/llm/local_models.py` | Slots activos por proyecto y sustitución al principal |
| `api/app/llm/allin.py` | Orquestador híbrido `mode=allin` / `provider=allin` |
| `api/app/llm/heuristics.py` | Web, código, razonamiento, NLP y contexto largo (tests sin red) |
| `api/app/llm/keypool.py` | Selección de clave, cooldown, fallos consecutivos |
| `api/app/llm/cache.py` | Lectura/escritura Redis de respuestas |

## Antes de terminar

Ejecutar desde la raíz del repo:

```bash
.cursor/skills/llm-routing/scripts/assert-no-hardcoded-models.sh
```

Corregir cualquier slug de modelo fuera de migraciones/tests permitidos.
