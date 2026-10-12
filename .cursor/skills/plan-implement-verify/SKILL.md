---
name: plan-implement-verify
description: Flujo Multitask con padre en Auto — decide plan vs. directo, Task planificador (sin OK del usuario) → implement → verify. Niveles de capacidad; slugs resueltos en sesión. Portable a cualquier repo.
---

# Plan → Implement → Verify (padre Auto + Multitask)

Plantilla reutilizable: copia este skill a `.cursor/skills/` en tu proyecto y enlázalo desde `auto-orchestrator.mdc`. Sustituye los ejemplos de "Fase 3 — Verify" y las reglas de dominio por las de **tu** repositorio (crea tu propia rule de convenciones, ver `templates/project-conventions.mdc.template` en este kit).

## Objetivo

Clasificar cada tarea en vía **plan** o vía **directa** antes de tocar código; cerrar diseño con el **modo de planificación más barato** que baste; delegar implementación en Tasks **`balanced`** pequeños; verificar con build/tests. El **padre en Auto** orquesta todo **sin** pedir aprobación del plan al usuario.

**Modelos y coste:** el padre y cada `Task` consumen plan Cursor. Ver `auto-orchestrator.mdc` § «Presupuesto de consumo». Resumen:

- **UI / copy / docs / qa / draft** → `askbox-first` si está instalado (padre puede llamar askbox sin Task); sin `ASKBOX_*`, fallback **Composer `fast`/`balanced`**, no especialidades caras por defecto.
- **Plan** → modo A (cabeza) > B (Task medio/Grok) > C (Opus/thinking justificado).
- **Implement / verify** → **`balanced`** o **`fast`** (Composer/Grok); **nunca** Opus para codificar o correr tests.
- Elegir **slug explícito** en cada Task; evitar `inherit` ciego.

## Flujo por defecto

0. **Decidir** — plan vs. directo y modo de plan **A | B | C** (ver Fase 0).
1. **Plan** (solo vía plan, modos B o C) — Task planificador **antes** de código de producto; entregable = **lista de tareas pequeñas** para `balanced`.
1b. **Directo** (modo A) — plan en cabeza; implement sin planificador dedicado.
2. **Implement** — uno o más `Task` **`balanced`** con ownership disjunto; paralelo si no hay dependencia.
3. **Verify** — padre o Task **`fast`**; build, health, tests.

Al terminar el plan: el padre **continúa solo** a implementación; no hay paso de "espera OK".

## Niveles de capacidad (referencia)

| Nivel | Uso |
|-------|-----|
| **`fast`** | Explore, búsquedas, checks, verify ligero |
| **`balanced`** | Implementación, migraciones, tests; planificador modo B |
| **`strong`** | Plan modo B si hace falta más capacidad que `balanced`; resolver a slug **medio** (Grok), no Opus |
| **`strong-thinking`** | Plan modo C, Bugbot/security-review explícito |

## Fase 0 — Decidir: plan o directo

Criterio completo en `auto-orchestrator.mdc` § «Plan vs. directo» y «Planificación: tres modos»:

- **Directo / modo A** → bugfix, spec cerrada, un módulo, follow-up, o diseño obvio tras lectura rápida.
- **Plan modo B** → varias capas, contrato/migración acotada, coordinación multi-archivo **sin** señales de complejidad alta.
- **Plan modo C** → multi-tenant, seguridad crítica, routing LLM, greenfield grande, varios servicios.
- **Regla dura:** duda de diseño → **explore `fast`** + plan en cabeza (A); **no** abrir planificador Opus por precaución.
- **Anunciar:** `plan`|`directo`, modo **A|B|C** si plan, **vía**, **tipo**, **nivel + slug + por qué no Opus** en cada Task Cursor.

## Fase 1 — Plan (Task planificador, modos B y C)

**Solo si la Fase 0 exige Task planificador** (no modo A).

- Opcional previo: **`explore` `fast`** (padre resume al planificador; no duplicar explore caro).
- Entregable: alcance, archivos, contratos, migraciones, riesgos, **tareas independientes acotadas** (cada una implementable en `balanced`).
- **Sin código de producto** salvo borradores (SQL, OpenAPI) que acoten diseño.

### Complejidad → modo y nivel del planificador

| Complejidad | Ejemplos | Modo | Nivel + slug |
|-------------|----------|------|----------------|
| **Baja** | CRUD, stub, docs | Preferir **A** (sin Task) | Si Task: **`balanced`** → Composer/Grok |
| **Media** | API + DB + auth, multi-archivo | **B** | **`balanced`** o **`strong`** → **Grok** / medio; anunciar por qué no Opus |
| **Alta** | Multi-tenant, LLM routing, seguridad | **C** | **`strong-thinking`** → Opus/thinking + justificación de una línea |

Tras elegir nivel, **slug explícito** en `available_subagent_models`. **Prohibido** Opus para plan «media» estándar.

## Fase 1b — Directo (modo A)

- Plan mental breve; implement **`balanced`** o padre si trivial y un archivo.
- Task implement único con spec cerrada; nivel **`balanced`** (subir a `strong`+medio solo si varios archivos delicados, **sin** Opus).
- Si aparece ambigüedad de arquitectura → escalar a plan modo B, no improvisar.

## Fase 2 — Implement (subagentes)

- **Un Task por tarea acotada** del plan; prompts con rutas absolutas y allowlist.
- Nivel por defecto: **`balanced`** + Composer o Grok.
- **Paralelo** solo con allowlists disjuntas.
- Entregable: diff resumido, rutas, cómo probar.

### Tipo de trabajo → nivel y subagente

| Tipo de tarea | Nivel | Subagente |
|---------------|-------|-----------|
| Implementación según plan | **`balanced`** | `generalPurpose` |
| Tests, migraciones, boilerplate | **`balanced`** | `generalPurpose` |
| UI, maquetación | **askbox-first**; fallback Composer **`balanced`** | `generalPurpose` |
| Docs, copy, qa, draft | **askbox-first**; fallback Composer **`fast`/`balanced`** | padre o `generalPurpose` |
| Búsqueda / mapa | **`fast`** | `explore` |
| Revisión crítica (usuario pide) | **`strong-thinking`** | `bugbot` / `security-review` |
| Verify rápido de comandos | **`fast`** o padre | — |

Anunciar en cada Task: **Cursor — tipo — nivel — slug — por qué no Opus**.

### Ownership y paralelismo

- Siempre `.cursor/rules/multitask-ownership.mdc`.
- Tras paralelo: comprobar solapes antes de verify.
- El padre delega; no editar el mismo árbol que un Task activo salvo integración mínima.

## Fase 3 — Verify (obligatorio, adaptar a tu proyecto)

Desde la raíz del repositorio, ejecutar lo que aplique:

1. Build del proyecto (`docker compose build`, `npm run build`, `make build`, etc. — lo que use tu repo).
2. Health check de los servicios afectados (endpoint de salud, smoke test, etc.).
3. Tests (`pytest`, `npm test`, etc.) si existen.
4. Scripts propios del proyecto (linters de convenciones, checks de secretos hardcodeados, etc. — define los tuyos en tu rule de convenciones).
5. Revisión contra tu rule de secretos (si tu proyecto maneja claves/API keys, considera una rule tipo `no-secret-leak.mdc`).

**Esta fase es un placeholder**: cada proyecto debe rellenar sus comandos reales en su propia rule de convenciones o en este mismo archivo tras instalarlo.

Verify lo ejecuta el **padre** o un Task **`fast`**; no lanzar Opus para «ver si compila». Si algo falla, corregir con Task **`balanced`** o padre antes de cerrar.

## Fase 4 — Git autopilot (si `git-autopilot` está instalado)

Tras Verify en verde, si `.cursor/skills/git-autopilot/SKILL.md` está presente en el repo, el
padre **decide y ejecuta** commit + push sin pedir OK al usuario (ver esa skill para la
heurística completa de cuándo sí/no commitear y cómo publicar con `gh` si falta remoto). No
confundir con la Fase 3: primero build/tests/health en verde, **luego** commit/push.

## Hábitos que ahorran tokens

- Los subagentes **no** ven el chat del usuario: repetir solo contexto necesario.
- Investigar/planificar **una vez** (`explore` fast); pasar **resumen** a Tasks `balanced`.
- **Partir** el plan en N Tasks pequeños en lugar de un «implementa todo» en `strong`.
- Varios `Task` en un turno cuando sean independientes y las allowlists no se solapen.
- UI, textos, Q&A y borradores: **askbox** antes que Task Cursor de pago.

## Comunicación

- Resumen final: qué se hizo, `plan`|`directo`, modo **A|B|C**, bloques **askbox** vs **Cursor** (tipo + nivel + slug + por qué no Opus), cómo probar, pendientes.
- **No** pedir aprobación del plan ni consultar modelo al usuario.
