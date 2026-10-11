"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";

const SCOPES = ["ask:read", "prompts:write", "keys:write", "usage:read"];

function Keys({ project }) {
  const [keys, setKeys] = useState([]);
  const [provider, setProvider] = useState("gemini");
  const [label, setLabel] = useState("");
  const [secret, setSecret] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => api(`/v1/projects/${project.id}/keys`).then(setKeys).catch((e) => setMsg(e.message)), [project.id]);
  useEffect(() => { load(); }, [load]);

  async function add(e) {
    e.preventDefault();
    setMsg("");
    try {
      await api(`/v1/projects/${project.id}/keys`, { method: "POST", body: { provider, label, key: secret } });
      setSecret("");
      setLabel("");
      load();
    } catch (err) {
      setMsg(err.message);
    }
  }

  async function del(id) {
    await api(`/v1/projects/${project.id}/keys/${id}`, { method: "DELETE" }).catch((e) => setMsg(e.message));
    load();
  }

  return (
    <div>
      <h4>Claves de proveedor</h4>
      {keys.length === 0 && <p className="muted">Sin claves de proyecto.</p>}
      <ul>
        {keys.map((k) => (
          <li key={k.id}>
            {k.provider} · ****{k.last4} · {k.status}{k.cooldown_until ? " (cooldown)" : ""} {k.label && `· ${k.label}`}{" "}
            <button type="button" onClick={() => del(k.id)}>Borrar</button>
          </li>
        ))}
      </ul>
      <form onSubmit={add}>
        <div className="row">
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="gemini">Gemini</option>
            <option value="openrouter">OpenRouter</option>
          </select>
          <input placeholder="Etiqueta (opcional)" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} />
        </div>
        <input type="password" placeholder="Pega la clave (se cifra en el servidor)" value={secret} onChange={(e) => setSecret(e.target.value)} required minLength={8} maxLength={512} autoComplete="off" />
        <button type="submit">Añadir clave</button>
      </form>
      {msg && <p className="err">{msg}</p>}
    </div>
  );
}

function AccessTokens({ project }) {
  const [tokens, setTokens] = useState([]);
  const [label, setLabel] = useState("");
  const [scopeSel, setScopeSel] = useState({ "ask:read": true });
  const [created, setCreated] = useState(null);
  const [msg, setMsg] = useState("");

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
    const scopes = SCOPES.filter((s) => scopeSel[s]);
    if (scopes.length === 0) {
      setMsg("Elige al menos un scope.");
      return;
    }
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
    }
  }

  async function revoke(id) {
    await api(`/v1/projects/${project.id}/access-tokens/${id}`, { method: "DELETE" }).catch((e) => setMsg(e.message));
    load();
  }

  return (
    <div>
      <h4>Tokens de acceso (API)</h4>
      <p className="muted">Prefijo <code>ab_live_</code>. Se muestran una vez al crear; en BD solo el hash.</p>
      <ul>
        {tokens.map((t) => (
          <li key={t.id}>
            {t.prefix} · {t.scopes.join(", ")} {t.label && `· ${t.label}`}
            {t.revoked_at ? " · revocado" : ""}
            {!t.revoked_at && (
              <button type="button" onClick={() => revoke(t.id)}>Revocar</button>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={create}>
        <input placeholder="Etiqueta (opcional)" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} />
        <div className="row">
          {SCOPES.map((s) => (
            <label key={s} style={{ display: "inline-flex", gap: "0.25rem", marginRight: "0.75rem" }}>
              <input type="checkbox" checked={!!scopeSel[s]} onChange={() => toggleScope(s)} />
              {s}
            </label>
          ))}
        </div>
        <button type="submit">Crear token</button>
      </form>
      {created && (
        <p className="card">
          <strong>Copia ahora:</strong>
          <code style={{ display: "block", wordBreak: "break-all", marginTop: "0.5rem" }}>{created}</code>
        </p>
      )}
      {msg && <p className="err">{msg}</p>}
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

  const load = useCallback(
    () => api(`/v1/projects/${project.id}/prompts`).then(setPrompts).catch((e) => setMsg(e.message)),
    [project.id],
  );
  useEffect(() => { load(); }, [load]);

  async function save(e) {
    e.preventDefault();
    setMsg("");
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
    }
  }

  async function delPrompt(promptName) {
    await api(`/v1/projects/${project.id}/prompts/${encodeURIComponent(promptName)}`, { method: "DELETE" }).catch((e) => setMsg(e.message));
    load();
  }

  return (
    <div>
      <h4>Plantillas de prompt</h4>
      <p className="muted">Variables con <code>{`{{nombre}}`}</code>. Cada guardado crea una nueva versión.</p>
      <ul>
        {prompts.map((p) => (
          <li key={p.id}>
            <strong>{p.name}</strong> v{p.version} · tier {p.default_tier}
            {p.variables?.length ? ` · vars: ${p.variables.join(", ")}` : ""}
            <button type="button" onClick={() => delPrompt(p.name)}>Borrar</button>
          </li>
        ))}
      </ul>
      <form onSubmit={save}>
        <label>
          Nombre (slug)
          <input value={name} onChange={(e) => setName(e.target.value)} required pattern="[a-zA-Z0-9_-]+" maxLength={80} />
        </label>
        <label>
          System (opcional)
          <textarea value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} rows={2} maxLength={50000} />
        </label>
        <label>
          Plantilla usuario
          <textarea value={userTemplate} onChange={(e) => setUserTemplate(e.target.value)} required rows={3} maxLength={200000} />
        </label>
        <label>
          Tier por defecto
          <select value={defaultTier} onChange={(e) => setDefaultTier(e.target.value)}>
            <option value="auto">auto</option>
            <option value="fast">fast</option>
            <option value="balanced">balanced</option>
            <option value="strong">strong</option>
          </select>
        </label>
        <button type="submit">Guardar plantilla</button>
      </form>
      {msg && <p className="err">{msg}</p>}
    </div>
  );
}

export default function Projects() {
  const router = useRouter();
  const [projects, setProjects] = useState([]);
  const [name, setName] = useState("");
  const [allowSystem, setAllowSystem] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api("/v1/projects").then(setProjects).catch((e) => (e.status === 401 ? router.replace("/login") : setError(e.message)));
  }, [router]);
  useEffect(() => { load(); }, [load]);

  async function create(e) {
    e.preventDefault();
    setError("");
    try {
      await api("/v1/projects", { method: "POST", body: { name, allow_system_keys: allowSystem } });
      setName("");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div>
      <h2>Proyectos</h2>
      <form className="card" onSubmit={create}>
        <label>Nuevo proyecto<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} /></label>
        <label><input type="checkbox" style={{ width: "auto" }} checked={allowSystem} onChange={(e) => setAllowSystem(e.target.checked)} /> Permitir claves de sistema del servidor como respaldo</label>
        <button type="submit">Crear</button>
        {error && <p className="err">{error}</p>}
      </form>
      {projects.map((p) => (
        <div className="card" key={p.id}>
          <strong>{p.name}</strong> <small>tier por defecto: {p.default_tier} · claves de sistema: {p.allow_system_keys ? "sí" : "no"}</small>
          <AccessTokens project={p} />
          <Prompts project={p} />
          <Keys project={p} />
        </div>
      ))}
    </div>
  );
}
