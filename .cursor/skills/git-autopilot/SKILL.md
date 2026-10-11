---
name: git-autopilot
description: Política de auto-commit y auto-push cuando estas rules/skills están instaladas. Define cuándo commitear, cuándo crear remoto con gh, y la pantalla de configuración Git por proyecto. Anula el comportamiento por defecto de Cursor de "solo commit si el usuario lo pide".
---

# Git Autopilot (commit + push autónomo)

## Por qué existe esta skill

Cursor, por defecto, no hace commits ni pushes salvo que el usuario lo pida explícitamente en
el turno. **Esta skill cambia esa norma por defecto, de forma permanente, para cualquier
proyecto donde esté instalada**: el usuario ya autorizó, como política general del paradigma,
que el padre/implementador decida cuándo commitear y pushear sin pedir OK cada vez.

Si este archivo está presente en `.cursor/skills/git-autopilot/` (o referenciado desde
`auto-orchestrator.mdc`), aplica siempre — no solo cuando el usuario lo menciona en el turno
actual.

## Tres capas de configuración (no mezclar)

| Capa | Git autopilot | Dónde |
|------|----------------|-------|
| **A. Askbox LLM** | *No aplica* — no es Git | `ASKBOX_*` en env; ver `askbox-first` |
| **B. Git del proyecto** | Commit/push al remoto del **repo abierto en Cursor** | `.askbox/git-settings.json` en ese repo, `gh`/git local; pantalla propia o askbox `/settings/git` solo si ese repo está montado en askbox |
| **C. Git del orquestador** | Commit/push al remoto del **kit** | Repo `cursor-agent-orchestrator`: su `.askbox/git-settings.json`, su `origin`, su `gh` |

Guía extendida: `docs/git-and-askbox.md` (en el kit) o la copia en el proyecto tras `install.sh`.

**Askbox producto no centraliza git de apps arbitrarias.** La UI `/settings/git` de askbox es
opcional y solo afecta a repos que ese despliegue monta (`ASKBOX_REPO_PATH` + `ASKBOX_EXTRA_REPOS`).
El token `ASKBOX_ACCESS_TOKEN` (askbox-first) **no** autoriza operaciones git.

## Alcance: kit vs proyecto (capas B y C)

Antes de `git add` / `git push`, fija el repo activo:

```bash
git rev-parse --show-toplevel
```

- **Kit (capa C):** el toplevel es el directorio del orquestador (`cursor-agent-orchestrator`).
  Mejoras a rules/skills del kit → commit + push al remoto **del kit**, sin pedir OK.
- **Proyecto (capa B):** cualquier otro toplevel (tu app, el repo askbox como producto, etc.).
  Tras cerrar fase o cambio sustancial → commit + push al remoto **de ese repo**.

**Nunca:** usar el remoto, PAT o `git-settings.json` del kit para la app; ni el de askbox
producto para publicar el kit; ni asumir un solo `origin` compartido entre repos.

1. **El propio kit de orquestación** (`cursor-agent-orchestrator` o como lo hayas llamado):
   capa **C** — remoto y credenciales propios del kit.
2. **El proyecto gestionado** (el producto en el que trabajas): capa **B** — remoto y
   credenciales propios de ese proyecto.

## Cuándo commitear (y cuándo no)

**Sí, commitear + intentar push** cuando:

- Se cierra una fase completa de un plan.
- Se entrega una feature usable de principio a fin (aunque sea MVP/stub documentado como tal).
- Se corrige un bug importante o de seguridad.
- Se actualizan rules/skills del propio kit de orquestación.
- Al cierre de cualquier turno donde el repo quedó en un estado "visible" distinto al del
  último commit (no dejar trabajo terminado sin commitear).

**No** commitear todavía (seguir trabajando) cuando:

- Estás a mitad de un paso de un plan más amplio y el código no compila / no pasa los checks
  básicos de esa fase.
- Sería un micro-commit por cada typo o ajuste trivial dentro del mismo paso lógico.
- El cambio deja el repo en un estado roto a propósito de forma transitoria — agrupa esos
  cambios en un solo commit coherente al terminar el paso.

**Regla dura:** el repo no debe quedar desactualizado respecto al trabajo ya terminado y
visible para el usuario al cierre del turno. Ante la duda entre commitear ya o esperar un poco
más de contexto, prefiere cerrar con un commit coherente antes que dejarlo sin commitear.

## Cómo commitear

1. Actualizar `.gitignore` si hace falta: ignorar `.env`, secretos, cachés (`__pycache__/`,
   `node_modules/`, `.next/`, `.venv/`, logs, artefactos de build); **no** ignorar código
   fuente relevante ni la config no secreta de la pantalla de ajustes Git (ver más abajo).
2. `git add` solo lo relevante del cambio (evita `git add -A` ciego si hay archivos generados
   que aún no estén cubiertos por `.gitignore`).
3. Mensaje de commit: explica el **por qué**, no solo el qué (resumen en una línea + cuerpo si
   aporta contexto). Nunca incluir valores de secretos en el mensaje.
4. `git push` si hay remoto con tracking configurado.
5. Si no hay remoto, intentar en este orden (el primero disponible):
   1. **`gh` del host**: `gh auth status` OK → `gh repo create` (visibilidad según la preferencia
      del proyecto; por defecto `private` salvo que el usuario pida `public`) y push.
   2. **UI/API de settings git** (opcional; p. ej. despliegue askbox con el repo montado):
      `GET /v1/settings/git/status?repo=<nombre>` → `github_token_configured: true` **para ese
      repo** → `POST /v1/settings/git/remote?repo=<nombre>` con `create`/`push` según toplevel
      actual (`askbox` = producto, `cursor-agent-orchestrator` = kit si está en
      `ASKBOX_EXTRA_REPOS`). Auth: sesión o Bearer de **usuario** API askbox (no confundir con
      `ASKBOX_ACCESS_TOKEN` de proyecto para `/v1/ask`). Push: `POST /v1/settings/git/push?repo=…`.
      El agente **no necesita ni debe conocer el PAT**: la API lo descifra en memoria.
      Si el repo **no** está montado en askbox, usar solo el paso 1 o 3 — no forzar askbox.
   3. Si no hay ninguna credencial: dejar el repo local con el commit hecho y documentar en el
      informe final los comandos exactos (`gh auth login`, `git remote add`, o token en la
      pantalla git **del repo correspondiente**) para que el usuario publique.

## Credenciales de GitHub: dónde viven

- Opciones válidas: `gh auth` del host, o un **GitHub token guardado cifrado por la app** vía su
  pantalla de settings git (campo password → API → cifrado con la master key de la app, p. ej.
  AES-GCM; nunca en claro en BD, `.git/config`, logs ni respuestas; la API solo expone
  `github_token_configured` y `last4`).
- Al pushear con token: pasarlo solo por entorno del subproceso (`GIT_CONFIG_*` con
  `http.https://github.com/.extraheader`, o `GH_TOKEN`), nunca en argv ni en la URL del remoto.
- Pedir al usuario un PAT por chat **no** es el camino: indícale que lo pegue en la pantalla de
  settings git. Si lo pegara en el chat, no lo repitas ni lo escribas en ficheros.

## Nunca commitear

- `.env` ni ningún archivo con secretos reales.
- Claves de API, tokens Bearer completos, enlaces de reset de contraseña, ciphertext
  descifrado.
- Cualquier cosa prohibida por la rule de secretos del proyecto (p. ej. `no-secret-leak.mdc`).

Antes de cada commit, repasa el diff mentalmente contra estas categorías; si hay duda sobre si
un archivo contiene secretos, **no** lo incluyas y avisa al usuario en el informe.

## Pantalla de configuración Git por proyecto (capa B; kit C igual patrón)

Cada **repo** con git-autopilot (tu app **o** el kit) puede tener preferencias en
`.askbox/git-settings.json` (plantilla: `.askbox/git-settings.json.example`). Las credenciales
**no** van ahí.

Opcionalmente, cada **producto** puede implementar una pantalla/endpoint de administración (askbox
lo hace para repos montados en ese servidor). Al iniciar un proyecto nuevo sin UI git (o si falta),
el agente **puede** crear esa pantalla siguiendo `templates/git-settings-screen.md`; no es
obligatorio si el equipo usa solo `gh` en el host.

La pantalla/endpoint, si existe, debería ofrecer:

- **Estado**: remoto conectado (nombre + URL **sin credenciales embebidas**), branch actual,
  último commit (hash corto, fecha, resumen), si hay cambios sin commitear.
- **Conectar o crear remoto**: permitir indicar la URL de un remoto existente, o crear uno
  nuevo vía `gh repo create` si `gh` está autenticado, o vía la API REST de GitHub con un
  **token guardado cifrado** (campo password + botón guardar/borrar + indicador de si hay token).
- **Preferencias**: `auto_commit` (on/off), `remote_name`, `default_visibility`
  (`public`/`private` por defecto al crear repos nuevos).
- **Dónde vive la config**: las preferencias (no secretas) se guardan **dentro del propio
  repo** (p. ej. `.askbox/git-settings.json` o equivalente por proyecto), para que viajen con
  el código. Las credenciales (tokens, SSH keys) **nunca** se guardan ahí: viven en `gh auth`,
  en variables de entorno del host/contenedor, o **cifradas en la BD/almacén de la app**
  (GitHub token vía API: `GET` solo `configured`+`last4`; `PUT/POST` guarda; `DELETE` borra),
  siempre fuera del repo versionado.
- **Degradación clara**: si el proceso que sirve esta pantalla no tiene acceso al `.git` del
  repo (p. ej. corre en un contenedor sin ese volumen montado), debe decirlo explícitamente en
  vez de fallar en silencio o simular datos. Es válido un MVP tipo *stub* que solo exponga
  `git remote -v` / estado básico mientras no haya acceso completo.

La implementación concreta (rutas, framework, UI) es responsabilidad de cada app. Usa
`templates/git-settings-screen.md` de este kit como guía de contrato (endpoints sugeridos,
forma de las preferencias) al construir la tuya.

## Relación con el resto del paradigma

- Esta política se ejecuta **después** de Verify en el flujo `plan-implement-verify` (ver fase
  de autopilot ahí), no la sustituye: primero build/tests/health, luego commit/push.
- Sigue aplicando `multitask-ownership.mdc`: si hubo Tasks paralelos, el commit lo hace el
  padre tras integrar y comprobar que no hay solapes — no cada subagente por su cuenta.
- Anuncia en el informe final: qué se commiteó, el mensaje usado, si hubo push y a qué remoto
  (URL saneada, nunca con credenciales), y si falta autenticación `gh` para publicar.
