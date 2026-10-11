"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "../../lib/api";

const SCOPES_INFO = [
  { id: "ask:read", label: "ask:read", desc: "Permite ejecutar consultas a modelos (POST /v1/ask)", defaultChecked: true },
  { id: "prompts:write", label: "prompts:write", desc: "Crear, editar y versionar plantillas de prompt", defaultChecked: false },
  { id: "keys:write", label: "keys:write", desc: "Añadir o eliminar claves de proveedor", defaultChecked: false },
  { id: "usage:read", label: "usage:read", desc: "Consultar métricas y costes estimados de consumo", defaultChecked: false },
];

function Keys({ project }) {
  const [keys, setKeys] = useState([]);
  const [provider, setProvider] = useState("gemini");
  const [label, setLabel] = useState("");
  const [secret, setSecret] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    () => api(`/v1/projects/${project.id}/keys`).then(setKeys).catch((e) => setMsg(e.message)),
    [project.id],
  );
  useEffect(() => { load(); }, [load]);

  async function add(e) {
    e.preventDefault();
    setMsg("");
    setBusy(true);
    try {
      await api(`/v1/projects/${project.id}/keys`, {
        method: "POST",
        body: { provider, label, key: secret },
      });
      setSecret("");
      setLabel("");
      load();
    } catch (err) {
      setMsg(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function del(id) {
    if (!window.confirm("¿Seguro que deseas eliminar esta clave de proveedor?")) return;
    await api(`/v1/projects/${project.id}/keys/${id}`, { method: "DELETE" }).catch((e) => setMsg(e.message));
    load();
  }

  return (
    <div style={{ marginTop: "1rem" }}>
      <div className="card-header" style={{ borderBottomColor: "var(--border-subtle)" }}>
        <div>
          <h4>1. Claves de Proveedor LLM</h4>
          <p className="field-desc" style={{ margin: 0 }}>
            Tus claves privadas de Gemini y OpenRouter. Se almacenan <strong>cifradas en BD (AES-GCM)</strong> y nunca se devuelven en claro al navegador.
          </p>
        </div>
        <span className="badge badge-emerald">Cifrado AES-GCM</span>
      </div>

      {keys.length === 0 ? (
        <p className="muted" style={{ fontSize: "0.88rem", margin: "0.5rem 0" }}>
          Sin claves configuradas en este proyecto. Si no añades una clave propia, askbox dependerá de las claves de sistema del servidor (si están permitidas).
        </p>
      ) : (
        <ul className="clean-list" style={{ marginBottom: "1rem" }}>
          {keys.map((k) => (
            <li key={k.id}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                <span className="badge badge-petrol" style={{ textTransform: "capitalize" }}>
                  {k.provider}
                </span>
                <code style={{ fontSize: "0.85rem" }}>****{k.last4}</code>
                {k.label && <span style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>({k.label})</span>}
                <span className={`badge ${k.status === "active" ? "badge-emerald" : "badge-rose"}`}>
                  {k.status}
                </span>
                {k.cooldown_until && (
                  <span className="badge badge-amber" title={`En cooldown hasta ${k.cooldown_until}`}>
                    cooldown activo
                  </span>
                )}
              </div>
              <button type="button" className="btn-danger btn-sm" onClick={() => del(k.id)}>
                Eliminar
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Formulario para añadir clave */}
      <form onSubmit={add} style={{ background: "var(--bg-subtle)", padding: "1rem", borderRadius: "8px", border: "1px solid var(--border-subtle)" }}>
        <h5 style={{ marginBottom: "0.5rem" }}>Añadir nueva clave</h5>
        <div className="row" style={{ marginTop: 0 }}>
          <div>
            <label htmlFor={`prov-${project.id}`}>Proveedor</label>
            <select id={`prov-${project.id}`} value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="gemini">Google Gemini</option>
              <option value="openrouter">OpenRouter</option>
              <option value="opencode_zen">OpenCode Zen</option>
            </select>
          </div>
          <div>
            <label htmlFor={`label-${project.id}`}>Etiqueta (opcional)</label>
            <input
              id={`label-${project.id}`}
              placeholder="Ej. Clave producción o cuenta personal"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              maxLength={80}
            />
          </div>
        </div>

        <label htmlFor={`sec-${project.id}`}>Clave API (se cifra en el servidor)</label>
        <input
          id={`sec-${project.id}`}
          type="password"
          placeholder="AIzaSy... o sk-or-v1-..."
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          required
          minLength={8}
          maxLength={512}
          autoComplete="off"
        />

        <div style={{ marginTop: "0.75rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <button type="submit" className="btn-primary btn-sm" disabled={busy || !secret.trim()}>
            {busy ? "Cifrando y guardando…" : "+ Guardar clave cifrada"}
          </button>
          <small className="muted">Rotación y cooldown automáticos en fallos.</small>
        </div>
      </form>
      {msg && <div className="err" style={{ marginTop: "0.5rem" }}>{msg}</div>}
    </div>
  );
}

function AccessTokens({ project }) {
  const [tokens, setTokens] = useState([]);
  const [label, setLabel] = useState("");
  const [scopeSel, setScopeSel] = useState({ "ask:read": true });
  const [created, setCreated] = useState(null);
  const [copied, setCopied] = useState(false);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    () => api(`/v1/projects/${project.id}/access-tokens`).then(setTokens).catch((e) => setMsg(e.message)),
    [project.id],
  );
  useEffect(() => { load(); }, [load]);

  function toggleScope(s) {
    setScopeSel((prev) => ({ ...prev, [s]: !prev[s] }));
  }

  async function create(e) {
    e.preventDefault();
    setMsg("");
    setCreated(null);
    const scopes = SCOPES_INFO.filter((s) => scopeSel[s.id]).map((s) => s.id);
    if (scopes.length === 0) {
      setMsg("Debes seleccionar al menos un scope de permisos.");
      return;
    }
    setBusy(true);
    try {
      const res = await api(`/v1/projects/${project.id}/access-tokens`, {
        method: "POST",
        body: { label, scopes },
      });
      setCreated(res.token);
      setLabel("");
      load();
    } catch (err) {
      setMsg(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id) {
    if (!window.confirm("¿Seguro que deseas revocar este token? Dejará de funcionar de inmediato.")) return;
    await api(`/v1/projects/${project.id}/access-tokens/${id}`, { method: "DELETE" }).catch((e) => setMsg(e.message));
    load();
  }

  function copyToken() {
    if (!created) return;
    navigator.clipboard.writeText(created).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    });
  }

  return (
    <div style={{ marginTop: "1.5rem" }}>
      <div className="card-header" style={{ borderBottomColor: "var(--border-subtle)" }}>
        <div>
          <h4>2. Tokens de Acceso API (Bearer ab_live_)</h4>
          <p className="field-desc" style={{ margin: 0 }}>
            Credenciales Bearer para invocar la API desde <strong>Postman, cURL, scripts o tus aplicaciones backend</strong>.
          </p>
        </div>
        <span className="badge badge-petrol">Prefijo ab_live_</span>
      </div>

      {created && (
        <div className="card" style={{ background: "var(--emerald-tint)", borderColor: "var(--emerald-500)", margin: "0.75rem 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.4rem" }}>
            <strong style={{ color: "var(--emerald-500)", display: "flex", alignItems: "center", gap: "0.4rem" }}>
              <span>✓</span> Token creado con éxito — Copia ahora:
            </strong>
            <button type="button" className="btn-primary btn-sm" onClick={copyToken}>
              {copied ? "✓ Copiado al portapapeles" : "Copiar Token"}
            </button>
          </div>
          <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)", margin: "0.2rem 0" }}>
            Por motivos de seguridad, este token <strong>no se volverá a mostrar</strong>. En base de datos solo se almacena su hash criptográfico SHA-256.
          </p>
          <pre style={{ margin: "0.5rem 0 0", background: "var(--bg-app)", fontSize: "0.85rem", wordBreak: "break-all" }}>
            {created}
          </pre>
        </div>
      )}

      {tokens.length === 0 ? (
        <p className="muted" style={{ fontSize: "0.88rem", margin: "0.5rem 0" }}>
          Sin tokens creados en este proyecto. Crea uno para empezar a llamar a <code>POST /v1/ask</code> de forma automatizada.
        </p>
      ) : (
        <ul className="clean-list" style={{ marginBottom: "1rem" }}>
          {tokens.map((t) => (
            <li key={t.id}>
              <div>
                <code style={{ fontWeight: 600 }}>{t.prefix}...</code>
                {t.label && <span style={{ marginLeft: "0.5rem", fontSize: "0.85rem" }}>({t.label})</span>}
                <div style={{ display: "flex", gap: "0.3rem", marginTop: "0.3rem", flexWrap: "wrap" }}>
                  {t.scopes.map((s) => (
                    <span key={s} className="badge badge-slate" style={{ fontSize: "0.72rem" }}>
                      {s}
                    </span>
                  ))}
                  {t.revoked_at ? (
                    <span className="badge badge-rose">Revocado</span>
                  ) : (
                    <span className="badge badge-emerald">Activo</span>
                  )}
                </div>
              </div>
              {!t.revoked_at && (
                <button type="button" className="btn-danger btn-sm" onClick={() => revoke(t.id)}>
                  Revocar
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Formulario crear token */}
      <form onSubmit={create} style={{ background: "var(--bg-subtle)", padding: "1rem", borderRadius: "8px", border: "1px solid var(--border-subtle)" }}>
        <h5 style={{ marginBottom: "0.5rem" }}>Crear nuevo token de acceso API</h5>
        <label htmlFor={`token-label-${project.id}`}>Etiqueta de la integración</label>
        <input
          id={`token-label-${project.id}`}
          placeholder="Ej. Postman dev, Microservicio backend, Worker tareas"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          maxLength={80}
        />

        <label style={{ marginTop: "0.6rem" }}>Permisos / Scopes asignados:</label>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "0.5rem", marginTop: "0.3rem" }}>
          {SCOPES_INFO.map((s) => (
            <label
              key={s.id}
              className="checkbox-label"
              style={{
                background: "var(--bg-surface)",
                padding: "0.5rem 0.75rem",
                borderRadius: "6px",
                border: "1px solid var(--border-subtle)",
                margin: 0,
              }}
            >
              <input
                type="checkbox"
                checked={!!scopeSel[s.id]}
                onChange={() => toggleScope(s.id)}
              />
              <div>
                <strong style={{ fontSize: "0.82rem", display: "block" }}>{s.label}</strong>
                <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", display: "block", lineHeight: 1.25 }}>
                  {s.desc}
                </span>
              </div>
            </label>
          ))}
        </div>

        <div style={{ marginTop: "0.85rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <button type="submit" className="btn-primary btn-sm" disabled={busy}>
            {busy ? "Generando token…" : "Crear token API ab_live_ →"}
          </button>
          <small className="muted">Autenticación Bearer para POST /v1/ask</small>
        </div>
      </form>
      {msg && <div className="err" style={{ marginTop: "0.5rem" }}>{msg}</div>}
    </div>
  );
}

function Prompts({ project }) {
  const [prompts, setPrompts] = useState([]);
  const [name, setName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userTemplate, setUserTemplate] = useState("");
  const [defaultTier, setDefaultTier] = useState("auto");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    () => api(`/v1/projects/${project.id}/prompts`).then(setPrompts).catch((e) => setMsg(e.message)),
    [project.id],
  );
  useEffect(() => { load(); }, [load]);

  async function save(e) {
    e.preventDefault();
    setMsg("");
    setBusy(true);
    try {
      await api(`/v1/projects/${project.id}/prompts`, {
        method: "POST",
        body: {
          name,
          system_prompt: systemPrompt,
          user_template: userTemplate,
          default_tier: defaultTier,
        },
      });
      setName("");
      setSystemPrompt("");
      setUserTemplate("");
      load();
    } catch (err) {
      setMsg(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function delPrompt(promptName) {
    if (!window.confirm(`¿Seguro que deseas eliminar la plantilla "${promptName}" y sus versiones?`)) return;
    await api(`/v1/projects/${project.id}/prompts/${encodeURIComponent(promptName)}`, { method: "DELETE" }).catch((e) => setMsg(e.message));
    load();
  }

  return (
    <div style={{ marginTop: "1.5rem" }}>
      <div className="card-header" style={{ borderBottomColor: "var(--border-subtle)" }}>
        <div>
          <h4>3. Plantillas de Prompt Versionadas</h4>
          <p className="field-desc" style={{ margin: 0 }}>
            Centraliza tus prompts con variables dinámicas tipo <code>{`{{nombre}}`}</code>. Cada guardado crea una nueva versión automática.
          </p>
        </div>
        <span className="badge badge-amber">Variables dinámicas</span>
      </div>

      {prompts.length === 0 ? (
        <p className="muted" style={{ fontSize: "0.88rem", margin: "0.5rem 0" }}>
          Sin plantillas configuradas en este proyecto.
        </p>
      ) : (
        <ul className="clean-list" style={{ marginBottom: "1rem" }}>
          {prompts.map((p) => (
            <li key={p.id}>
              <div>
                <strong>{p.name}</strong> <span className="badge badge-slate">v{p.version}</span>
                <span className="badge badge-petrol" style={{ marginLeft: "0.4rem" }}>tier {p.default_tier}</span>
                {p.variables?.length > 0 && (
                  <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
                    Variables requeridas: {p.variables.map((v) => <code key={v} style={{ marginRight: "0.3rem" }}>{`{{${v}}}`}</code>)}
                  </div>
                )}
              </div>
              <button type="button" className="btn-danger btn-sm" onClick={() => delPrompt(p.name)}>
                Eliminar
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Formulario guardar plantilla */}
      <form onSubmit={save} style={{ background: "var(--bg-subtle)", padding: "1rem", borderRadius: "8px", border: "1px solid var(--border-subtle)" }}>
        <h5 style={{ marginBottom: "0.5rem" }}>Guardar nueva versión de plantilla</h5>

        <div className="row" style={{ marginTop: 0 }}>
          <div>
            <label htmlFor={`pname-${project.id}`}>Nombre único (slug)</label>
            <input
              id={`pname-${project.id}`}
              placeholder="resumen-noticia, extractor-factura"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              pattern="[a-zA-Z0-9_-]+"
              maxLength={80}
            />
          </div>
          <div>
            <label htmlFor={`ptier-${project.id}`}>Tier sugerido</label>
            <select id={`ptier-${project.id}`} value={defaultTier} onChange={(e) => setDefaultTier(e.target.value)}>
              <option value="auto">auto</option>
              <option value="fast">fast</option>
              <option value="balanced">balanced</option>
              <option value="strong">strong</option>
            </select>
          </div>
        </div>

        <label htmlFor={`psys-${project.id}`}>System Prompt (opcional)</label>
        <div className="field-desc">Instrucciones de comportamiento general y rol para el modelo.</div>
        <textarea
          id={`psys-${project.id}`}
          placeholder="Eres un asistente experto en análisis financiero..."
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          rows={2}
          maxLength={50000}
        />

        <label htmlFor={`ptpl-${project.id}`}>Plantilla de Usuario (con {`{{variables}}`})</label>
        <div className="field-desc">Plantilla donde se sustituirán los valores en cada llamada a la API.</div>
        <textarea
          id={`ptpl-${project.id}`}
          placeholder="Analiza el siguiente texto: {{texto_usuario}} y responde en formato {{idioma}}."
          value={userTemplate}
          onChange={(e) => setUserTemplate(e.target.value)}
          required
          rows={3}
          maxLength={200000}
        />

        <div style={{ marginTop: "0.85rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <button type="submit" className="btn-primary btn-sm" disabled={busy || !name || !userTemplate}>
            {busy ? "Guardando versión…" : "Guardar plantilla versionada →"}
          </button>
          <small className="muted">Crea v+1 manteniendo historial.</small>
        </div>
      </form>
      {msg && <div className="err" style={{ marginTop: "0.5rem" }}>{msg}</div>}
    </div>
  );
}

export default function Projects() {
  const router = useRouter();
  const [projects, setProjects] = useState([]);
  const [name, setName] = useState("");
  const [allowSystem, setAllowSystem] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api("/v1/projects")
      .then(setProjects)
      .catch((e) => (e.status === 401 ? router.replace("/login") : setError(e.message)));
  }, [router]);

  useEffect(() => { load(); }, [load]);

  async function create(e) {
    e.preventDefault();
    if (!name.trim()) return;
    setError("");
    setBusy(true);
    try {
      await api("/v1/projects", {
        method: "POST",
        body: { name, allow_system_keys: allowSystem },
      });
      setName("");
      setAllowSystem(false);
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {/* Header */}
      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.75rem" }}>
          <div>
            <h1>Proyectos, Claves y Tokens API</h1>
            <p style={{ margin: 0 }}>
              Administra tus entornos de trabajo, registra claves de Gemini/OpenRouter y genera tokens Bearer <code>ab_live_</code> para Postman y automatizaciones.
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <Link href="/playground" className="btn btn-secondary btn-sm">
              Probar en Playground →
            </Link>
          </div>
        </div>
      </div>

      {/* Formulario Crear Nuevo Proyecto */}
      <form className="card" onSubmit={create} style={{ marginBottom: "1.5rem" }}>
        <div className="card-header">
          <h3 className="card-title">+ Crear Nuevo Proyecto</h3>
          <span className="badge badge-petrol">Espacio Aislado</span>
        </div>
        <div className="row" style={{ marginTop: 0, alignItems: "center" }}>
          <div style={{ flex: "2 1 280px" }}>
            <label htmlFor="new-project-name">Nombre del proyecto</label>
            <input
              id="new-project-name"
              placeholder="Ej. Producción, Blog Asistente, Testing QA"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={80}
            />
          </div>
          <div style={{ flex: "1 1 200px", paddingTop: "1.4rem" }}>
            <label className="checkbox-label" style={{ margin: 0 }}>
              <input
                type="checkbox"
                checked={allowSystem}
                onChange={(e) => setAllowSystem(e.target.checked)}
              />
              <span>Permitir claves de sistema de respaldo</span>
            </label>
          </div>
          <div style={{ flex: "0 0 auto", paddingTop: "1.4rem" }}>
            <button type="submit" className="btn-primary" disabled={busy || !name.trim()}>
              {busy ? "Creando…" : "Crear Proyecto"}
            </button>
          </div>
        </div>
        {error && <div className="err" style={{ marginTop: "0.75rem" }}>{error}</div>}
      </form>

      {/* Lista de Proyectos */}
      {projects.length === 0 ? (
        <div className="card empty-state">
          <div className="empty-state-icon">📁</div>
          <h3>Sin proyectos activos</h3>
          <p className="muted">Crea tu primer proyecto arriba para configurar claves y generar tokens de acceso.</p>
        </div>
      ) : (
        projects.map((p) => (
          <div className="card" key={p.id} style={{ marginBottom: "2rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "0.5rem", paddingBottom: "0.75rem", borderBottom: "1px solid var(--border-subtle)" }}>
              <div>
                <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <span>📁</span> {p.name}
                </h3>
                <small className="muted">
                  ID: <code style={{ fontSize: "0.75rem" }}>{p.id}</code> · Tier por defecto: <strong>{p.default_tier}</strong>
                </small>
              </div>
              <div style={{ display: "flex", gap: "0.4rem" }}>
                <span className={`badge ${p.allow_system_keys ? "badge-emerald" : "badge-slate"}`}>
                  Claves de sistema: {p.allow_system_keys ? "Permitidas" : "Solo propias"}
                </span>
              </div>
            </div>

            {/* Subcomponentes de Configuración */}
            <Keys project={p} />
            <AccessTokens project={p} />
            <Prompts project={p} />
          </div>
        ))
      )}
    </div>
  );
}
