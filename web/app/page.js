"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "../lib/api";

export default function Home() {
  const [me, setMe] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeDiagram, setActiveDiagram] = useState("web");

  useEffect(() => {
    api("/v1/auth/me")
      .then((user) => {
        setMe(user);
        setLoading(false);
      })
      .catch(() => {
        setMe(null);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "3rem 1rem" }}>
        <div className="spinner" style={{ marginBottom: "1rem" }} />
        <p className="muted">Cargando panel de control askbox…</p>
      </div>
    );
  }

  return (
    <div>
      {/* Hero Welcome */}
      <div className="card" style={{ background: "linear-gradient(135deg, var(--bg-surface), var(--bg-surface-elevated))", borderLeft: "4px solid var(--petrol-500)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "1rem" }}>
          <div>
            <h1>Pasarela Unificada LLM</h1>
            <p style={{ maxWidth: "680px", fontSize: "1.02rem", color: "var(--text-secondary)" }}>
              askbox centraliza el acceso a modelos de inteligencia artificial (Gemini, OpenRouter y más), gestiona claves de proveedor cifradas en base de datos, enruta por niveles de capacidad (tiers) y acelera respuestas con caché semántica.
            </p>
          </div>
          {me ? (
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <Link href="/playground" className="btn btn-primary">
                Ir al Playground →
              </Link>
              <Link href="/projects" className="btn btn-secondary">
                Proyectos y Claves
              </Link>
            </div>
          ) : (
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <Link href="/login" className="btn btn-primary">
                Iniciar Sesión
              </Link>
              <Link href="/register" className="btn btn-secondary">
                Crear Cuenta
              </Link>
            </div>
          )}
        </div>
      </div>

      {/* Grid de Accesos Rápidos */}
      <div className="grid-2">
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">
              <span style={{ color: "var(--petrol-400)" }}>✦</span> Playground Interactivo
            </h3>
            <span className="badge badge-petrol">Web</span>
          </div>
          <p>
            Prueba prompts en vivo directamente desde el navegador. Ajusta el tier (<code>auto</code>, <code>fast</code>, <code>balanced</code>, <code>strong</code>), observa la latencia en milisegundos, el modelo seleccionado y si se utilizó caché.
          </p>
          <div style={{ marginTop: "1rem" }}>
            <Link href="/playground" className="btn btn-secondary btn-sm">
              Abrir Playground →
            </Link>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <h3 className="card-title">
              <span style={{ color: "var(--emerald-500)" }}>✦</span> Proyectos y Claves
            </h3>
            <span className="badge badge-emerald">Seguridad</span>
          </div>
          <p>
            Añade tus API keys de Gemini u OpenRouter. Se cifran con AES-GCM en el servidor y nunca se exponen al cliente. Genera tokens <code>ab_live_</code> para conectar Postman, cURL o tus aplicaciones.
          </p>
          <div style={{ marginTop: "1rem" }}>
            <Link href="/projects" className="btn btn-secondary btn-sm">
              Gestionar Proyectos →
            </Link>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <h3 className="card-title">
              <span style={{ color: "var(--amber-500)" }}>✦</span> Métricas de Consumo
            </h3>
            <span className="badge badge-amber">Telemetría</span>
          </div>
          <p>
            Revisa las peticiones diarias, aciertos de caché, volumen de tokens de entrada/salida y costes estimados en USD. Exporta el histórico completo en formato CSV.
          </p>
          <div style={{ marginTop: "1rem" }}>
            <Link href="/usage" className="btn btn-secondary btn-sm">
              Ver Gráficos de Uso →
            </Link>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <h3 className="card-title">
              <span style={{ color: "var(--accent-cyan)" }}>✦</span> Documentación API REST
            </h3>
            <span className="badge badge-slate">OpenAPI</span>
          </div>
          <p>
            Accede a la especificación interactiva Swagger UI en <code>/api/docs</code> o ReDoc en <code>/api/redoc</code>. Consulta esquemas de datos, códigos de error y prueba endpoints en tiempo real.
          </p>
          <div style={{ marginTop: "1rem", display: "flex", gap: "0.5rem" }}>
            <a href="/api/docs" target="_blank" rel="noopener noreferrer" className="btn btn-secondary btn-sm">
              Swagger UI ↗
            </a>
            <a href="/api/redoc" target="_blank" rel="noopener noreferrer" className="btn btn-secondary btn-sm">
              ReDoc ↗
            </a>
          </div>
        </div>
      </div>

      {/* Sección de Diagramas de Arquitectura y Flujos */}
      <div className="card">
        <div className="card-header">
          <h2 className="card-title">Flujos de Trabajo en askbox</h2>
          <div style={{ display: "flex", gap: "0.35rem" }}>
            <button
              type="button"
              className={`btn-sm ${activeDiagram === "web" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setActiveDiagram("web")}
            >
              1. Flujo Web (Configuración y Playground)
            </button>
            <button
              type="button"
              className={`btn-sm ${activeDiagram === "api" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setActiveDiagram("api")}
            >
              2. Flujo API Externa (Postman / cURL)
            </button>
          </div>
        </div>

        {activeDiagram === "web" && (
          <div>
            <p className="muted" style={{ marginBottom: "1rem" }}>
              <strong>Flujo 1: Configuración en el panel y uso en el Playground.</strong> Desde el registro del usuario hasta la ejecución del prompt en los modelos enrutados.
            </p>
            <div className="diagram-box" style={{ overflowX: "auto" }}>
              <svg viewBox="0 0 920 160" className="diagram-svg" style={{ minWidth: "720px" }}>
                <defs>
                  <marker id="arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                    <path d="M 0 1 L 8 5 L 0 9 z" fill="var(--petrol-500)" />
                  </marker>
                  <linearGradient id="boxGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="var(--bg-surface-elevated)" />
                    <stop offset="100%" stopColor="var(--bg-subtle)" />
                  </linearGradient>
                </defs>

                {/* Paso 1: Login */}
                <rect x="10" y="25" width="150" height="90" rx="8" fill="url(#boxGrad)" stroke="var(--border-subtle)" strokeWidth="1.5" />
                <text x="85" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">1. Login / Cuenta</text>
                <text x="85" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">Cookie HttpOnly</text>
                <text x="85" y="92" fill="var(--petrol-400)" fontSize="10" textAnchor="middle">Sesión Web segura</text>

                {/* Flecha 1 */}
                <line x1="160" y1="70" x2="195" y2="70" stroke="var(--petrol-500)" strokeWidth="2" markerEnd="url(#arrow)" />

                {/* Paso 2: Proyecto */}
                <rect x="200" y="25" width="150" height="90" rx="8" fill="url(#boxGrad)" stroke="var(--border-subtle)" strokeWidth="1.5" />
                <text x="275" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">2. Proyecto</text>
                <text x="275" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">Aislamiento y reglas</text>
                <text x="275" y="92" fill="var(--accent-cyan)" fontSize="10" textAnchor="middle">Tiers configurados</text>

                {/* Flecha 2 */}
                <line x1="350" y1="70" x2="385" y2="70" stroke="var(--petrol-500)" strokeWidth="2" markerEnd="url(#arrow)" />

                {/* Paso 3: Clave Proveedor */}
                <rect x="390" y="25" width="155" height="90" rx="8" fill="url(#boxGrad)" stroke="var(--border-subtle)" strokeWidth="1.5" />
                <text x="467" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">3. Clave Proveedor</text>
                <text x="467" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">Gemini / OpenRouter</text>
                <text x="467" y="92" fill="var(--emerald-500)" fontSize="10" textAnchor="middle">Cifrada AES-GCM en BD</text>

                {/* Flecha 3 */}
                <line x1="545" y1="70" x2="580" y2="70" stroke="var(--petrol-500)" strokeWidth="2" markerEnd="url(#arrow)" />

                {/* Paso 4: Router askbox */}
                <rect x="585" y="25" width="155" height="90" rx="8" fill="url(#boxGrad)" stroke="var(--border-focus)" strokeWidth="1.5" />
                <text x="662" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">4. Enrutador & Caché</text>
                <text x="662" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">Tier auto / fast / strong</text>
                <text x="662" y="92" fill="var(--amber-500)" fontSize="10" textAnchor="middle">Fallback automático</text>

                {/* Flecha 4 */}
                <line x1="740" y1="70" x2="775" y2="70" stroke="var(--petrol-500)" strokeWidth="2" markerEnd="url(#arrow)" />

                {/* Paso 5: Playground / Ask */}
                <rect x="780" y="25" width="130" height="90" rx="8" fill="url(#boxGrad)" stroke="var(--border-subtle)" strokeWidth="1.5" />
                <text x="845" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">5. Playground</text>
                <text x="845" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">Respuesta & texto</text>
                <text x="845" y="92" fill="var(--petrol-400)" fontSize="10" textAnchor="middle">Latencia y telemetría</text>
              </svg>
            </div>
          </div>
        )}

        {activeDiagram === "api" && (
          <div>
            <p className="muted" style={{ marginBottom: "1rem" }}>
              <strong>Flujo 2: Integración externa con token Bearer <code>ab_live_</code>.</strong> Ideal para Postman, scripts de Node/Python, GitHub Actions o tus backends.
            </p>
            <div className="diagram-box" style={{ overflowX: "auto" }}>
              <svg viewBox="0 0 920 160" className="diagram-svg" style={{ minWidth: "720px" }}>
                <defs>
                  <marker id="arrow2" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                    <path d="M 0 1 L 8 5 L 0 9 z" fill="var(--petrol-500)" />
                  </marker>
                  <linearGradient id="boxGrad2" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="var(--bg-surface-elevated)" />
                    <stop offset="100%" stopColor="var(--bg-subtle)" />
                  </linearGradient>
                </defs>

                {/* Paso 1: Generar Token */}
                <rect x="10" y="25" width="155" height="90" rx="8" fill="url(#boxGrad2)" stroke="var(--border-subtle)" strokeWidth="1.5" />
                <text x="87" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">1. Token ab_live_</text>
                <text x="87" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">Creado en Proyectos</text>
                <text x="87" y="92" fill="var(--emerald-500)" fontSize="10" textAnchor="middle">Hash SHA-256 en BD</text>

                {/* Flecha 1 */}
                <line x1="165" y1="70" x2="200" y2="70" stroke="var(--petrol-500)" strokeWidth="2" markerEnd="url(#arrow2)" />

                {/* Paso 2: Postman / App */}
                <rect x="205" y="25" width="155" height="90" rx="8" fill="url(#boxGrad2)" stroke="var(--border-subtle)" strokeWidth="1.5" />
                <text x="282" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">2. Cliente Externo</text>
                <text x="282" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">Postman, cURL, Python</text>
                <text x="282" y="92" fill="var(--petrol-400)" fontSize="10" textAnchor="middle">Authorization: Bearer</text>

                {/* Flecha 2 */}
                <line x1="360" y1="70" x2="395" y2="70" stroke="var(--petrol-500)" strokeWidth="2" markerEnd="url(#arrow2)" />

                {/* Paso 3: Endpoint REST */}
                <rect x="400" y="25" width="155" height="90" rx="8" fill="url(#boxGrad2)" stroke="var(--border-subtle)" strokeWidth="1.5" />
                <text x="477" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">3. POST /v1/ask</text>
                <text x="477" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">Proxy Next / FastAPI</text>
                <text x="477" y="92" fill="var(--accent-cyan)" fontSize="10" textAnchor="middle">JSON payload & tier</text>

                {/* Flecha 3 */}
                <line x1="555" y1="70" x2="590" y2="70" stroke="var(--petrol-500)" strokeWidth="2" markerEnd="url(#arrow2)" />

                {/* Paso 4: Gateway LLM */}
                <rect x="595" y="25" width="155" height="90" rx="8" fill="url(#boxGrad2)" stroke="var(--border-focus)" strokeWidth="1.5" />
                <text x="672" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">4. Gateway LLM</text>
                <text x="672" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">Keypool & Redis Cache</text>
                <text x="672" y="92" fill="var(--amber-500)" fontSize="10" textAnchor="middle">Llamada a proveedores</text>

                {/* Flecha 4 */}
                <line x1="750" y1="70" x2="785" y2="70" stroke="var(--petrol-500)" strokeWidth="2" markerEnd="url(#arrow2)" />

                {/* Paso 5: JSON Response */}
                <rect x="790" y="25" width="120" height="90" rx="8" fill="url(#boxGrad2)" stroke="var(--border-subtle)" strokeWidth="1.5" />
                <text x="850" y="55" fill="var(--text-primary)" fontSize="13" fontWeight="600" textAnchor="middle">5. JSON Output</text>
                <text x="850" y="75" fill="var(--text-muted)" fontSize="11" textAnchor="middle">text, latency_ms</text>
                <text x="850" y="92" fill="var(--emerald-500)" fontSize="10" textAnchor="middle">model_used, tier</text>
              </svg>
            </div>
          </div>
        )}
      </div>

      {/* Guía de Conceptos Clave en Español */}
      <div className="card">
        <h2 style={{ marginBottom: "1rem" }}>Conceptos Clave de askbox</h2>

        <div className="grid-2">
          <div style={{ background: "var(--bg-subtle)", padding: "1rem", borderRadius: "8px", border: "1px solid var(--border-subtle)" }}>
            <h4 style={{ color: "var(--petrol-400)", display: "flex", alignItems: "center", gap: "0.4rem" }}>
              <span>⚡</span> ¿Qué son los Tiers?
            </h4>
            <p style={{ fontSize: "0.88rem" }}>
              En lugar de acoplar tu código a un modelo concreto (que puede cambiar de precio o quedar obsoleto), defines un nivel de capacidad:
            </p>
            <ul style={{ paddingLeft: "1.2rem", fontSize: "0.85rem", color: "var(--text-secondary)" }}>
              <li><strong>auto:</strong> Elige dinámicamente el modelo más eficiente según la longitud y requisitos del prompt.</li>
              <li><strong>fast:</strong> Prioriza velocidad extrema y menor coste (ideal para clasificación, tagging o extracción).</li>
              <li><strong>balanced:</strong> Equilibrio óptimo entre razonamiento y velocidad para tareas cotidianas.</li>
              <li><strong>strong:</strong> Modelos avanzados con alta capacidad de razonamiento para análisis complejos.</li>
            </ul>
          </div>

          <div style={{ background: "var(--bg-subtle)", padding: "1rem", borderRadius: "8px", border: "1px solid var(--border-subtle)" }}>
            <h4 style={{ color: "var(--emerald-500)", display: "flex", alignItems: "center", gap: "0.4rem" }}>
              <span>🔑</span> Tokens ab_live_ (API Bearer)
            </h4>
            <p style={{ fontSize: "0.88rem" }}>
              Son credenciales generadas en askbox para autenticar peticiones HTTP externas. Se muestran <strong>solo una vez</strong> al crearse por seguridad.
            </p>
            <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
              En Postman o cURL se envían en la cabecera:
              <br />
              <code>Authorization: Bearer ab_live_...</code>
            </p>
            <p style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>
              Soportan scopes específicos (<code>ask:read</code>, <code>usage:read</code>, <code>tokens:write</code>, etc.) para limitar los permisos de cada integración.
            </p>
          </div>

          <div style={{ background: "var(--bg-subtle)", padding: "1rem", borderRadius: "8px", border: "1px solid var(--border-subtle)" }}>
            <h4 style={{ color: "var(--accent-cyan)", display: "flex", alignItems: "center", gap: "0.4rem" }}>
              <span>📝</span> Plantillas con Variables
            </h4>
            <p style={{ fontSize: "0.88rem" }}>
              Guarda tus prompts versionados en el servidor utilizando la sintaxis de variables <code>{`{{variable}}`}</code>.
            </p>
            <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
              Tus aplicaciones pueden invocar la plantilla pasando solo un diccionario de variables, evitando replicar prompts largos en el cliente y facilitando cambios de copy sin re-desplegar código.
            </p>
          </div>

          <div style={{ background: "var(--bg-subtle)", padding: "1rem", borderRadius: "8px", border: "1px solid var(--border-subtle)" }}>
            <h4 style={{ color: "var(--amber-500)", display: "flex", alignItems: "center", gap: "0.4rem" }}>
              <span>🔄</span> Playground vs Postman / cURL
            </h4>
            <p style={{ fontSize: "0.88rem" }}>
              <strong>Playground Web:</strong> Entorno gráfico interactivo para que pruebes prompts, verifiques respuestas y valides latencias usando tu sesión de usuario.
            </p>
            <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
              <strong>Postman / cURL:</strong> Integración técnica para producción mediante el endpoint <code>POST /v1/ask</code> con un token <code>ab_live_</code>. En la sección inferior del Playground encontrarás ejemplos cURL listos para copiar.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
