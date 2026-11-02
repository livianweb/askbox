# OpenClaw sanitize-proxy

Sidecar de [OpenClaw](https://github.com/openclaw/openclaw) para este repo. **No** sustituye la API askbox, el worker, la web ni Ollama, y **no** entra en el `docker compose up` de la raíz.

| Pieza | Ruta |
|---|---|
| Proxy (código vivo) | `openclaw/sanitize-proxy/app.py` |
| Imagen | `openclaw/sanitize-proxy/Dockerfile` |
| Compose hermano | `openclaw/docker-compose.yml` |
| Variables (sin secretos) | `openclaw/.env.example` |
| Contrato del cliente `/v1/route` | `openclaw/sanitize-proxy/ROUTE_PHASE2.md` |

OpenClaw habla OpenAI-compatible con el proxy (`sanitize-proxy:8080`). El proxy enmascara el cuerpo y llama a askbox. El token de askbox no se monta en el contenedor de OpenClaw.

## Qué hace

1. **Enmascara** antes de reenviar: claves privadas, JWT, prefijos de API key, secretos `password=` / `Bearer`, CIF/NIF e IPs privadas.
2. **Quita el prefijo de modelo** `askbox/`, `ollama/` y `ollama-safe/` (`askbox/qwen2.5-coder:14b` → `qwen2.5-coder:14b`).
3. **Recorte lean** en destinos locales (allin / ollama / un modelo concreto): system corto y estable, últimos N mensajes enteros, tope de prompt. Los tiers `cloud-*` no se recortan así.
4. **Cliente `POST /v1/route`** si `ROUTE_ENABLED=1`. Pide un perfil (`lean` / `standard` / `full`) y clampa la respuesta a topes fijos. Con `ROUTE_ENABLED=0` el recorte es el de lean v2. Detalle en `sanitize-proxy/ROUTE_PHASE2.md`.
5. **SPLIT** (activo por defecto): el último mensaje de usuario elige el destino.
   - Código → modelo local `LEAN_OLLAMA_MODEL` (`qwen2.5-coder:14b`) vía `POST /v1/ask` con `provider=ollama`.
   - Chat → tier cloud `CHAT_CLOUD_MODEL` (`cloud-fast` → `tier=fast` en askbox).
   - Un `model` que ya es `cloud`, `cloud-fast`, `cloud-balanced` o `cloud-strong` no se reescribe.

### Sticky: solo el último user

`_is_code_turn` decide el split con el **último** mensaje de usuario (fences, `_CODE_RX` o palabras clave de código). Un bloque de código en mensajes anteriores no arrastra el turno de chat al modelo local.

`_route_features` sigue calculando `has_code` sobre los últimos 4 mensajes, pero eso solo viaja al router de contexto. El split **no** lee `feats["has_code"]`.

## Cómo habla con askbox

El servicio se une a la red Docker externa `askbox_default` (la del compose de la raíz; el nombre es `<proyecto>_default`, y con el directorio `askbox` queda `askbox_default`).

| Variable | Destino |
|---|---|
| `ASKBOX_UPSTREAM` | `http://api:8000` — `POST /v1/ask` y, si el route está activo, `POST /v1/route` |
| `OLLAMA_UPSTREAM` | `http://ollama:11434` — passthrough `/ollama/v1/*` (tools), también con recorte lean |
| `ASKBOX_TOKEN` | `Authorization: Bearer` hacia askbox. Vacío → 503 en el chat |

No publica puertos en el host. Desde la misma red el health es `http://openclaw-sanitize:8080/health`.

Rutas del proxy: `GET /health`, `POST /sanitize` (vista previa del enmascarado), `GET /v1/models`, `POST /v1/chat/completions`, `/ollama/v1/{path}`.

## Variables

Nombres y valores de ejemplo (el token va vacío): `openclaw/.env.example`.

| Variable | Default en compose | Rol |
|---|---|---|
| `ASKBOX_TOKEN` | vacío | Bearer hacia askbox |
| `ASKBOX_MODE` | `allin` | `mode` cuando el modelo es `allin` |
| `SPLIT_ENABLED` | `1` | Código local / chat `cloud-fast` |
| `CHAT_CLOUD_MODEL` | `cloud-fast` | Tier cloud del chat |
| `LEAN_OLLAMA_MODEL` | `qwen2.5-coder:14b` | Modelo local del turno de código |
| `LEAN_KEEP_MESSAGES` | `4` | Mensajes que conserva lean v2 |
| `ROUTE_ENABLED` | `0` en compose; `1` en el ejemplo | Cliente `/v1/route` |
| `ROUTE_URL` | `http://api:8000/v1/route` | URL del perfil de contexto |
| `ROUTE_FALLBACK` | `lean` en compose; `standard` en el ejemplo | Perfil si la nube no responde |
| `ROUTE_ON_CHAT` | `0` | No enruta turnos que ya van a cloud |

`ROUTE_ENABLED=0` deja el proxy en lean v2 aunque el ejemplo documente el valor `1` del despliegue con `/v1/route` ya vivo.

## Arranque

Askbox tiene que estar creado antes (hace falta la red y los DNS `api` y `ollama`):

```bash
docker compose up -d
cp openclaw/.env.example openclaw/.env   # rellenar ASKBOX_TOKEN
docker compose -f openclaw/docker-compose.yml up -d --build sanitize-proxy
```

Ese `-f` usa `openclaw/` como directorio de proyecto, así que las variables salen de `openclaw/.env`, no del `.env` de la raíz (ahí `ASKBOX_TOKEN` es el secreto del servidor). Si hace falta forzarlo: `--env-file openclaw/.env`.

Solo el proxy. El gateway (`openclaw-gateway`) es el otro servicio del mismo fichero: exige `OPENCLAW_GATEWAY_TOKEN` (`openssl rand -hex 32`), un `openclaw/data/` y `openclaw/ssh/config` en el host, y los bind mounts de proyectos/claves SSH (en el vps4, `/var/www/proyectos_web` y `/root/.ssh/keys_remote`). Esos directorios están en `openclaw/.gitignore`.

El auto-update de askbox reconstruye el compose de la raíz. Este sidecar se reconstruye aparte, con el comando de arriba.

Imagen: Python 3.11, usuario no root, sin herramientas de más. El healthcheck pega a `http://127.0.0.1:8080/health`.
