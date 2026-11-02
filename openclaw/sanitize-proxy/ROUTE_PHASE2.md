# Fase 2 — cliente `/v1/route` del sanitize-proxy

Notas de diseño del proxy vendido en este directorio (`app.py`). El servidor `POST /v1/route` lo implementa askbox (`api/app/routers/route.py`). El proxy es el cliente: decide cuánto contexto local conservar y **clampa** la respuesta a perfiles fijos.

Con `ROUTE_ENABLED=0` no hay llamada: el recorte es el de lean v2 (`LEAN_KEEP_MESSAGES`, `LEAN_SYSTEM`, `LEAN_MAX_OUTPUT_TOKENS`, `LEAN_PROMPT_CHARS`).

## Qué se envía

`POST {ROUTE_URL}` con `Authorization: Bearer $ASKBOX_TOKEN`. Cuerpo (sin system y sin historial):

```json
{"last_user": "…", "n_messages": 3, "approx_chars": 100, "has_code": false, "has_tools": false, "profiles": ["lean", "standard", "full"]}
```

`last_user` va sin envoltorio OpenClaw, re-enmascarado y cortado a `ROUTE_LAST_USER_CHARS` (1500).

`has_code` de este JSON es el de `_route_features`: fence en el último user **o** ` ``` ` en alguno de los últimos 4 mensajes. Ese flag alimenta el router de contexto y el fast-path. **No** decide el SPLIT código/chat. El split usa `_is_code_turn`, que solo mira el último user (`feats["last_user"]`).

## Clamp local (valores de `PROFILES` en `app.py`)

La respuesta esperada es `{profile, keep_messages, system, max_out, reason}`. Cualquier tope externo se recorta a:

| Perfil | keep | system | max_out | prompt |
|---|---|---|---|---|
| `lean` | 4 (fijo) | `lean` | ≤ 128 | `LEAN_PROMPT_CHARS` (1800) |
| `standard` | 3–4 | `lean` o `summary` | ≤ 128 | `ROUTE_STANDARD_PROMPT_CHARS` (3200) |
| `full` | 8–12 | `full` o `summary` | ≤ 256 | `ROUTE_FULL_PROMPT_CHARS` (9000) |

- Perfil desconocido, HTTP no 200, JSON malo, timeout (`asyncio.wait_for`) o conexión caída → `ROUTE_FALLBACK` (ejemplo: `standard`).
- `system=full`: cabeza/cola del system de OpenClaw a `ROUTE_FULL_SYSTEM_CHARS` (2400).
- `system=summary`: `LEAN_SYSTEM` + cabeza/cola a `ROUTE_SUMMARY_SYSTEM_CHARS` (700), en local, sin reenviar el system al cloud.
- Fast-path (sin llamada): `n_messages ≤ 1` y `len(last_user) ≤ ROUTE_FASTPATH_CHARS` (200) y no `has_code` → perfil `lean`.
- Solo destinos locales (allin / ollama / modelo concreto). Los tiers `cloud-*` no se enrutan salvo `ROUTE_ON_CHAT=1`.
- Aplica en `POST /v1/chat/completions` y en el passthrough `/ollama/v1/*`.
- Log: `route profile=… src=route|fastpath|timeout|error|invalid|http_error keep=… sys=… max_out=… ms=… reason=…`.

## Activar

1. askbox en marcha, con `POST /v1/route` respondiendo 200.
2. En `openclaw/.env`: `ROUTE_ENABLED=1`. `ROUTE_FALLBACK=standard` evita bajar de `keep=4` si la nube no llega a tiempo.
3. `docker compose -f openclaw/docker-compose.yml up -d --build --no-deps sanitize-proxy`.
4. Revisar logs `route `.

Rollback: `ROUTE_ENABLED=0` y volver a levantar el servicio. El comportamiento vuelve a lean v2.
