# VS Code + Roo Code contra askBox

Playbook para apuntar un VS Code limpio a la API OpenAI-compatible de askBox
(perfiles cloud y `local-qwen7b`). El mismo contenido, en JSON, lo sirve la API
para que un agente (Grok Bot u otro) lo aplique sin leer el repo.

Versión del playbook: **1**.

## Dónde está (URL estable)

Sin autenticación y sin secretos. Tras el despliegue de esta versión:

| Qué | URL |
|---|---|
| JSON (agente) | `https://askbox.softportweb.com/v1/ide/vscode-roo` |
| La misma ruta detrás del proxy `/api` | `https://askbox.softportweb.com/api/v1/ide/vscode-roo` |
| Este markdown | `https://askbox.softportweb.com/v1/ide/vscode-roo.md` |
| API directa en el host | `http://127.0.0.1:8066/v1/ide/vscode-roo` |

El origen sale de `ASKBOX_PUBLIC_URL` (producción: `https://askbox.softportweb.com`).
La base OpenAI que debe usar Roo es `{ASKBOX_PUBLIC_URL}/v1`, o el valor de
`ASKBOX_IDE_OPENAI_BASE_URL` si el chat no cuelga del mismo host. En vps4 SoftPort
esa base es:

`https://askbox.softportweb.com/v1`

Un agente basta con un `GET` al JSON: trae `base_url`, la forma del header
(sin el secreto), `profiles`, `steps`, el fichero que hay que escribir
(`roo_import`) y este markdown en `markdown`.

## Requisitos

- VS Code limpio (estable). En el portable de Linux (ASUS), `rg` (ripgrep) tiene
  que estar en el `PATH` del proceso que lanza `code`. Si no está, enlace
  `ripgrep-universal` del propio portable a un directorio de ese `PATH`
  (por ejemplo `~/.local/bin/rg`).
- Extensión **Roo Code**, id `RooVeterinaryInc.roo-cline`, desde Open VSX
  (VSIX) o con `code --install-extension RooVeterinaryInc.roo-cline`.
- Token de cliente **sanitize**: variable de entorno `SANITIZE_CLIENT_TOKEN`.
  Lo tiene el operador del servidor. No es `ASKBOX_TOKEN` (secreto interno de
  askBox), ni `ASKBOX_ACCESS_TOKEN`, ni un token de proyecto `ab_live_…`.
- No uses la extensión Continue. Su pestaña y las completions llaman a
  `POST /v1/completions`, y esta base responde **404**. Roo habla
  `POST /v1/chat/completions`.

El token solo vive en el fichero local de Roo (`chmod 0600`). Roo **no**
expande variables de shell: hay que pegar el valor, no la cadena
`${SANITIZE_CLIENT_TOKEN}`. No commitees ese fichero.

Comprueba el token antes de configurar el IDE:

```bash
curl -sS -H "Authorization: Bearer ${SANITIZE_CLIENT_TOKEN}" \
  https://askbox.softportweb.com/v1/models
```

Deben aparecer `cloud-fast`, `cloud-balanced`, `cloud-strong` y `local-qwen7b`.

## Cloud y `local-qwen7b`

| | Cloud (`cloud-fast`, `cloud-balanced`, `cloud-strong`) | `local-qwen7b` |
|---|---|---|
| Qué corre | Gemini, cascada de askBox. `cloud-balanced` existe en `/v1/models`; el arranque de Roo en el ASUS no lo trae de serie. | Ollama solo en vps4, tag `qwen2.5-coder-7b-roo`. Sin fallback a Gemini. |
| Privacidad | El prompt sale al proveedor de la cascada (Gemini y, si toca, OpenRouter). | El turno se queda en el Ollama de vps4. |
| Latencia | Segundos, salvo cuota o frío del proveedor. | El 7B ocupa unos 7 GB. En CPU ARM de 4 núcleos, sin GPU, el primer turno con el system grande de Roo puede tardar minutos. |
| Límites en Roo | Ventana 128000, salida máxima 8192. | Ventana **32768**, salida máxima **4096**. |
| Tools | Tool calls nativos del modelo cloud. | Sanitize convierte el JSON o XML que el modelo escribe como texto en `tool_calls` OpenAI. El stream manda keepalives SSE cada 10 s: no cortes el cliente por silencio. |

Perfil de arranque: **`askbox-cloud-strong`**.

Si Roo no fuerza modelo (`mode=allin`, o un perfil sin `model`), los locales
están apagados (`OLLAMA_DEFAULT_ACTIVE_MODELS=none`) y la cascada es
Gemini y luego OpenRouter. Eso **no** es Qwen. El perfil `askbox-local-qwen7b`
tiene que mandar el id `local-qwen7b`.

## Pasos manuales

1. Instala VS Code. En portable Linux, deja `rg` en el `PATH` (ver requisitos).
2. Instala Roo Code (`RooVeterinaryInc.roo-cline`), mejor el VSIX de Open VSX:
   `https://open-vsx.org/api/RooVeterinaryInc/roo-cline/latest` (campo de descarga del JSON).
3. Crea `~/.roo/askbox-roo-settings.json` con el objeto `roo_import` del playbook.
   Sustituye `openAiApiKey` por el valor de `SANITIZE_CLIENT_TOKEN`. `chmod 0600`.
   Proveedor OpenAI-compatible: `apiProvider` es `"openai"` (no `"openai-native"`).
   La base termina en `/v1`; Roo añade `/chat/completions`.
4. En el `settings.json` de **ese** VS Code, añade:

   ```json
   "roo-cline.autoImportSettingsPath": "~/.roo/askbox-roo-settings.json"
   ```

   Rutas típicas del fichero: `~/.config/Code/User/settings.json`, o en portable
   `$VSCODE_PORTABLE/user-data/User/settings.json`. No borres el resto de claves.
   `~/` lo expande Roo al activar la extensión.
5. Command Palette → **Developer: Reload Window**. Roo importa al arrancar
   (fusiona perfiles; no borra los que ya tuvieras).
6. En Roo, el perfil activo debe ser `askbox-cloud-strong`. Para el 7B local,
   cambia a `askbox-local-qwen7b`.
7. Si el índice de codebase estorba, déjalo apagado. El import ya manda
   `globalSettings.codebaseIndexEnabled: false`. Si tu build de Roo usa otro
   nombre, apágalo en la UI de Roo.

El índice de codebase y el system de Roo son grandes. En local, calienta el
modelo en el servidor antes del primer turno (sección siguiente).

## Calentar `local-qwen7b`

Esto se hace **en vps4**, donde corre Ollama, no en el portátil. El contenedor
deja el modelo en RAM `30m` (`OLLAMA_KEEP_ALIVE`). Un generate corto renueva
ese plazo:

```bash
curl -sS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5-coder-7b-roo","keep_alive":"30m","prompt":"ok","stream":false}'
```

Si el tag no está cargado, el primer turno de Roo paga la carga (minutos en
esa CPU) y además el prefijo de sistema. Los keepalives SSE cada 10 s mantienen
el stream vivo mientras tanto.

## Qué debe hacer un agente

Orden del array `steps` del JSON (no te saltes el de comprobar el token):

1. `ensure_ripgrep` — `rg` en el PATH del portable, si hace falta.
2. `install_extension` — `RooVeterinaryInc.roo-cline`. No instales Continue.
3. `read_env` — lee `SANITIZE_CLIENT_TOKEN` del entorno del operador. No lo
   imprimas ni lo escribas en el repo.
4. `http_get` — `GET {base_url}/models` con `Authorization: Bearer …`.
5. `write_json` — `~/.roo/askbox-roo-settings.json` = `roo_import`, con la clave
   ya sustituida, modo `0600`.
6. `merge_vscode_settings` — `roo-cline.autoImportSettingsPath` apuntando a ese
   fichero.
7. `reload_window` — `workbench.action.reloadWindow`.
8. `select_api_config` — deja `askbox-cloud-strong` salvo que pidan el local.

## Problemas frecuentes

| Síntoma | Qué mirar |
|---|---|
| `tool_calls` a medias o la tool no corre | El 7B a veces corta el JSON/XML al llegar a 4096 tokens de salida. Acorta la tarea o pasa a `askbox-cloud-strong`. Sanitize solo puede promover a `tool_calls` un bloque que llegó entero. |
| HTTP 413 | El historial de Roo no cabe (tope de cuerpo de askBox, `ASKBOX_MAX_PROMPT_CHARS`, u otro proxy). Abre una tarea nueva en Roo; no reenvíes el hilo entero. |
| Tarda minutos y parece colgado | Perfil local, modelo frío o fuera de RAM (`ollama ps` en vps4). Calienta con el `generate` de arriba. El stream debería seguir vivo por los keepalives de 10 s. |
| Responde Gemini cuando querías Qwen | Perfil equivocado, o la petición no lleva `model=local-qwen7b`. Sin modelo forzado y con `OLLAMA_DEFAULT_ACTIVE_MODELS=none`, la cascada es Gemini → OpenRouter. |
| 401 | El Bearer no es `SANITIZE_CLIENT_TOKEN`, o el fichero guardó el placeholder sin sustituir. |
| 404 en completions o en la pestaña de Continue | Estás en Continue, o la base no termina en `/v1`. Roo + `/v1/chat/completions`. |
| El import no entra | `roo-cline.autoImportSettingsPath` vacío, ruta mal resuelta, o no hubo Reload Window. Roo escribe el motivo en su Output. |
| `rg` / búsqueda del portable falla | Falta ripgrep en el PATH de ese VS Code. Symlink a `ripgrep-universal`. |

## Variables

| Variable | Dónde | Uso |
|---|---|---|
| `SANITIZE_CLIENT_TOKEN` | Cliente (quien configura VS Code) | Bearer de `/v1/models` y `/v1/chat/completions`. No va en git. |
| `ASKBOX_PUBLIC_URL` | Servidor askBox | Origen público. El JSON arma las URLs del playbook con esto. |
| `ASKBOX_IDE_OPENAI_BASE_URL` | Servidor askBox, opcional | Si la base OpenAI no es `{ASKBOX_PUBLIC_URL}/v1`. |
| `ASKBOX_TOKEN` | Servidor | Secreto interno. **No** lo pongas en Roo. |
| `OLLAMA_KEEP_ALIVE` | Contenedor Ollama | Default `30m`. |
| `OLLAMA_DEFAULT_ACTIVE_MODELS` | Servidor | En este despliegue, `none`: sin modelo forzado no entra Qwen. |
