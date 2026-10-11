# askbox

Gateway LLM mono-usuario: `POST /v1/ask` con peers por tier (Gemini, OpenRouter, OpenCode Zen), claves cifradas (AES-256-GCM) y traza en PostgreSQL.

Licencia [MIT](LICENSE).

- **Fase 0**: andamiaje (API FastAPI, web Next.js, PostgreSQL, Redis, worker arq).
- **Fase 1 (MVP)**: auth, proyectos, claves cifradas, `/v1/ask`, UI mínima.
- **Fase 2**: tokens de proyecto (`ab_live_…`), plantillas de prompt versionadas, rate limit por token/proyecto, UI en `/projects`.
- **Fase 3**: routing `tier=auto`, uso diario, keypool `exhausted`.
- **Fase 4**: batch (`POST /v1/batch`, `GET /v1/jobs/{id}`), stream SSE (`POST /v1/ask/stream`), alertas de claves, worker arq real.

## Arranque

```bash
cp .env.example .env            # rellenar POSTGRES_PASSWORD, DATABASE_URL y ASKBOX_MASTER_KEY
openssl rand -hex 32            # -> ASKBOX_MASTER_KEY (guárdala: sin ella las claves guardadas no se descifran)
docker compose up -d --build
curl -s http://localhost:8066/health   # {"ok":true}
curl -s http://localhost:8066/ready    # db, redis y master_key
```

- API: http://localhost:8066 (migraciones SQL automáticas al arrancar, `api/app/migrations/`)
- Web: http://localhost:8067 (`/login`, `/register`, `/forgot`, `/reset`, `/projects`, `/playground`, `/settings/deploy`, `/settings/git`; proxy `/api/*` → API)
- **OpenAPI (Swagger / ReDoc)** detrás de la web (mismo origen que la UI): [http://localhost:8067/api/docs](http://localhost:8067/api/docs), [http://localhost:8067/api/redoc](http://localhost:8067/api/redoc), esquema en `/api/openapi.json`. Con túnel: **https://askbox.softportweb.com/api/docs** (y `/api/redoc`, `/api/openapi.json`). Acceso directo al contenedor API en `:8066` sigue en `/docs` si `ASKBOX_API_EXTERNAL_PREFIX` está vacío.

### Clonar y usar sin túnel (por defecto)

Tras `cp .env.example .env` y `docker compose up -d --build`, la API y la web escuchan en **localhost** (8066/8067) o en la IP de tu LAN. No hace falta Cloudflare ni `CLOUDFLARE_TUNNEL_TOKEN`.

### Cloudflare Tunnel (opcional)

El servicio `cloudflared` en `docker-compose.yml` lleva el **profile** `tunnel`: un `docker compose up -d` normal **no** lo levanta. Así cualquiera puede clonar el repo sin token ni túnel.

**Flujo recomendado (UI):** en **`/settings/deploy`**

1. Crea un [API token](https://dash.cloudflare.com/profile/api-tokens) con **Account → Cloudflare Tunnel → Edit** y **Zone → DNS → Edit** en la zona de tu dominio.
2. Pega el token en «API token Cloudflare» (se cifra en BD; la API solo muestra `last4`).
3. Indica el hostname público (p. ej. `askbox.tudominio.com`) y pulsa **Crear / actualizar túnel**.
4. La API crea o reutiliza un túnel llamado como el proyecto (`askbox`), configura ingress `hostname → http://web:3000`, CNAME proxied a `{tunnel_id}.cfargotunnel.com`, guarda el connector token cifrado, actualiza `public_url` / cookies en `.askbox/deploy-settings.json` y, si el host lo permite, escribe `.env` y ejecuta `docker compose --profile tunnel up -d cloudflared`.

En Docker, el contenedor `api` no lee `.env` del repo (está enmascarado). Para aplicación automática en el host, monta en `docker-compose.yml` (ver comentarios en el servicio `api`) `./.env` en `/host-env/.env` con `ASKBOX_HOST_ENV_FILE=/host-env/.env`, y opcionalmente `docker.sock`, **o** define `ASKBOX_TUNNEL_APPLY_SCRIPT=/repo/scripts/apply-tunnel.sh` y deja el script ejecutable en el host.

**Manual (panel Zero Trust):** crea el tunnel, copia el connector token a `CLOUDFLARE_TUNNEL_TOKEN` en `.env`, `ASKBOX_PUBLIC_URL=https://…`, `ASKBOX_COOKIE_SECURE=true`, ingress → `http://web:3000`, y `docker compose --profile tunnel up -d`.

**Este servidor (softportweb):** mantener el túnel con `docker compose --profile tunnel up -d` al actualizar el stack; el provision desde la UI es idempotente y reutiliza el túnel `askbox` existente.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/v1/auth/register` | `{email, password, password_confirm}` (422 `password_mismatch` si no coinciden). Solo el primer usuario (o `ASKBOX_ALLOW_REGISTER=true`); fija cookie de sesión |
| POST | `/v1/auth/forgot` | `{email}` → respuesta genérica (no revela si existe). Crea token en `password_reset_tokens` (hash, caduca en `ASKBOX_RESET_TTL_MINUTES`) |
| POST | `/v1/auth/reset` | `{token, password, password_confirm}`; token de un solo uso; cierra todas las sesiones del usuario |
| POST | `/v1/auth/login` / `/logout` | Cookie `askbox_session` (httpOnly, SameSite=Lax) |
| GET | `/v1/auth/me` | Usuario actual |
| POST | `/v1/auth/token` | Bearer largo para scripts (se muestra una vez) |
| GET/POST | `/v1/projects` | Listar / crear proyecto |
| GET/POST | `/v1/projects/{id}/keys` | Listar (solo `last4`, `status`, `label`, `provider`) / alta de clave (se cifra) |
| DELETE | `/v1/projects/{id}/keys/{key_id}` | Borrar clave |
| GET/POST | `/v1/projects/{id}/access-tokens` | Listar metadatos (`prefix`, `scopes`, `last4` vía prefix) / crear token (`ab_live_…` solo en la respuesta POST) |
| DELETE | `/v1/projects/{id}/access-tokens/{token_id}` | Revocar token de proyecto |
| GET/POST | `/v1/projects/{id}/prompts` | Listar última versión por nombre / crear nueva versión (`name`, `system_prompt`, `user_template`, `default_tier`) |
| GET | `/v1/projects/{id}/prompts/{name}` | Última versión (`?version=N` opcional) |
| GET | `/v1/projects/{id}/prompts/{name}/versions` | Historial de versiones |
| DELETE | `/v1/projects/{id}/prompts/{name}` | Borrar todas las versiones |
| POST | `/v1/ask` | Sesión o `Authorization: Bearer ab_live_…` (scope `ask:read`). Cuerpo: `prompt` **o** `prompt_template` + `variables`, `system?`, `project_id?` (ignorado con token de proyecto), `tier`, `max_output_tokens` |
| POST | `/v1/ask/stream` | Igual auth/cuerpo que `/v1/ask`; respuesta **SSE** (`text/event-stream`): eventos `delta` con `text` y `done` con metadatos (`request_id`, tokens, etc.) |
| POST | `/v1/batch` | Solo sesión. `{project_id?, tier, max_output_tokens, items:[{prompt, system?}]}` → `{job_id, status, total}`; encola worker arq |
| GET | `/v1/jobs/{job_id}` | Estado del batch e ítems (`text` / `error_code` por prompt) |
| GET | `/v1/alerts` | Claves de proyecto en `invalid` o `exhausted` (sesión) |
| GET | `/v1/projects/{id}/usage` | Agregado diario (`?days=30`; token con scope `usage:read`) |
| GET | `/v1/requests` | Trazas recientes (sin prompts) |
| GET | `/v1/settings/git/status` | Estado del repo del proyecto: branch, remotos (URL sin credenciales), último commit, preferencias |
| PATCH | `/v1/settings/git/preferences` | `{auto_commit?, remote_name?, default_visibility?}` — guardadas en `.askbox/git-settings.json` (no secreto, versionable) |
| POST | `/v1/settings/git/remote` | Conectar remoto existente (`{name, url}`) o crear uno (`{name, create:true, slug?, visibility?, description?, push?}`): con `gh` si está autenticado en el host; si no, con el GitHub token guardado (REST; `slug` obligatorio) |
| GET | `/v1/settings/git/github-token?repo=` | `{github_token_configured, github_token_last4?, github_token_repo}` **del repo indicado**; **nunca** el token |
| PUT/POST | `/v1/settings/git/github-token?repo=` | `{token}` → se cifra (AES-256-GCM, `ASKBOX_MASTER_KEY`) en `git_credentials` para ese repo; reemplaza el anterior |
| DELETE | `/v1/settings/git/github-token?repo=` | Borra el token de ese repo |
| POST | `/v1/settings/git/push` | `{remote?, branch?}` → `git push -u` de la rama actual (usa el token si el remoto es `https://github.com/...`) |
| GET | `/v1/settings/deploy/status` | URL efectiva, preferencias, tunnel id/hostname, `cloudflared_running`, metadatos tokens (`last4` solo) |
| PATCH | `/v1/settings/deploy/preferences` | `{public_url?, tunnel_enabled?}` — no secretos; sugiere valores para `.env` |
| PUT/POST | `/v1/settings/deploy/cloudflare-api-token` | `{token}` API Cloudflare → cifrado (`cloudflare_api`); **nunca** devuelve el token |
| DELETE | `/v1/settings/deploy/cloudflare-api-token` | Borra el token de API de la BD |
| POST | `/v1/settings/deploy/tunnel/provision` | `{hostname, api_token?}` crea/actualiza túnel + DNS; guarda connector; opcionalmente `.env` + docker |
| PUT/POST | `/v1/settings/deploy/tunnel-token` | `{token}` connector → cifrado en BD (`cloudflare_tunnel`); **nunca** devuelve el token |
| DELETE | `/v1/settings/deploy/tunnel-token` | Borra el token de túnel de la BD |

`status`, `remote`, `push` y `github-token` aceptan `?repo=<nombre>`: `askbox` (por defecto) o un repo montado en `ASKBOX_EXTRA_REPOS` (por defecto `cursor-agent-orchestrator`).

**Pantalla `/settings/git` en Docker:** `docker-compose.yml` monta el repo en el contenedor `api` como `/repo` (`ASKBOX_REPO_PATH=/repo`): árbol en solo lectura, `.git` y `.askbox/` en lectura/escritura, y `.env` enmascarado con `/dev/null` (los secretos no son legibles desde el contenedor). La API corre con `ASKBOX_UID`/`ASKBOX_GID` (por defecto 1000:1000, el dueño del repo) para no dejar ficheros root en `.git`. El `worker` no usa gitops y no monta nada. Sin `gh auth login` en el host/contenedor no se puede crear remoto con `gh`; conectar uno existente (`POST /v1/settings/git/remote`) sí funciona.

Errores: `{"error": {"code", "message"}}`. `401` sin auth, `403` registro cerrado, `413/422` tamaños, `429` rate limit, `502` fallo de proveedores (`no_provider_key` o `provider_failed`; siempre con `request_id`).

## Cómo probar

```bash
A=http://localhost:8066; J='-H Content-Type:application/json'
curl -c cj -s $J -d '{"email":"tu@email","password":"una-clave-larga","password_confirm":"una-clave-larga"}' $A/v1/auth/register
PID=$(curl -b cj -s $J -d '{"name":"demo"}' $A/v1/projects | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
# Pegar tu clave real de Gemini u OpenRouter (se cifra; la API solo devolverá last4):
curl -b cj -s $J -d '{"provider":"gemini","key":"<TU_CLAVE>"}' $A/v1/projects/$PID/keys
curl -b cj -s $J -d "{\"project_id\":\"$PID\",\"prompt\":\"Di hola\",\"tier\":\"fast\"}" $A/v1/ask
curl -b cj -s $A/v1/requests

# Fase 4: stream SSE y batch
curl -b cj -N -s $J -d "{\"project_id\":\"$PID\",\"prompt\":\"Cuenta hasta 3\",\"tier\":\"fast\"}" $A/v1/ask/stream
JOB=$(curl -b cj -s $J -d "{\"project_id\":\"$PID\",\"tier\":\"fast\",\"items\":[{\"prompt\":\"uno\"},{\"prompt\":\"dos\"}]}" $A/v1/batch | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')
sleep 2
curl -b cj -s $A/v1/jobs/$JOB
curl -b cj -s $A/v1/alerts
curl -b cj -s $A/v1/projects/$PID/usage?days=7

# Fase 2: token de proyecto y plantilla
TOK=$(curl -b cj -s $J -d '{"label":"script","scopes":["ask:read"]}' $A/v1/projects/$PID/access-tokens | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -b cj -s $J -d '{"name":"saludo","user_template":"Hola {{quien}}","default_tier":"fast"}' $A/v1/projects/$PID/prompts
curl -s $J -H "Authorization: Bearer $TOK" -d '{"prompt_template":"saludo","variables":{"quien":"mundo"}}' $A/v1/ask
```

Sin clave de proveedor, `/v1/ask` responde `502 no_provider_key` y deja la traza en `requests`. Con claves de sistema (`GEMINI_API_KEY` / `OPENROUTER_API_KEY` en `.env`) y proyecto con `allow_system_keys=true`, se usan como respaldo.

Tests sin red: `docker run --rm -v "$PWD/api":/srv -w /srv -e PYTHONPATH=/srv askbox-api python -m unittest discover -s tests`

## Email con Brevo (recuperación de contraseña)

Checklist (5 min):

1. Crear cuenta en [Brevo](https://www.brevo.com/) y verificar un **remitente** (Senders & IP → añadir email o dominio).
2. En el panel: **SMTP & API** → generar o copiar la **clave SMTP** (`xsmtpsib-…`; no uses la contraseña de login).
3. En `.env` del servidor (copiado de `.env.example`), rellena `ASKBOX_SMTP_USER` (email con el que entras en Brevo), `ASKBOX_SMTP_PASSWORD` (clave SMTP), `ASKBOX_SMTP_FROM` (remitente verificado) y `ASKBOX_PUBLIC_URL` (URL pública de la web, para los enlaces).
4. Opcional: `ASKBOX_LOG_RESET_LINK=false` cuando el SMTP funcione (deja de volcar enlaces en logs).
5. Aplicar: `docker compose up -d api` y prueba en `/forgot` con un email de usuario registrado.

Variables que lee la API (`api/app/config.py` → `api/app/mailer.py`): `ASKBOX_SMTP_HOST`, `ASKBOX_SMTP_PORT`, `ASKBOX_SMTP_USER`, `ASKBOX_SMTP_PASSWORD`, `ASKBOX_SMTP_FROM`, `ASKBOX_SMTP_TLS` (Brevo: `587` + `starttls`).

## Recuperar contraseña / cuenta bloqueada

- Web: `/forgot` → email → enlace `/reset?token=…` (caduca en 30 min, un solo uso). Con SMTP (`ASKBOX_SMTP_*`) se envía por email; **sin SMTP** el enlace se escribe en los logs de la API: `docker compose logs api | grep RESET_LINK`.
- Solo desarrollo local: `ASKBOX_DEV_SHOW_RESET_TOKEN=true` hace que `/forgot` devuelva `reset_url` (nunca en producción).
- Operador (registro cerrado o usuario inexistente), enlace de 24 h guardado en un archivo privado:
  ```bash
  umask 077; docker compose exec -T api python -m app.cli ensure-user tu@email > ~/askbox-reset.txt   # crea si no existe
  docker compose exec -T api python -m app.cli reset-link tu@email > ~/askbox-reset.txt              # usuario existente
  ```
- El registro está cerrado en cuanto existe un usuario (incluidos datos de prueba como `admin@askbox.local`); `register` devuelve `403 registration_closed`.

## Diseño (resumen)

- `api/app/llm/`: `router.py` (caché → tier → peers same-class en todos los proveedores con clave → bajada de tier), `providers/` (gemini, openrouter, `opencode_zen`), `keypool.py`, `cache.py`, `catalog.py` (`peer_chain`).
- OpenCode Zen: clave de proyecto o `OPENCODE_ZEN_API_KEY` en servidor; endpoint `ASKBOX_OPENCODE_ZEN_API_URL` (por defecto chat completions Zen). Los modelos **free** de la consola OpenCode **no** sirven para `POST /v1/ask` (política app-only); el catálogo los lleva `enabled=false` — usar modelos de pago con créditos y `enabled=true` en `model_catalog` (ver `.cursor/skills/llm-routing/SKILL.md`).
- Modelos y cadenas en la tabla `model_catalog` (seeds/migraciones en `api/app/migrations/`); ajustar `enabled`, `priority`, `tier` y costes en BD (o nueva migración), no en código.
- **Revisión de modelos free:** automática **diaria 06:00 UTC** en el servicio **`worker`** (arq). Al hacer `docker compose up -d` (o `up` con worker), el cron `catalog_free_models_daily` se registra **solo** — no hace falta crontab del host ni pasos post-deploy. Desactivar: `ASKBOX_CATALOG_SYNC_CRON=false` o `0` en `.env`. Manual on-demand: `./scripts/check-free-models.sh`. Solo descubre candidatos y escribe `.askbox/catalog-sync-latest.json`; **no** activa modelos en BD.
- Revisión manual on-demand: `./scripts/check-free-models.sh` — checklist en `.cursor/skills/llm-routing/SKILL.md`.
- Seguridad: AES-256-GCM con AAD `proyecto:proveedor`, nonce aleatorio, `key_version`; la API nunca devuelve claves; logs sin prompts (solo longitud y hash truncado).
- Límites: prompt ≤ 20000 chars, `max_output_tokens` ≤ 8192, rate limit Redis (`ASKBOX_ASK_RATE_PER_MIN`, 30/min por usuario, token `ab_live_` y proyecto en `/v1/ask`; login 10/min por IP).
- Tokens de proyecto: prefijo `ab_live_`, SHA-256 en `project_access_tokens`; scopes `ask:read`, `prompts:write`, `keys:write`, `usage:read` (gestión UI con sesión; `/v1/ask` con Bearer de token).
- Tras HTTPS, poner `ASKBOX_COOKIE_SECURE=true` en `.env` (o guardar `public_url` https en `/settings/deploy`; la API usa cookies `Secure` según la URL efectiva aunque el contenedor no se haya reiniciado).
- **Fase 3 (routing):** `tier=auto` infiere `fast|balanced|strong` (longitud, código, `max_output_tokens`, `json_schema`, `default_tier` del proyecto) y **escala una sola vez** al siguiente nivel si la cascada falla. Caché Redis con clave que incluye `json_schema`. Keypool con cooldown `min(900, max(60, 2^n·60))` y `exhausted`. `usage_daily` + `GET /v1/projects/{id}/usage`. Respuesta de `/v1/ask` incluye `cache_hit`, tokens y `models_chain` opcional.
- **Fase 4 (batch/stream):** `batch_jobs` + `batch_job_items` en PostgreSQL; worker `arq` (`process_batch_job`). Stream reutiliza `app/llm/router.ask` y emite trozos SSE antes del evento `done`. `GET /v1/alerts` y badge en nav `/usage` (gráficas + CSV). Límite `ASKBOX_BATCH_MAX_ITEMS` (32 por defecto).

## Configuración Git del proyecto (`/settings/git`)

Esta pantalla es **git del producto askbox y repos montados en este despliegue** (capa B/C del
kit), no un panel universal para cualquier app. El gateway LLM (`ASKBOX_ACCESS_TOKEN` en agentes)
es independiente (capa A). Las tres capas están en `.cursor/docs/git-and-askbox.md`. Tras clonar
el kit portable, usa `./configure` en `cursor-agent-orchestrator` (guía en `setup/`), no esta URL.

MVP de pantalla de administración para el propio repo de askbox (ver `api/app/gitops.py`,
`api/app/routers/git_settings.py`, `web/app/settings/git/page.js`):

- Lee `git branch`, `git remote -v` (URL siempre saneada, sin credenciales embebidas), último
  commit y si hay cambios sin commitear.
- Permite conectar un remoto existente o crear uno nuevo (público/privado según
  `default_visibility`): con `gh` si está autenticado, o con el **GitHub token** guardado.
- **GitHub token (PAT), uno por repositorio:** se pega en el campo password de `/settings/git` (el
  selector de repo decide a cuál pertenece; la UI muestra «Token de este repositorio»), se cifra con
  AES-256-GCM (`ASKBOX_MASTER_KEY`, AAD ligado a usuario+repo) en la tabla `git_credentials`
  (PK `user_id, provider, repo`; migración `004`, que asocia el token global antiguo a `askbox`) y no vuelve a salir
  de la API (solo `github_token_configured` y `last4`). Para crear repo + push necesita permiso
  de crear repos (clásico: `repo`; fine-grained: *Administration* y *Contents* en write). Al
  usarlo, solo vive en memoria: la API lo pasa a `git` por variables de entorno del subproceso
  (cabecera acotada a `https://github.com/`, sin tocar `.git/config` ni argv) y los errores se
  redactan. El remoto se guarda con URL limpia, sin credenciales. Si se pierde la master key,
  hay que volver a pegar el token.
- Repos adicionales: `docker-compose.yml` monta el kit hermano `../cursor-agent-orchestrator`
  (`ASKBOX_KIT_PATH` para otra ruta) y lo declara en `ASKBOX_EXTRA_REPOS`; la pantalla muestra un
  selector de repositorio.
- Preferencias (`auto_commit`, `remote_name`, `default_visibility`) se guardan en
  `.askbox/git-settings.json` dentro del propio repo (no secreto, versionable).
- `ASKBOX_REPO_PATH` (opcional): ruta del repo accesible desde el proceso de la API. En Docker
  es `/repo` (montado por `docker-compose.yml`, ver abajo); fuera de Docker se calcula la raíz
  del repo. Si no hay `.git` accesible, la pantalla responde `available: false` con el motivo.
- Política de auto-commit/push del propio código de askbox: ver
  `.cursor/skills/git-autopilot/SKILL.md`.
