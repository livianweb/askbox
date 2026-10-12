---
name: askbox-first
description: Para maquetación/UI/visual y copy/docs/legibilidad, usar primero el gateway askbox (POST /v1/ask) con tiers gratuitos del keypool; si falla, no aprueba o se agotan modelos, fallback a subagentes Cursor con la heurística normal del orquestador (tipo → nivel → slug), no Composer genérico por defecto.
---

# Askbox-first (UI y copy antes que Cursor de pago)

## Alcance: solo capa A (LLM)

Esta skill cubre **únicamente** el gateway askbox (`POST /v1/ask`): variables `ASKBOX_API_URL` y
`ASKBOX_ACCESS_TOKEN` en el entorno del agente. **No** configura Git, remotos ni autopilot.

| Capa | Skill / doc |
|------|-------------|
| A — Askbox LLM | **Esta skill** |
| B — Git del proyecto abierto en Cursor | `git-autopilot` + `.askbox/git-settings.json` / `gh` |
| C — Git del kit orquestador | `git-autopilot` en el repo del kit + `docs/git-and-askbox.md` |

El producto askbox y el kit **cursor-agent-orchestrator** son repos separados; el token de
proyecto para ask-first no sustituye credenciales GitHub ni la pantalla `/settings/git` de un
despliegue askbox (que, si se usa, es por repo montado, no «git global de todas las apps»).

## Objetivo

Ahorrar coste de subagentes Cursor en tareas donde **askbox** (gateway LLM con keypool y tiers) suele dar buenos resultados:

1. **Maquetación, temas visuales, ayuda al desarrollo UI** (CSS, layout, componentes, accesibilidad visual, copy de micro-UI).
2. **Mejora de textos, legibilidad y documentación** (README, changelogs, mensajes de error, tooltips, comentarios de alto nivel en docs).

Para el resto (explore, backend, arquitectura, seguridad, implementación mecánica de lógica), **no** usar askbox por defecto: seguir `auto-orchestrator.mdc` y subagentes Cursor.

## Cuándo usar askbox primero

| Tipo de tarea | askbox primero | Tier sugerido |
|---------------|----------------|---------------|
| Propuesta de estilos, paleta, spacing, responsive, JSX/HTML/CSS de un componente | Sí | `balanced` (o `auto`) |
| Revisión visual / consistencia de UI (sin tocar lógica de negocio) | Sí | `balanced` |
| Redacción o mejora de párrafos (docs, ayuda, strings de UI) | Sí | `fast` o `balanced` |
| Changelog, README, comentarios de documentación | Sí | `fast` |
| Refactor grande multi-capa, API, BD, tests, explore de repo | No | Cursor (`fast`/`balanced`/…) |
| Plan de arquitectura, seguridad, review Bugbot | No | Cursor `strong*` |

Si la tarea mezcla UI/copy con código de producto, el padre puede: (a) pedir a askbox solo el bloque visual o el texto, e implementar el código con Cursor; o (b) ir directo a Cursor si el diff toca muchos archivos de lógica.

## Requisitos del entorno (sin secretos en el repo)

El agente **no** guarda tokens en skills ni en git. Configuración solo por **variables de entorno** en la máquina donde corre el padre (o sesión de shell):

| Variable | Uso |
|----------|-----|
| `ASKBOX_API_URL` | Base de la API de **tu** instancia askbox, sin barra final. Ejemplos: `https://askbox.cliente.com/api`, `http://192.168.x.x:8066` (LAN). No hay un host único global — configúralo por cliente/entorno. |
| `ASKBOX_ACCESS_TOKEN` | Bearer de proyecto: prefijo `ab_live_…`, scope `ask:read`. Crear en la UI de askbox (`/projects` → tokens) o vía API; **nunca** commitear ni pegar en el chat. |

Alternativa solo en desarrollo local con sesión de usuario: cookie de login en `curl -b` — preferir token de proyecto para scripts del agente.

Si **falta** URL o token, el padre **anuncia** que askbox no está configurado y pasa al fallback Cursor sin intentar adivinar credenciales.

## Cómo llamar (contrato)

- **Endpoint:** `POST {ASKBOX_API_URL}/v1/ask`
- **Auth:** `Authorization: Bearer $ASKBOX_ACCESS_TOKEN` (o cookie de sesión en local).
- **Cuerpo JSON** (mínimo):

```json
{
  "prompt": "…instrucciones y contexto…",
  "system": "opcional: rol, tono, restricciones",
  "tier": "auto|fast|balanced|strong",
  "max_output_tokens": 4096
}
```

Con token de proyecto, `project_id` en el cuerpo se ignora (el proyecto viene del token).

**Ejemplo (shell, sin volcar secretos):**

```bash
curl -sS -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ASKBOX_ACCESS_TOKEN}" \
  -d '{"prompt":"…","tier":"balanced","max_output_tokens":4096}' \
  "${ASKBOX_API_URL}/v1/ask"
```

Respuesta útil: campo de texto de salida del modelo (según esquema OpenAPI actual). Errores: `401` sin auth, `429` rate limit, `502` con códigos como `no_provider_key`, `provider_failed` o keypool agotado — tratar como señal de **fallback**.

Opcional: `POST /v1/ask/stream` (SSE) para respuestas largas; mismo auth y cuerpo.

## Qué NO enviar a askbox

Alineado con `no-secret-leak` en proyectos que lo tengan:

- Contenido de `.env`, claves API, tokens completos, `ASKBOX_MASTER_KEY`, contraseñas.
- Ciphertext de claves, cabeceras `Authorization` de terceros, dumps de BD con datos sensibles.
- Volcados enormes del repo: enviar **fragmentos acotados** (componente, diff relevante, sección de doc) y rutas, no el árbol entero.

No registrar en logs el valor de `ASKBOX_ACCESS_TOKEN` ni el prompt completo en producción si el proyecto lo desaconseja.

## Criterio de aprobación del resultado

Antes de aplicar el texto/estilos al repo, el **padre** valida brevemente:

1. **Utilidad:** responde al encargo (UI coherente con el stack del proyecto, copy en el idioma acordado).
2. **Seguridad:** la salida no introduce secretos ni instrucciones peligrosas.
3. **Encaje técnico:** CSS/componentes compatibles con el framework del repo (React/Next, etc.); si askbox inventa APIs inexistentes, corregir en implementación Cursor o rechazar y reintentar con prompt más contexto.

**Aprobación humana:** si el usuario rechaza el resultado en el turno («no me gusta», «otro tono»), **no** reintentar askbox en bucle: máximo **una** repetición con prompt ajustado; luego **fallback Cursor**.

Si askbox devuelve vacío, basura, o JSON de error → fallback inmediato.

## Fallback a Cursor

Usar subagentes Cursor (`Task` / trabajo del padre) cuando ocurra **cualquiera** de:

- `ASKBOX_API_URL` o `ASKBOX_ACCESS_TOKEN` no configurados.
- HTTP `429`, `502`, timeouts, o mensaje de modelos/claves agotadas.
- Resultado no aprobado tras el reintento permitido.
- La tarea requiere **editar archivos** con ownership y verify: askbox solo **genera** propuestas; la escritura en disco sigue siendo Cursor (salvo que el padre aplique un diff pequeño él mismo).

En fallback, **no** enviar todo a Composer genérico: **retomar el flujo Auto** como si askbox no hubiera sido opción — misma heurística que `auto-orchestrator.mdc` y `plan-implement-verify`:

1. Clasificar **tipo de tarea** y asignar **nivel** (`fast` / `balanced` / `strong` / `strong-thinking`) como en cualquier otro Task.
2. Consultar **`available_subagent_models`** y elegir el **mejor slug** para ese tipo y nivel.
3. **UI / maquetación / visual** → **`balanced` + Composer** (o Grok medio) **primero**; especialidad visual cara solo si el encargo lo exige y askbox ya falló.
4. **Copy / docs / legibilidad** → **`fast`/`balanced` + Composer**; especialidad escritura solo si textos largos y Composer no basta.
5. **Composer / Grok** como destino habitual del fallback; especialidades caras son **excepción** anunciada, no sustituto de configurar `ASKBOX_*`.

Anunciar explícitamente el cambio de vía (ver abajo).

## Cómo debe anunciar el padre

En el primer uso de askbox en un turno o subfase:

- **Vía askbox:** `Askbox-first — <tipo: UI|copy|docs> — tier <fast|balanced|auto> — endpoint configurado sí/no`
- Tras respuesta: `Askbox — <ok|fallback> — <motivo breve si fallback>`

Al lanzar subagente Cursor (incluido fallback):

- **Vía Cursor:** `Cursor — <tipo de tarea> — <nivel> — <slug> — <por qué no Opus>` (+ excepción fuera de Composer si aplica)

En el resumen final, listar qué bloques salieron de askbox y qué de Cursor.

## Relación con otros skills

- Orquestación general: `plan-implement-verify`, reglas en `auto-orchestrator.mdc`.
- Modificar routing interno de askbox (producto): `llm-routing` solo en el repo askbox, no en el kit portable.
- **Git (capas B y C):** `git-autopilot` tras Verify en el repo que toques; nunca commitear
  tokens ni `.env`. No usar askbox para commitear salvo que operes vía API git de un despliegue
  que monte **ese** repo (`?repo=…`).
