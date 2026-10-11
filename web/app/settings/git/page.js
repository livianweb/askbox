"use client";
import { useCallback, useEffect, useRef, useState } from "react";
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

  // GitHub token (solo se escribe; el servidor nunca lo devuelve)
  const [repo, setRepo] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const [loading, setLoading] = useState(false);
  const reqId = useRef(0);

  const load = useCallback(() => {
    const id = ++reqId.current;
    setLoading(true);
    api(`/v1/settings/git/status${repo ? `?repo=${encodeURIComponent(repo)}` : ""}`)
      .then((s) => {
        if (id !== reqId.current) return; // respuesta de un repo anterior: descartar
        setStatus(s);
        setLoading(false);
        if (s.preferences) {
          setAutoCommit(!!s.preferences.auto_commit);
          setRemoteName(s.preferences.remote_name || "origin");
          setVisibility(s.preferences.default_visibility || "private");
          setCreateVisibility(s.preferences.default_visibility || "private");
        }
      })
      .catch((e) => {
        if (id !== reqId.current) return;
        setLoading(false);
        if (e.status === 401) router.replace("/login");
        else setError(e.message);
      });
  }, [router, repo]);

  useEffect(() => { load(); }, [load]);

  const q = repo ? `?repo=${encodeURIComponent(repo)}` : "";
  // Repo efectivo del selector (el token es por repositorio)
  const currentRepo = repo || status?.repo || "askbox";
  const tokenQ = `?repo=${encodeURIComponent(currentRepo)}`;

  function changeRepo(next) {
    setRepo(next);
    setTokenInput(""); // no arrastrar un PAT a otro repo
    setError(""); setMsg("");
    // el estado del token del repo anterior no debe mostrarse mientras carga el nuevo
    setStatus((s) => (s ? { ...s, github_token_configured: undefined, github_token_last4: undefined } : s));
  }

  async function saveToken(e) {
    e.preventDefault();
    setError(""); setMsg("");
    try {
      await api(`/v1/settings/git/github-token${tokenQ}`, { method: "PUT", body: { token: tokenInput } });
      setTokenInput(""); // no conservar el PAT en el estado del navegador
      setMsg(`GitHub token de "${currentRepo}" guardado (cifrado en el servidor).`);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function deleteToken() {
    setError(""); setMsg("");
    try {
      await api(`/v1/settings/git/github-token${tokenQ}`, { method: "DELETE" });
      setMsg(`GitHub token de "${currentRepo}" borrado.`);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function pushRemote() {
    setError(""); setMsg("");
    try {
      await api(`/v1/settings/git/push${q}`, { method: "POST", body: {} });
      setMsg("Push realizado.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

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
      await api("/v1/settings/git/remote" + q, { method: "POST", body: { name: remoteName, url: remoteUrl } });
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
      const res = await api("/v1/settings/git/remote" + q, {
        method: "POST",
        body: { name: remoteName, create: true, slug: slug || undefined, visibility: createVisibility, push: pushNow },
      });
      setMsg(res?.created_repo?.html_url ? `Repo creado: ${res.created_repo.html_url}` : "Repo remoto creado.");
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
        Las preferencias son metadatos no secretos. Cada repositorio tiene su propio GitHub token, guardado
        cifrado (AES-GCM) en la base de datos del servidor, nunca en el repo, y no se vuelve a mostrar.
      </p>

      {status.repos?.length > 1 && (
        <div className="card">
          <label>
            Repositorio
            <select value={currentRepo} onChange={(e) => changeRepo(e.target.value)}>
              {status.repos.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
        </div>
      )}

      <div className="card">
        <h4>Token de este repositorio: <code>{currentRepo}</code></h4>
        <p>
          Estado:{" "}
          {loading || status.github_token_configured === undefined ? (
            <span className="muted">comprobando…</span>
          ) : status.github_token_configured ? (
            <span className="ok">configurado{status.github_token_last4 ? ` (…${status.github_token_last4})` : ""}</span>
          ) : (
            <span className="muted">sin token en este repo</span>
          )}
          {status.gh_authenticated ? <> · <small className="muted">gh autenticado en el host</small></> : null}
        </p>
        <form onSubmit={saveToken}>
          <label>
            Personal Access Token para «{currentRepo}» (permiso de crear repos y escribir contenido)
            <input
              type="password"
              placeholder="Pega aquí tu token de GitHub"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              autoComplete="new-password"
              required
            />
          </label>
          <small className="muted">Solo se usa para «{currentRepo}» (crear remoto y push). Se cifra al guardar y no vuelve a salir del servidor. Guardar de nuevo lo reemplaza.</small>
          <div className="row">
            <button disabled={loading}>{status.github_token_configured ? "Reemplazar token" : "Guardar token"}</button>
            {status.github_token_configured && (
              <button type="button" onClick={deleteToken}>Borrar token</button>
            )}
          </div>
        </form>
      </div>

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
          <p><button type="button" onClick={pushRemote} disabled={!status.remotes?.length}>Push de la rama actual</button></p>
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
          <h4>Crear repo remoto en GitHub</h4>
          <p className="muted">
            Usa <code>gh</code> si está autenticado en el host; si no, el token de este repositorio guardado arriba (el nombre es obligatorio con token).
          </p>
          <form onSubmit={createRemote}>
            <div className="row">
              <input placeholder="nombre-repo (opcional)" value={slug} onChange={(e) => setSlug(e.target.value)} maxLength={100} />
              <select value={createVisibility} onChange={(e) => setCreateVisibility(e.target.value)}>
                <option value="private">Privado</option>
                <option value="public">Público</option>
              </select>
            </div>
            <label><input type="checkbox" style={{ width: "auto" }} checked={pushNow} onChange={(e) => setPushNow(e.target.checked)} /> Pushear ahora</label>
            <button>Crear repo</button>
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
