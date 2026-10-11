---
name: plan-implement-verify
description: Flujo Multitask con padre en Auto — decide plan vs. directo, Task planificador (sin OK del usuario) → implement → verify. Niveles de capacidad; slugs resueltos en sesión. Portable a cualquier repo.
---

# Plan → Implement → Verify (padre Auto + Multitask)

Plantilla reutilizable: copia este skill a `.cursor/skills/` en tu proyecto y enlázalo desde `auto-orchestrator.mdc`. Sustituye los ejemplos de "Fase 3 — Verify" y las reglas de dominio por las de **tu** repositorio (crea tu propia rule de convenciones, ver `templates/project-conventions.mdc.template` en este kit).

## Objetivo

Clasificar cada tarea en vía **plan** o vía **directa** antes de tocar código; cerrar diseño con un **Task planificador** cuando corresponda; delegar implementación; verificar con build/tests. El **padre en Auto** orquesta todo **sin** pedir aprobación del plan al usuario.

**Modelos:** el usuario **no** elige modelo ni especialidad. El padre asigna **tipo de tarea → nivel → slug** consultando `available_subagent_models` (política completa en `auto-orchestrator.mdc` § «Elección de modelo»). **Ahorro:** Composer por defecto en `fast`/`balanced`; especialidades (UI, escritura) solo si el tipo lo justifica y el slug está en catálogo; escalar a `strong`/`strong-thinking` o proveedores caros solo cuando la complejidad lo exige.

## Flujo por defecto

0. **Decidir** — el padre clasifica la tarea en vía **plan** o vía **directa** (ver Fase 0).
1. **Plan** (solo vía plan) — subagente `Task` planificador **antes** de escribir código de producto.
1b. **Directo** (solo vía directa) — sin Task planificador dedicado; plan en cabeza y a implementar.
2. **Implement** — uno o más `Task` con ownership disjunto; paralelo si no hay dependencia.
3. **Verify** — build, health, tests, checks de secretos/convenciones según lo tocado.

Al terminar el plan: el padre **continúa solo** a implementación; no hay paso de "espera OK" ni revisión humana obligatoria del plan.

## Niveles de capacidad (referencia)

| Nivel | Uso |
|-------|-----|
| **`fast`** | Explore, búsquedas, checks puntuales, status |
| **`balanced`** | Implementación mecánica, migraciones, boilerplate, tests; fallback UI/docs |
| **`strong`** | Plan feature media, diseño API/DB/auth |
| **`strong-thinking`** | Plan greenfield/alta complejidad, seguridad, multi-tenant, integraciones externas, review de alto riesgo |

## Fase 0 — Decidir: plan o directo

El padre clasifica la tarea **antes** de tocar código (criterio completo en `auto-orchestrator.mdc` § "Plan vs. directo"):

- **Vía plan** → greenfield, varias capas (API+BD+auth+UI), arquitectura/seguridad/multi-tenant/integraciones externas, alcance ambiguo o >~1 día, contratos públicos o migraciones no triviales.
- **Vía directa** → bugfix acotado, spec ya cerrada con paths claros, un solo módulo sin diseño abierto, o follow-up mecánico de un plan ya ejecutado en la conversación.
- **Regla dura:** si hay duda, **planificar** (rehacer sale más caro que un plan corto).
- El padre **anuncia** la decisión (`plan` | `directo`) y, por cada subagente, **tipo de tarea + nivel + slug** (y excepción fuera de Composer si aplica). **No** pedir opinión al usuario sobre modelos.

## Fase 1 — Plan (Task planificador, no el padre entero)

**Solo si la Fase 0 eligió la vía plan.** El padre lanza un **único Task planificador** (o explore + planificador en serie si hace falta contexto) con:

- Investigación del código existente y restricciones del repo (convenciones, módulos sensibles, contratos ya publicados).
- Entregable: alcance, archivos a tocar, contratos (API, esquema), migraciones, riesgos, tareas **independientes** para implementadores.
- **Sin código de producto** en esta fase salvo borradores (SQL, OpenAPI) si acotan el diseño.

### Complejidad → nivel del planificador (solo el padre elige)

| Complejidad | Ejemplos | Nivel |
|-------------|----------|-------|
| **Baja** | CRUD simple, stub, documentación | **`balanced`** (o **`fast`** si trivial y plan mínimo) |
| **Media** | API + DB + auth, coordinación multi-archivo | **`strong`** |
| **Alta** | Multi-tenant, integraciones externas, seguridad, varios servicios | **`strong-thinking`** |

**Señales de complejidad:** número de servicios/capas afectados; auth y permisos; cifrado y secretos; multi-tenant; integraciones externas; greenfield frente a cambio puntual.

Tras elegir el nivel, el padre **resuelve el slug** contra `available_subagent_models` y **informa**: tipo de tarea (planificador), nivel, slug (+ excepción si no es Composer). Igual para cada Task posterior.

## Fase 1b — Directo (sin Task planificador dedicado)

**Solo si la Fase 0 eligió la vía directa.** No hay Task planificador separado:

- El padre arma el plan mentalmente (unas líneas, sin entregable formal) usando el contexto ya disponible en la conversación.
- Para cambios realmente triviales y de un solo archivo, el padre puede **implementar él mismo** sin lanzar Task.
- Para el resto, el padre lanza un **único Task implement** (nivel `balanced`; `strong` si el cambio, aun acotado, exige coordinar varios archivos con cuidado) con la spec ya cerrada: archivo(s), cambio esperado, cómo probar.
- Se salta el entregable de diseño de la Fase 1, pero **no** se salta Verify (Fase 3).
- Si durante la implementación directa aparece ambigüedad de diseño no prevista, el padre **escala a la vía plan** (Fase 1) en vez de improvisar arquitectura sobre la marcha.

## Fase 2 — Implement (subagentes)

- Un subagente por tarea acotada; prompt con rutas absolutas bajo la raíz del repo.
- Incluir las reglas locales de **tu** proyecto (convenciones de arquitectura, dónde van las llamadas a servicios externos, dónde no hay que poner secretos, etc. — ver tu propia rule de convenciones).
- **Paralelo** solo con allowlists **disjuntas**; no un solo Task "haz todo el repo".
- Entregable del subagente: diff resumido, rutas, cómo probar — no volcado masivo.

### Tipo de trabajo → nivel y subagente

El padre elige **subagente** (`explore`, `generalPurpose`, `bugbot`, etc.) y **nivel** según el tipo; luego **slug** en catálogo (`auto-orchestrator.mdc`):

| Tipo de tarea | Nivel | Subagente |
|---------------|-------|-----------|
| Implementación según plan | **`balanced`** | `generalPurpose` |
| Tests, migraciones, boilerplate | **`balanced`** | `generalPurpose` |
| UI, maquetación, frontend visual | visual en catálogo si existe; si no **`balanced`** | `generalPurpose` |
| Docs, redacción, changelogs | escritura en catálogo si existe; si no **`balanced`** | `generalPurpose` |
| Búsqueda / mapa de estructura | **`fast`** | `explore` |
| Lecturas largas / muchos ficheros | **`fast`** | `explore` |
| Revisión crítica (usuario pide review) | **`strong-thinking`** | `bugbot` / `security-review` |
| Verificación rápida de comandos | **`fast`** | `generalPurpose` |

Al lanzar cada Task: anunciar **tipo + nivel + slug**; excepción Composer solo si se usó otro proveedor por especialidad o complejidad.

### Ownership y paralelismo

- Siempre `.cursor/rules/multitask-ownership.mdc` (`alwaysApply`).
- Cada Task: **allowlist** de escritura y **denylist**; sin solapar paths entre Tasks paralelos.
- Mismo archivo o módulo → serie o un agente; ficheros "calientes" (compose, `.env*`, entrypoints) → un solo agente.
- Tras paralelo: el padre comprueba solapes antes de verify.

El padre **delega** implementación; evita editar el mismo árbol que un Task activo salvo integración mínima.

## Fase 3 — Verify (obligatorio, adaptar a tu proyecto)

Desde la raíz del repositorio, ejecutar lo que aplique:

1. Build del proyecto (`docker compose build`, `npm run build`, `make build`, etc. — lo que use tu repo).
2. Health check de los servicios afectados (endpoint de salud, smoke test, etc.).
3. Tests (`pytest`, `npm test`, etc.) si existen.
4. Scripts propios del proyecto (linters de convenciones, checks de secretos hardcodeados, etc. — define los tuyos en tu rule de convenciones).
5. Revisión contra tu rule de secretos (si tu proyecto maneja claves/API keys, considera una rule tipo `no-secret-leak.mdc`).

**Esta fase es un placeholder**: cada proyecto debe rellenar sus comandos reales en su propia rule de convenciones o en este mismo archivo tras instalarlo.

Si algo falla, corregir (delegar o padre) antes de dar por cerrado.

## Fase 4 — Git autopilot (si `git-autopilot` está instalado)

Tras Verify en verde, si `.cursor/skills/git-autopilot/SKILL.md` está presente en el repo, el
padre **decide y ejecuta** commit + push sin pedir OK al usuario (ver esa skill para la
heurística completa de cuándo sí/no commitear y cómo publicar con `gh` si falta remoto). No
confundir con la Fase 3: primero build/tests/health en verde, **luego** commit/push.

## Hábitos que ahorran tokens

- Los subagentes **no** ven el chat del usuario: repetir solo contexto necesario.
- Investigar/planificar una vez; pasar **resumen** a Tasks de implementación.
- Varios `Task` en un turno cuando las tareas sean independientes y las allowlists no se solapen.

## Comunicación

- Resumen final en el idioma acordado con el usuario: qué se hizo, decisión (`plan`|`directo`), por agente **tipo de tarea + nivel + slug** (y excepción Composer si hubo), cómo probar, pendientes.
- **No** solicitar aprobación del plan intermedio ni **consultar** qué modelo prefiere el usuario.
