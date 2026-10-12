# Askbox, orquestador y Git: tres configuraciones independientes

El kit **cursor-agent-orchestrator** y el producto **askbox** son repositorios distintos con
remotos distintos. La confusión habitual es pensar que «todo se configura en askbox» o que la
pantalla `/settings/git` de askbox es el único lugar para git de cualquier app. No: hay **tres
capas** que no se mezclan.

## Tabla de capas

| Capa | Qué configura | Dónde | Secretos |
|------|----------------|-------|----------|
| **A. Askbox LLM** | Gateway para askbox-first (`POST /v1/ask`) | Variables de entorno del host / sesión del agente Cursor: `ASKBOX_API_URL`, `ASKBOX_ACCESS_TOKEN` | Token creado en askbox (`/projects` → tokens de proyecto). **No** va en git ni en skills. |
| **B. Git del proyecto** | Remoto, push, preferencias autopilot del **repo abierto en Cursor** (tu app, askbox como producto, etc.) | Dentro de **ese** repo: `.askbox/git-settings.json` (preferencias no secretas), `git remote`, `gh auth` en el host | PAT en `gh` o, si usas askbox como UI opcional, token cifrado **por repo** en la BD de askbox |
| **C. Git del orquestador** | Remoto y autopilot del **kit** (`cursor-agent-orchestrator`) | Repo del kit: `/srv/softportweb/cursor-agent-orchestrator` (o donde lo clones), mismo patrón que B | Igual que B, pero **solo** para el remoto del kit — nunca el de askbox ni el de tu app |

**Regla de oro:** un commit/push siempre va al remoto del repo cuyo `.git` es el **cwd** (o el
toplevel que devuelve `git rev-parse --show-toplevel`). No reutilizar remoto, token ni identidad
del kit para la app, ni al revés.

## Capa A — Askbox LLM (askbox-first)

- Documentación: `.cursor/skills/askbox-first/SKILL.md`.
- Askbox **no** sustituye a Git: solo aporta inferencia barata para UI, textos, Q&A y borradores antes de gastar
  subagentes Cursor.
- El orquestador **sí** necesita A en el entorno del agente para usar askbox-first; eso es
  independiente de dónde viva el código del proyecto.

## Capa B — Git del proyecto en curso

Cuando trabajas en **tu aplicación** (o en el repo askbox como producto):

1. Abre ese proyecto en Cursor; git-autopilot opera sobre **ese** árbol.
2. Preferencias versionables (opcional): copia `.askbox/git-settings.json.example` →
   `.askbox/git-settings.json` y ajusta `auto_commit`, `remote_name`, `default_visibility`.
3. Remoto y credenciales:
   - **Canónico portable:** `gh auth login` en el host + `git remote add origin …` o
     `gh repo create`.
   - **Opcional (deploy askbox):** si el repo está montado en el contenedor API
     (`ASKBOX_REPO_PATH` para askbox, o entrada en `ASKBOX_EXTRA_REPOS` para hermanos), la
     pantalla `/settings/git` y `POST /v1/settings/git/*` pueden gestionar remoto y push **de
     ese repo concreto**. Es conveniencia de operación, no el modelo mental obligatorio.

Implementar tu propia pantalla de settings git: ver `templates/git-settings-screen.md`.

## Capa C — Git del orquestador (kit)

Cuando mejoras **rules/skills** de este kit:

1. El cwd debe ser el repo **cursor-agent-orchestrator** (no el de askbox ni el de la app).
2. Configura remoto y `gh` (o `.askbox/git-settings.json` en el kit) para el **remoto del kit**
   en GitHub.
3. Commits de documentación/skills del kit → push al remoto del kit.

**Camino canónico del kit:** git-autopilot + `gh` (o config local en el repo del kit).

**Camino opcional:** montar el kit en `ASKBOX_EXTRA_REPOS` (p. ej. en el `docker-compose.yml`
de askbox: `cursor-agent-orchestrator=/repos/cursor-agent-orchestrator`) y usar
`/settings/git?repo=cursor-agent-orchestrator` o el CLI `git-setup --repo cursor-agent-orchestrator`
solo si ya operas git vía askbox. Sigue siendo el remoto **del kit**, no el de askbox.

## Cómo detecta el agente en qué capa B/C está

Antes de commit/push:

```bash
git rev-parse --show-toplevel
```

- Si el toplevel es el directorio del kit → capa **C**.
- Si es otro path → capa **B** para ese repo.

No asumir que «estamos en askbox» implica un solo remoto: askbox producto es B cuando el cwd es
askbox; el kit es C cuando el cwd es el kit.

## Relación con askbox producto

- `/settings/git` en askbox sirve a **repos montados** en ese despliegue (askbox + EXTRA_REPOS).
- `ASKBOX_ACCESS_TOKEN` (capa A) **no** autoriza git: es solo para `/v1/ask`.
- Tokens de API de usuario askbox para `/v1/settings/git/*` son otra credencial (sesión o token
  de usuario admin), distinta del token de proyecto ask-first.

## Referencias en el kit

| Archivo | Contenido |
|---------|-----------|
| `.cursor/skills/git-autopilot/SKILL.md` | Política commit/push; B y C |
| `.cursor/skills/askbox-first/SKILL.md` | Solo capa A |
| `templates/git-settings-screen.md` | Contrato si implementas pantalla en tu app |
| `.askbox/git-settings.json.example` | Plantilla de preferencias (sin secretos) |
