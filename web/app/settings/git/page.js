"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api } from "../../../lib/api";

const PRIMARY_REPO = "askbox";

function repoQuery(repo) {
  return `?repo=${encodeURIComponent(repo || PRIMARY_REPO)}`;
}

function GitSettingsInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedRepo = searchParams.get("repo") || PRIMARY_REPO;

  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const [autoCommit, setAutoCommit] = useState(true);
  const [remoteName, setRemoteName] = useState("origin");
  const [visibility, setVisibility] = useState("private");

  const [remoteUrl, setRemoteUrl] = useState("");

  const [slug, setSlug] = useState("");
  const [createVisibility, setCreateVisibility] = useState("private");
  const [pushNow, setPushNow] = useState(false);

  const [tokenInput, setTokenInput] = useState("");
  const [loading, setLoading] = useState(false);
  const reqId = useRef(0);

  const load = useCallback(() => {
    const id = ++reqId.current;
    setLoading(true);
    api(`/v1/settings/git/status${repoQuery(selectedRepo)}`)
      .then((s) => {
        if (id !== reqId.current) return;
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
        else if (e.status === 404 && e.code === "repo_not_found") {
          router.replace("/settings/git", { scroll: false });
        } else setError(e.message);
      });
  }, [router, selectedRepo]);

  useEffect(() => { load(); }, [load]);

  const q = repoQuery(selectedRepo);

  function changeRepo(next) {
    ++reqId.current; // invalida peticiones en vuelo del repo anterior
    setTokenInput("");
    setError("");
    setMsg("");
    setStatus((s) => (s ? { ...s, github_token_configured: undefined, github_token_last4: undefined } : s));
    const qs = new URLSearchParams(searchParams.toString());
    if (next === PRIMARY_REPO) qs.delete("repo");
    else qs.set("repo", next);
    const suffix = qs.toString();
    router.replace(suffix ? `/settings/git?${suffix}` : "/settings/git", { scroll: false });
  }

  async function saveToken(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      const meta = await api(`/v1/settings/git/github-token${q}`, { method: "PUT", body: { token: tokenInput } });
      setTokenInput("");
      setStatus((s) => (s ? { ...s, ...meta } : s));
      setMsg(`GitHub token de "${selectedRepo}" guardado (cifrado en el servidor).`);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function deleteToken() {
    setError("");
    setMsg("");
    try {
      const meta = await api(`/v1/settings/git/github-token${q}`, { method: "DELETE" });
      setStatus((s) => (s ? { ...s, ...meta, github_token_last4: undefined } : s));
      setMsg(`GitHub token de "${selectedRepo}" borrado.`);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function pushRemote() {
    setError("");
    setMsg("");
    try {
      await api(`/v1/settings/git/push${q}`, { method: "POST", body: {} });
      setMsg("Push realizado correctamente.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function savePreferences(e) {
    e.preventDefault();
    setError("");
    setMsg("");
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
    setError("");
    setMsg("");
    try {
      await api(`/v1/settings/git/remote${q}`, { method: "POST", body: { name: remoteName, url: remoteUrl } });
      setRemoteUrl("");
      setMsg("Remoto conectado correctamente.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function createRemote(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      const res = await api(`/v1/settings/git/remote${q}`, {
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
      <div className="card" style={{ textAlign: "center", padding: "3rem" }}>
        {error ? <div className="err">{error}</div> : (
          <>
            <div className="spinner" style={{ marginBottom: "1rem" }} />
            <p className="muted">Cargando estado de Git…</p>
          </>
        )}
      </div>
    );
  }

  const displayRepo = status.repos?.includes(selectedRepo) ? selectedRepo : status.repo || PRIMARY_REPO;

  return (
    <div>
      {/* Header */}
      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.75rem" }}>
          <div>
            <h1>Configuración Git & GitOps</h1>
            <p style={{ margin: 0 }}>
              Estado del repositorio local, conexión con GitHub y preferencias de auto-commit / auto-push.
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <Link href="/settings/deploy" className="btn btn-secondary btn-sm">
              Despliegue y Acceso
            </Link>
          </div>
        </div>
      </div>

      {msg && <div className="ok">{msg}</div>}
      {error && <div className="err">{error}</div>}

      {status.repos?.length > 1 && (
        <div className="card">
          <label htmlFor="select-git-repo">Repositorio activo</label>
          <select id="select-git-repo" value={displayRepo} onChange={(e) => changeRepo(e.target.value)}>
            {status.repos.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
      )}

      {/* Token GitHub */}
      <div className="card">
        <div className="card-header">
          <h3 className="card-title">Token de GitHub para «{displayRepo}»</h3>
          <span className="badge badge-petrol">Personal Access Token</span>
        </div>
        <p style={{ fontSize: "0.88rem" }}>
          Estado actual:{" "}
          {loading || status.github_token_configured === undefined ? (
            <span className="muted">comprobando…</span>
          ) : status.github_token_configured ? (
            <span className="badge badge-emerald">
              Configurado{status.github_token_last4 ? ` (…${status.github_token_last4})` : ""}
            </span>
          ) : (
            <span className="badge badge-slate">Sin token configurado en este repo</span>
          )}
          {status.gh_authenticated ? <> · <small className="muted">CLI gh autenticado en el host</small></> : null}
        </p>

        <form onSubmit={saveToken}>
          <label htmlFor="input-gh-token">Personal Access Token (permiso <code>repo</code>)</label>
          <input
            id="input-gh-token"
            type="password"
            placeholder="ghp_... o github_pat_..."
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            autoComplete="new-password"
            required
          />
          <div className="field-desc">
            Se cifra en el servidor con AES-GCM y solo se utiliza para crear repos y ejecutar push en «{displayRepo}».
          </div>
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem" }}>
            <button type="submit" className="btn-primary btn-sm" disabled={loading}>
              {status.github_token_configured ? "Reemplazar Token" : "Guardar Token Cifrado"}
            </button>
            {status.github_token_configured && (
              <button type="button" className="btn-danger btn-sm" onClick={deleteToken}>
                Borrar Token
              </button>
            )}
          </div>
        </form>
      </div>

      {!status.available && (
        <div className="card">
          <div className="err">No disponible: {status.reason}</div>
        </div>
      )}

      {status.available && (
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Estado del Árbol Git</h3>
            <span className={`badge ${status.dirty ? "badge-rose" : "badge-emerald"}`}>
              {status.dirty ? "Cambios pendientes" : "Limpio"}
            </span>
          </div>

          <ul className="clean-list" style={{ marginBottom: "1rem" }}>
            <li>
              <span>Rama actual:</span>
              <strong>{status.branch || "—"}</strong>
              {status.upstream && <small className="muted"> (tracking: {status.upstream})</small>}
            </li>
            <li>
              <span>Último commit:</span>
              <span>
                {status.last_commit?.hash ? <code>{status.last_commit.hash.slice(0, 7)}</code> : "—"}
                {status.last_commit?.subject && ` — ${status.last_commit.subject}`}
              </span>
            </li>
            {status.preferences?.last_push_at && (
              <li>
                <span>Último push:</span>
                <small className="muted">{status.preferences.last_push_at}</small>
              </li>
            )}
          </ul>

          <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1.25rem" }}>
            <button
              type="button"
              className="btn-primary btn-sm"
              onClick={pushRemote}
              disabled={!status.remotes?.length}
            >
              Push de la rama actual ({status.branch})
            </button>
          </div>

          <h4>Remotos Configurados</h4>
          {status.remotes?.length ? (
            <ul className="clean-list" style={{ marginTop: "0.5rem" }}>
              {status.remotes.map((r, i) => (
                <li key={i}>
                  <div>
                    <strong>{r.name}</strong> <span className="badge badge-slate">{r.type}</span>
                    <div style={{ marginTop: "0.2rem" }}>
                      <code style={{ fontSize: "0.8rem" }}>{r.url}</code>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted" style={{ fontSize: "0.85rem" }}>Sin remotos configurados todavía.</p>
          )}
        </div>
      )}

      {status.available && (
        <div className="grid-2">
          {/* Conectar remoto existente */}
          <div className="card" style={{ margin: 0 }}>
            <div className="card-header">
              <h3 className="card-title">Conectar Remoto Existente</h3>
            </div>
            <form onSubmit={connectRemote}>
              <label htmlFor="input-remote-url">URL del repositorio remoto</label>
              <input
                id="input-remote-url"
                type="password"
                placeholder="https://usuario:token@github.com/..."
                value={remoteUrl}
                onChange={(e) => setRemoteUrl(e.target.value)}
                autoComplete="off"
                required
              />
              <div className="field-desc">
                Puede incluir credenciales embebidas; nunca se guardan en logs.
              </div>
              <div style={{ marginTop: "0.75rem" }}>
                <button type="submit" className="btn-primary btn-sm">
                  Conectar como &quot;{remoteName}&quot;
                </button>
              </div>
            </form>
          </div>

          {/* Crear repo en GitHub */}
          <div className="card" style={{ margin: 0 }}>
            <div className="card-header">
              <h3 className="card-title">Crear Repo en GitHub</h3>
            </div>
            <form onSubmit={createRemote}>
              <div className="row" style={{ marginTop: 0 }}>
                <div>
                  <label htmlFor="input-slug">Nombre repo (opcional)</label>
                  <input
                    id="input-slug"
                    placeholder="nombre-del-repo"
                    value={slug}
                    onChange={(e) => setSlug(e.target.value)}
                    maxLength={100}
                  />
                </div>
                <div>
                  <label htmlFor="select-vis">Visibilidad</label>
                  <select id="select-vis" value={createVisibility} onChange={(e) => setCreateVisibility(e.target.value)}>
                    <option value="private">Privado</option>
                    <option value="public">Público</option>
                  </select>
                </div>
              </div>
              <label className="checkbox-label" style={{ marginTop: "0.5rem" }}>
                <input
                  type="checkbox"
                  checked={pushNow}
                  onChange={(e) => setPushNow(e.target.checked)}
                />
                <span>Hacer push inicial inmediatamente</span>
              </label>
              <div style={{ marginTop: "0.75rem" }}>
                <button type="submit" className="btn-primary btn-sm">
                  Crear Repo Remoto →
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Preferencias de Auto-commit */}
      <div className="card" style={{ marginTop: "1.5rem" }}>
        <div className="card-header">
          <h3 className="card-title">Preferencias de Auto-Commit</h3>
        </div>
        <form onSubmit={savePreferences}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={autoCommit}
              onChange={(e) => setAutoCommit(e.target.checked)}
            />
            <span>Auto-commit y auto-push habilitados (git-autopilot)</span>
          </label>
          <div className="row" style={{ marginTop: "0.5rem" }}>
            <div>
              <label htmlFor="pref-remote">Nombre del remoto predeterminado</label>
              <input
                id="pref-remote"
                value={remoteName}
                onChange={(e) => setRemoteName(e.target.value)}
                maxLength={50}
              />
            </div>
            <div>
              <label htmlFor="pref-vis">Visibilidad por defecto</label>
              <select id="pref-vis" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
                <option value="private">Privado</option>
                <option value="public">Público</option>
              </select>
            </div>
          </div>
          <div style={{ marginTop: "0.75rem" }}>
            <button type="submit" className="btn-primary btn-sm">
              Guardar Preferencias
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function GitSettings() {
  return (
    <Suspense
      fallback={
        <div className="card" style={{ textAlign: "center", padding: "3rem" }}>
          <div className="spinner" style={{ marginBottom: "1rem" }} />
          <p className="muted">Cargando configuración Git…</p>
        </div>
      }
    >
      <GitSettingsInner />
    </Suspense>
  );
}
