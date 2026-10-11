"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../../lib/api";

export default function GitSettings() {
  const router = useRouter();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  // Preferencias (formulario controlado tras cargar el estado)
  const [autoCommit, setAutoCommit] = useState(true);
  const [remoteName, setRemoteName] = useState("origin");
  const [visibility, setVisibility] = useState("private");

  // Conectar remoto existente
  const [remoteUrl, setRemoteUrl] = useState("");

  // Crear remoto con gh
  const [slug, setSlug] = useState("");
  const [createVisibility, setCreateVisibility] = useState("private");
  const [pushNow, setPushNow] = useState(false);

  const load = useCallback(() => {
    api("/v1/settings/git/status")
      .then((s) => {
        setStatus(s);
        if (s.preferences) {
          setAutoCommit(!!s.preferences.auto_commit);
          setRemoteName(s.preferences.remote_name || "origin");
          setVisibility(s.preferences.default_visibility || "private");
        }
      })
      .catch((e) => (e.status === 401 ? router.replace("/login") : setError(e.message)));
  }, [router]);

  useEffect(() => { load(); }, [load]);

  async function savePreferences(e) {
    e.preventDefault();
    setError(""); setMsg("");
    try {
      await api("/v1/settings/git/preferences", {
        method: "PATCH",
        body: { auto_commit: autoCommit, remote_name: remoteName, default_visibility: visibility },
      });
      setMsg("Preferencias guardadas.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function connectRemote(e) {
    e.preventDefault();
    setError(""); setMsg("");
    try {
      await api("/v1/settings/git/remote", { method: "POST", body: { name: remoteName, url: remoteUrl } });
      setRemoteUrl(""); // no conservar en el formulario (puede llevar credenciales embebidas)
      setMsg("Remoto conectado.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function createRemote(e) {
    e.preventDefault();
    setError(""); setMsg("");
    try {
      await api("/v1/settings/git/remote", {
        method: "POST",
        body: { name: remoteName, create: true, slug: slug || undefined, visibility: createVisibility, push: pushNow },
      });
      setMsg("Repo remoto creado con gh.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  if (!status) {
    return (
      <div>
        <h2>Configuración Git</h2>
        {error ? <p className="err">{error}</p> : <p className="muted">Cargando…</p>}
      </div>
    );
  }

  return (
    <div>
      <h2>Configuración Git</h2>
      <p className="muted">
        Estado del repositorio del proyecto, conexión de remoto y preferencias de auto-commit.
        Las credenciales (tokens, SSH) nunca se guardan aquí: solo metadatos no secretos.
      </p>

      {!status.available && (
        <div className="card">
          <p className="err">No disponible: {status.reason}</p>
        </div>
      )}

      {status.available && (
        <div className="card">
          <h4>Estado</h4>
          <p>
            Rama: <strong>{status.branch || "—"}</strong>
            {status.upstream ? <> · tracking: <strong>{status.upstream}</strong></> : null}
            {" · "}
            {status.dirty ? <span className="err">cambios sin commitear</span> : <span className="ok">sin cambios pendientes</span>}
          </p>
          <p>
            Último commit:{" "}
            {status.last_commit?.hash ? (
              <code>{status.last_commit.hash}</code>
            ) : (
              <span className="muted">sin commits todavía</span>
            )}
            {status.last_commit?.subject && <> — {status.last_commit.subject}</>}
            {status.last_commit?.date && <> · <small className="muted">{status.last_commit.date}</small></>}
          </p>
          {status.preferences?.last_push_at && (
            <p><small className="muted">Último push registrado: {status.preferences.last_push_at}</small></p>
          )}
          <h4>Remotos</h4>
          {status.remotes?.length ? (
            <ul>
              {status.remotes.map((r, i) => (
                <li key={i}>{r.name} ({r.type}): <code>{r.url}</code></li>
              ))}
            </ul>
          ) : (
            <p className="muted">Sin remotos configurados todavía.</p>
          )}
        </div>
      )}

      {status.available && (
        <div className="card">
          <h4>Conectar remoto existente</h4>
          <form onSubmit={connectRemote}>
            <label>
              URL del remoto
              <input
                type="password"
                placeholder="https://usuario:token@github.com/usuario/repo.git"
                value={remoteUrl}
                onChange={(e) => setRemoteUrl(e.target.value)}
                autoComplete="off"
                required
              />
            </label>
            <small className="muted">Puede incluir credenciales embebidas; nunca se guardan ni se muestran de vuelta.</small>
            <button>Conectar como &quot;{remoteName}&quot;</button>
          </form>
        </div>
      )}

      {status.available && (
        <div className="card">
          <h4>Crear repo remoto con gh</h4>
          <p className="muted">Requiere <code>gh auth login</code> ya hecho en el host/contenedor.</p>
          <form onSubmit={createRemote}>
            <div className="row">
              <input placeholder="nombre-repo (opcional)" value={slug} onChange={(e) => setSlug(e.target.value)} maxLength={100} />
              <select value={createVisibility} onChange={(e) => setCreateVisibility(e.target.value)}>
                <option value="private">Privado</option>
                <option value="public">Público</option>
              </select>
            </div>
            <label><input type="checkbox" style={{ width: "auto" }} checked={pushNow} onChange={(e) => setPushNow(e.target.checked)} /> Pushear ahora</label>
            <button>Crear con gh</button>
          </form>
        </div>
      )}

      <div className="card">
        <h4>Preferencias</h4>
        <form onSubmit={savePreferences}>
          <label><input type="checkbox" style={{ width: "auto" }} checked={autoCommit} onChange={(e) => setAutoCommit(e.target.checked)} /> Auto-commit habilitado para este proyecto</label>
          <label>Nombre del remoto<input value={remoteName} onChange={(e) => setRemoteName(e.target.value)} maxLength={50} /></label>
          <label>
            Visibilidad por defecto al crear repos
            <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
              <option value="private">Privado</option>
              <option value="public">Público</option>
            </select>
          </label>
          <button>Guardar preferencias</button>
        </form>
      </div>

      {msg && <p className="ok">{msg}</p>}
      {error && <p className="err">{error}</p>}
    </div>
  );
}
