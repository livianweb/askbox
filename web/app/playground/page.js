"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "../../lib/api";

const PRESETS = [
  {
    name: "Resumen conciso",
    tier: "fast",
    maxTokens: 300,
    prompt: "Resume en 3 puntos clave y directos las ventajas de utilizar una pasarela unificada de modelos LLM con caché semántica y enrutamiento por tiers.",
  },
  {
    name: "Extracción JSON",
    tier: "fast",
    maxTokens: 400,
    prompt: "Extrae de este texto los datos en JSON válido con las claves 'empresa', 'servicio', 'latencia_ms' y 'estado':\n\n'La plataforma askbox procesó la consulta del cliente en 140ms devolviendo un estado satisfactorio sin errores.'",
  },
  {
    name: "Explicación técnica",
    tier: "balanced",
    maxTokens: 600,
    prompt: "Explica brevemente la diferencia entre autenticar una API con cookies de sesión HttpOnly vs tokens Bearer (como ab_live_), indicando cuándo conviene cada uno.",
  },
  {
    name: "Lógica y razonamiento",
    tier: "strong",
    maxTokens: 800,
    prompt: "Analiza qué consideraciones de arquitectura y tolerancia a fallos son necesarias para implementar un keypool de proveedores LLM con reintentos transparentes y rotación automática.",
  },
];

const TIER_DESCRIPTIONS = {
  auto: "Enrutamiento inteligente automático según la complejidad y tamaño del prompt para optimizar coste y latencia.",
  fast: "Mínima latencia y menor coste. Recomendado para clasificación, extracción estructurada y respuestas rápidas.",
  balanced: "Equilibrio ideal entre capacidad de razonamiento, velocidad y coste. El valor recomendado para uso general.",
  strong: "Modelos avanzados con máxima capacidad de razonamiento y amplio contexto para tareas complejas o análisis.",
};

export default function Playground() {
  const router = useRouter();
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState("");
  const [tier, setTier] = useState("auto");
  const [prompt, setPrompt] = useState("");
  const [maxTokens, setMaxTokens] = useState(512);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [curlCopied, setCurlCopied] = useState(false);
  const [attachmentFiles, setAttachmentFiles] = useState([]);
  const [taskMode, setTaskMode] = useState("");

  useEffect(() => {
    api("/v1/projects")
      .then((p) => {
        setProjects(p);
        if (p[0]) setProjectId(p[0].id);
      })
      .catch((e) => (e.status === 401 ? router.replace("/login") : setError(e.message)));
  }, [router]);

  function applyPreset(p) {
    setPrompt(p.prompt);
    setTier(p.tier);
    setMaxTokens(p.maxTokens);
    setError("");
  }

  function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = reader.result;
        const comma = String(dataUrl).indexOf(",");
        resolve({
          mime: file.type,
          data_base64: comma >= 0 ? String(dataUrl).slice(comma + 1) : "",
          filename: file.name,
        });
      };
      reader.onerror = () => reject(new Error(`No se pudo leer ${file.name}`));
      reader.readAsDataURL(file);
    });
  }

  async function send(e) {
    e.preventDefault();
    if (!prompt.trim()) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      let attachmentsPayload;
      if (attachmentFiles.length > 0) {
        attachmentsPayload = await Promise.all(attachmentFiles.map(readFileAsBase64));
      }
      const body = {
        project_id: projectId || undefined,
        tier,
        prompt,
        max_output_tokens: Number(maxTokens),
      };
      if (attachmentsPayload?.length) {
        body.attachments = attachmentsPayload;
        if (taskMode) body.task = taskMode;
      }
      const res = await api("/v1/ask", { method: "POST", body });
      setResult(res);
    } catch (err) {
      setError(`${err.status || ""} ${err.code || ""}: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  function copyResponse() {
    if (!result?.text) return;
    navigator.clipboard.writeText(result.text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  const curlSnippet = `curl -X POST "${typeof window !== "undefined" ? window.location.origin : "https://askbox.softportweb.com"}/api/v1/ask" \\
  -H "Authorization: Bearer ab_live_TU_TOKEN_AQUI" \\
  -H "Content-Type: application/json" \\
  -d '{
    "project_id": "${projectId || "TU_PROJECT_ID"}",
    "tier": "${tier}",
    "prompt": ${JSON.stringify(prompt || "Escribe tu consulta aquí")},
    "max_output_tokens": ${maxTokens}
  }'`;

  function copyCurl() {
    navigator.clipboard.writeText(curlSnippet).then(() => {
      setCurlCopied(true);
      setTimeout(() => setCurlCopied(false), 2000);
    });
  }

  return (
    <div>
      {/* Cabecera del Playground */}
      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.75rem" }}>
          <div>
            <h1>Playground Interactivo</h1>
            <p style={{ margin: 0 }}>
              Prueba consultas en vivo a través del router de askbox. Explora respuestas, latencias y fallbacks en tiempo real.
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <a href="/api/docs" target="_blank" rel="noopener noreferrer" className="btn btn-secondary btn-sm">
              Swagger Docs ↗
            </a>
            <Link href="/projects" className="btn btn-secondary btn-sm">
              Claves y Tokens
            </Link>
          </div>
        </div>
      </div>

      {projects.length === 0 && !error && (
        <div className="card" style={{ borderLeft: "4px solid var(--amber-500)" }}>
          <p>
            <strong>Aún no tienes ningún proyecto creado.</strong> Para consultar modelos necesitas crear un proyecto y registrar al menos una clave de proveedor (Gemini u OpenRouter).
          </p>
          <Link href="/projects" className="btn btn-primary btn-sm">
            Crear mi primer proyecto →
          </Link>
        </div>
      )}

      {/* Ejemplos Rápidos */}
      <div style={{ marginBottom: "0.75rem" }}>
        <small className="muted" style={{ fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em" }}>
          Ejemplos rápidos para probar:
        </small>
        <div className="preset-pills-wrap">
          {PRESETS.map((p, i) => (
            <button
              key={i}
              type="button"
              className="preset-pill"
              onClick={() => applyPreset(p)}
            >
              ✦ {p.name} ({p.tier})
            </button>
          ))}
        </div>
      </div>

      {/* Formulario y Zona de Respuesta en Cuadrícula 2 Columnas */}
      <div className="grid-2" style={{ alignItems: "stretch", marginBottom: "1.5rem" }}>
        {/* Columna Izquierda: Parámetros y Prompt */}
        <form className="card" onSubmit={send} style={{ display: "flex", flexDirection: "column", height: "100%", margin: 0 }}>
          <div className="card-header">
            <h3 className="card-title">Parámetros de Consulta</h3>
            <span className="badge badge-petrol">Petición</span>
          </div>

          <label htmlFor="select-project">Proyecto</label>
          <div className="field-desc">Define el proyecto, claves de proveedor asociadas y registro de métricas.</div>
          <select
            id="select-project"
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            disabled={projects.length === 0}
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.default_tier || "auto"})
              </option>
            ))}
          </select>

          <div className="row" style={{ marginTop: "0.5rem" }}>
            <div>
              <label htmlFor="select-tier">Nivel de Capacidad (Tier)</label>
              <div className="field-desc">Estrategia de enrutamiento del modelo.</div>
              <select
                id="select-tier"
                value={tier}
                onChange={(e) => setTier(e.target.value)}
              >
                <option value="auto">auto — Inteligente según prompt</option>
                <option value="fast">fast — Rápido y económico</option>
                <option value="balanced">balanced — Equilibrio óptimo</option>
                <option value="strong">strong — Máximo razonamiento</option>
              </select>
            </div>

            <div>
              <label htmlFor="input-attachments">Adjuntos (imagen o PDF)</label>
              <div className="field-desc">Opcional. Se envían en base64 inline; no se guardan en el servidor.</div>
              <input
                id="input-attachments"
                type="file"
                accept="image/png,image/jpeg,image/webp,application/pdf,image/*"
                multiple
                onChange={(e) => setAttachmentFiles(Array.from(e.target.files || []))}
              />
              {attachmentFiles.length > 0 && (
                <small className="muted" style={{ display: "block", marginTop: "0.25rem" }}>
                  {attachmentFiles.length} archivo(s): {attachmentFiles.map((f) => f.name).join(", ")}
                </small>
              )}
            </div>

            {attachmentFiles.length > 0 && (
              <div>
                <label htmlFor="select-task">Tarea con adjuntos</label>
                <div className="field-desc">Si no eliges, askbox infiere transcribe / extract / interpret.</div>
                <select id="select-task" value={taskMode} onChange={(e) => setTaskMode(e.target.value)}>
                  <option value="">Inferir automáticamente</option>
                  <option value="transcribe">transcribe — texto literal</option>
                  <option value="extract">extract — datos estructurados</option>
                  <option value="interpret">interpret — descripción o análisis</option>
                </select>
              </div>
            )}

            <div>
              <label htmlFor="input-maxtokens">Max Tokens: {maxTokens}</label>
              <div className="field-desc">Longitud máxima de la respuesta (1 token ≈ 4 caracteres).</div>
              <input
                id="input-maxtokens"
                type="number"
                min={1}
                max={8192}
                value={maxTokens}
                onChange={(e) => setMaxTokens(e.target.value)}
              />
            </div>
          </div>

          {/* Hint contextual de Tier */}
          <div className="info-box" style={{ margin: "0.5rem 0 0.75rem", padding: "0.6rem 0.8rem", fontSize: "0.82rem" }}>
            <strong>Tier {tier}:</strong> {TIER_DESCRIPTIONS[tier] || ""}
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.5rem" }}>
            <label htmlFor="textarea-prompt" style={{ margin: 0 }}>
              Prompt
            </label>
            <span className="muted" style={{ fontSize: "0.78rem" }}>
              {prompt.length} / 20.000 caracteres
            </span>
          </div>
          <div className="field-desc">Escribe la instrucción o contenido que procesará el modelo.</div>
          <textarea
            id="textarea-prompt"
            rows={7}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Escribe tu consulta o selecciona uno de los ejemplos rápidos superiores..."
            required
            maxLength={20000}
            style={{ flex: 1, minHeight: "140px" }}
          />

          <div style={{ marginTop: "0.75rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={busy || !projectId || !prompt.trim()}
              style={{ flex: 1 }}
            >
              {busy ? (
                <>
                  <span className="spinner" />
                  <span>Consultando modelo…</span>
                </>
              ) : (
                "Enviar consulta →"
              )}
            </button>
            {prompt && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setPrompt("")}
                disabled={busy}
              >
                Limpiar
              </button>
            )}
          </div>

          {error && <div className="err" style={{ marginTop: "0.75rem" }}>{error}</div>}
        </form>

        {/* Columna Derecha: Zona de Respuesta Visible y Telemetría */}
        <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%", margin: 0 }}>
          <div className="card-header">
            <h3 className="card-title">Respuesta del Modelo</h3>
            {result?.text && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={copyResponse}
              >
                {copied ? "✓ Copiado" : "Copiar texto"}
              </button>
            )}
          </div>

          {busy && (
            <div className="empty-state" style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center" }}>
              <div className="spinner" style={{ width: "2rem", height: "2rem", margin: "0 auto 1rem", borderColor: "var(--petrol-400)", borderTopColor: "transparent" }} />
              <p>Consultando modelo a través de askbox…</p>
              <small className="muted">Enrutando por tier <strong>{tier}</strong> con resolución de claves y caché.</small>
            </div>
          )}

          {!busy && !result && !error && (
            <div className="empty-state" style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center" }}>
              <div className="empty-state-icon">✦</div>
              <p style={{ fontWeight: 600, color: "var(--text-secondary)" }}>Zona de respuesta lista</p>
              <p className="muted" style={{ maxWidth: "340px", margin: "0 auto 1rem", fontSize: "0.85rem" }}>
                Selecciona tus parámetros, escribe un prompt o elige un ejemplo rápido superior y pulsa <strong>Enviar consulta</strong>.
              </p>
              <div style={{ display: "flex", gap: "0.4rem", justifyContent: "center", flexWrap: "wrap" }}>
                <span className="badge badge-slate">Latencia en vivo</span>
                <span className="badge badge-slate">Caché semántica</span>
                <span className="badge badge-slate">Fallback automático</span>
              </div>
            </div>
          )}

          {!busy && result && (
            <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
              {/* Barra de Telemetría */}
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "0.75rem", alignItems: "center" }}>
                <span className="badge badge-petrol" title="Proveedor ejecutado">
                  {result.provider}
                </span>
                <span className="badge badge-slate" title="Modelo exacto utilizado">
                  {result.model_used}
                </span>
                <span className="badge badge-slate" title="Tier efectivo">
                  tier {result.tier_used}
                </span>
                <span className="badge badge-emerald" title="Tiempo de respuesta del backend">
                  {result.latency_ms} ms
                </span>
                {result.cache_hit ? (
                  <span className="badge badge-emerald" title="Respuesta servida desde caché Redis">
                    ⚡ Caché HIT
                  </span>
                ) : (
                  <span className="badge badge-slate" title="Consulta resuelta directamente con el proveedor">
                    Caché Miss
                  </span>
                )}
                {result.tier_escalated_from && (
                  <span className="badge badge-amber" title="Tier escalado automáticamente">
                    ↑ Escalado desde {result.tier_escalated_from}
                  </span>
                )}
                {result.task_used && (
                  <span className="badge badge-slate" title="Tarea aplicada a los adjuntos">
                    task {result.task_used}
                  </span>
                )}
                {result.attachments_processed?.length > 0 && (
                  <span className="badge badge-slate" title="Adjuntos procesados (metadatos)">
                    {result.attachments_processed.length} adjunto(s)
                  </span>
                )}
              </div>

              {result.models_chain?.length > 1 && (
                <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginBottom: "0.5rem" }}>
                  <strong>Cadena de fallback:</strong> {result.models_chain.join(" → ")}
                </div>
              )}

              {/* Texto de la respuesta */}
              <div className="response-container" style={{ flex: 1 }}>
                <div className="response-text">
                  {result.text}
                </div>
              </div>

              <div style={{ marginTop: "0.6rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <small className="muted" style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>
                  req_id: {result.request_id}
                </small>
                <small className="muted">
                  Tokens máx: {maxTokens}
                </small>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Sección Inferior: Integración Externa (Postman vs Playground) */}
      <div className="card">
        <div className="card-header">
          <h3 className="card-title">Integración Externa: ¿Cómo llamar a esto desde Postman o tu backend?</h3>
          <span className="badge badge-emerald">API Bearer</span>
        </div>

        <p style={{ fontSize: "0.9rem" }}>
          El <strong>Playground</strong> que estás usando opera con tu cookie de sesión web. Para consultar este mismo endpoint de forma automatizada desde <strong>Postman</strong>, <strong>cURL</strong> o tus servicios de backend, necesitas un token de acceso con prefijo <code>ab_live_</code>.
        </p>

        <div className="row" style={{ alignItems: "center", margin: "0.75rem 0" }}>
          <div style={{ flex: "1 1 300px" }}>
            <ol style={{ paddingLeft: "1.2rem", fontSize: "0.86rem", color: "var(--text-secondary)", lineHeight: 1.6 }}>
              <li>Ve a <Link href="/projects"><strong>Proyectos y claves</strong></Link> y crea un Token de acceso API.</li>
              <li>Copia el token generado (<code>ab_live_...</code>).</li>
              <li>Añádelo en Postman o en tu cabecera HTTP como: <code>Authorization: Bearer ab_live_...</code></li>
              <li>Envía la petición en formato JSON al endpoint <code>POST /v1/ask</code>.</li>
            </ol>
          </div>
          <div style={{ flex: "0 0 auto", display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <Link href="/projects" className="btn btn-secondary btn-sm">
              Crear Token ab_live_ →
            </Link>
            <a href="/api/docs" target="_blank" rel="noopener noreferrer" className="btn btn-secondary btn-sm">
              Abrir Swagger /docs ↗
            </a>
          </div>
        </div>

        {/* Snippet cURL dinámico */}
        <div style={{ marginTop: "1rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.3rem" }}>
            <small style={{ fontWeight: 600, color: "var(--text-secondary)" }}>
              Ejemplo cURL generado con los parámetros actuales:
            </small>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={copyCurl}
            >
              {curlCopied ? "✓ cURL Copiado" : "Copiar cURL"}
            </button>
          </div>
          <pre style={{ margin: 0, fontSize: "0.82rem" }}>
            {curlSnippet}
          </pre>
        </div>
      </div>
    </div>
  );
}
