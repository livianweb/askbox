# askbox

Gateway LLM mono-usuario: `POST /v1/ask` con cascada Gemini → OpenRouter, claves cifradas (AES-256-GCM) y traza en PostgreSQL.

- **Fase 0**: andamiaje (API FastAPI, web Next.js, PostgreSQL, Redis, worker arq).
- **Fase 1 (MVP)**: auth, proyectos, claves cifradas, `/v1/ask`, UI mínima.

## Arranque

```bash
cp .env.example .env            # rellenar POSTGRES_PASSWORD, DATABASE_URL y ASKBOX_MASTER_KEY
openssl rand -hex 32            # -> ASKBOX_MASTER_KEY (guárdala: sin ella las claves guardadas no se descifran)
docker compose up -d --build
curl -s http://localhost:8066/health   # {"ok":true}
curl -s http://localhost:8066/ready    # db, redis y master_key
```

- API: http://localhost:8066 (migraciones SQL automáticas al arrancar, `api/app/migrations/`)
- Web: http://localhost:8067 (`/login`, `/register`, `/forgot`, `/reset`, `/projects`, `/playground`, `/settings/git`; proxy `/api/*` → API)

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
| POST | `/v1/ask` | `{prompt, system?, project_id?, tier: auto\|fast\|balanced\|strong, max_output_tokens}` |
| GET | `/v1/requests` | Trazas recientes (sin prompts) |
| GET | `/v1/settings/git/status` | Estado del repo del proyecto: branch, remotos (URL sin credenciales), último commit, preferencias |
| PATCH | `/v1/settings/git/preferences` | `{auto_commit?, remote_name?, default_visibility?}` — guardadas en `.askbox/git-settings.json` (no secreto, versionable) |
| POST | `/v1/settings/git/remote` | Conectar remoto existente (`{name, url}`) o crear uno con `gh` (`{name, create:true, slug?, visibility, push?}`) |

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
```

Sin clave de proveedor, `/v1/ask` responde `502 no_provider_key` y deja la traza en `requests`. Con claves de sistema (`GEMINI_API_KEY` / `OPENROUTER_API_KEY` en `.env`) y proyecto con `allow_system_keys=true`, se usan como respaldo.

Tests sin red: `docker run --rm -v "$PWD/api":/srv -w /srv -e PYTHONPATH=/srv askbox-api python -m unittest discover -s tests`

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

- `api/app/llm/`: `router.py` (caché → tier → `model_catalog` → keypool → proveedor), `providers/`, `keypool.py` (cooldown 429, `invalid` en 401), `cache.py` (Redis, TTL por proyecto, 0 = off), `catalog.py`.
- Modelos y cadenas en la tabla `model_catalog` (seed en `001_init.sql`); editar filas en BD, no código.
- Seguridad: AES-256-GCM con AAD `proyecto:proveedor`, nonce aleatorio, `key_version`; la API nunca devuelve claves; logs sin prompts (solo longitud y hash truncado).
- Límites: prompt ≤ 20000 chars, `max_output_tokens` ≤ 8192, rate limit Redis (`ASKBOX_ASK_RATE_PER_MIN`, 30 por defecto; login 10/min por IP).
- Tras HTTPS, poner `ASKBOX_COOKIE_SECURE=true`.
- Pendiente (fases siguientes): escalada única en `auto`, `json_schema`, multiusuario, uso del worker.

## Configuración Git del proyecto (`/settings/git`)

MVP de pantalla de administración para el propio repo de askbox (ver `api/app/gitops.py`,
`api/app/routers/git_settings.py`, `web/app/settings/git/page.js`):

- Lee `git branch`, `git remote -v` (URL siempre saneada, sin credenciales embebidas), último
  commit y si hay cambios sin commitear.
- Permite conectar un remoto existente o crear uno nuevo con `gh repo create` (requiere
  `gh auth login` ya hecho en el host/contenedor).
- Preferencias (`auto_commit`, `remote_name`, `default_visibility`) se guardan en
  `.askbox/git-settings.json` dentro del propio repo (no secreto, versionable).
- `ASKBOX_REPO_PATH` (opcional): ruta del repo accesible desde el proceso de la API. Por
  defecto, calcula la raíz del repo asumiendo ejecución fuera de Docker. **El `docker-compose.yml`
  actual no monta el repo dentro del contenedor `api`**, así que en un despliegue Docker normal
  este endpoint responderá `available: false` con el motivo explícito; para habilitarlo del
  todo, monta el repo como volumen en el servicio `api` (p. ej. `- .:/workspace/repo:rw`) y
  define `ASKBOX_REPO_PATH=/workspace/repo` — cambio de infraestructura fuera del alcance de
  este MVP (ver ownership de `docker-compose.yml`).
- Política de auto-commit/push del propio código de askbox: ver
  `.cursor/skills/git-autopilot/SKILL.md`.
