"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";

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
      setSecret(""); // el valor no se conserva en la UI tras enviarlo
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
      <h4>Claves</h4>
      {keys.length === 0 && <p className="muted">Sin claves de proyecto.</p>}
      <ul>
        {keys.map((k) => (
          <li key={k.id}>
            {k.provider} · ****{k.last4} · {k.status}{k.cooldown_until ? " (cooldown)" : ""} {k.label && `· ${k.label}`}{" "}
            <button onClick={() => del(k.id)}>Borrar</button>
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
        <button>Añadir clave</button>
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
        <button>Crear</button>
        {error && <p className="err">{error}</p>}
      </form>
      {projects.map((p) => (
        <div className="card" key={p.id}>
          <strong>{p.name}</strong> <small>tier por defecto: {p.default_tier} · claves de sistema: {p.allow_system_keys ? "sí" : "no"}</small>
          <Keys project={p} />
        </div>
      ))}
    </div>
  );
}
