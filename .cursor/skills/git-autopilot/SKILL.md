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

## Alcance: dos repos distintos a tener en cuenta

1. **El propio kit de orquestación** (`cursor-agent-orchestrator` o como lo hayas llamado):
   cuando el padre mejora sus rules/skills, debe commitear y pushear al remoto público sin
   pedir OK.
2. **El proyecto gestionado** (el producto en el que trabajas): tras cerrar una fase o un
   cambio sustancial, el padre/implementador debe dejar el repo actualizado (commit + push)
   sin esperar instrucción explícita.

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
5. Si no hay remoto: comprobar `gh auth status`. Si está autenticado, crear el repo con
   `gh repo create` (visibilidad según la preferencia del proyecto; por defecto `private` salvo
   que el usuario pida `public`) y pushear. Si `gh` no está disponible o no está autenticado,
   dejar el repo local con el commit hecho y documentar en el informe final los comandos
   exactos para que el usuario publique manualmente.

## Nunca commitear

- `.env` ni ningún archivo con secretos reales.
- Claves de API, tokens Bearer completos, enlaces de reset de contraseña, ciphertext
  descifrado.
- Cualquier cosa prohibida por la rule de secretos del proyecto (p. ej. `no-secret-leak.mdc`).

Antes de cada commit, repasa el diff mentalmente contra estas categorías; si hay duda sobre si
un archivo contiene secretos, **no** lo incluyas y avisa al usuario en el informe.

## Pantalla de configuración Git por proyecto

Cada proyecto gestionado con este paradigma debería tener una forma de configurar su propia
integración Git sin depender de que alguien use la terminal cada vez. Al iniciar un proyecto
nuevo (o si falta), el agente debe crear una pantalla/endpoint de administración con:

- **Estado**: remoto conectado (nombre + URL **sin credenciales embebidas**), branch actual,
  último commit (hash corto, fecha, resumen), si hay cambios sin commitear.
- **Conectar o crear remoto**: permitir indicar la URL de un remoto existente, o crear uno
  nuevo vía `gh repo create` si `gh` está autenticado en el host/contenedor.
- **Preferencias**: `auto_commit` (on/off), `remote_name`, `default_visibility`
  (`public`/`private` por defecto al crear repos nuevos).
- **Dónde vive la config**: las preferencias (no secretas) se guardan **dentro del propio
  repo** (p. ej. `.askbox/git-settings.json` o equivalente por proyecto), para que viajen con
  el código. Las credenciales (tokens, SSH keys) **nunca** se guardan ahí: viven en `gh auth`
  o variables de entorno del host/contenedor, fuera del repo versionado.
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
