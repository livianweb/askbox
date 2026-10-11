"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "../../../lib/api";

export default function DeploySettingsPage() {
  const router = useRouter();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const [publicUrl, setPublicUrl] = useState("");
  const [tunnelEnabled, setTunnelEnabled] = useState(false);
  const [hostname, setHostname] = useState("");
  const [cfApiToken, setCfApiToken] = useState("");
  const [connectorTokenInput, setConnectorTokenInput] = useState("");
  const [provisionLoading, setProvisionLoading] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api("/v1/settings/deploy/status")
      .then((s) => {
        setStatus(s);
        setLoading(false);
        if (s.preferences) {
          setPublicUrl(s.preferences.public_url || "");
          setTunnelEnabled(!!s.preferences.tunnel_enabled);
        }
        if (s.tunnel_hostname) setHostname(s.tunnel_hostname);
        else if (s.preferences?.public_url?.startsWith("https://")) {
          try {
            setHostname(new URL(s.preferences.public_url).hostname);
          } catch {
            /* ignore */
          }
        }
      })
      .catch((e) => {
        setLoading(false);
        if (e.status === 401) router.replace("/login");
        else setError(e.message);
      });
  }, [router]);

  useEffect(() => { load(); }, [load]);

  async function savePreferences(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      const res = await api("/v1/settings/deploy/preferences", {
        method: "PATCH",
        body: { public_url: publicUrl, tunnel_enabled: tunnelEnabled },
      });
      setMsg("Preferencias guardadas.");
      if (res.suggested_env) {
        setMsg(
          `${res.note} Sugerido en .env: ASKBOX_PUBLIC_URL=${res.suggested_env.ASKBOX_PUBLIC_URL}, `
          + `ASKBOX_COOKIE_SECURE=${res.suggested_env.ASKBOX_COOKIE_SECURE}`,
        );
      }
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function saveCfApiToken(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      const res = await api("/v1/settings/deploy/cloudflare-api-token", { method: "PUT", body: { token: cfApiToken } });
      setCfApiToken("");
      setMsg(res.note || "Token de API Cloudflare guardado (cifrado).");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function deleteCfApiToken() {
    setError("");
    setMsg("");
    try {
      await api("/v1/settings/deploy/cloudflare-api-token", { method: "DELETE" });
      setMsg("Token de API Cloudflare eliminado de la base de datos.");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function provisionTunnel(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setProvisionLoading(true);
    try {
      const body = { hostname };
      if (cfApiToken.trim()) body.api_token = cfApiToken.trim();
      const res = await api("/v1/settings/deploy/tunnel/provision", { method: "POST", body });
      setCfApiToken("");
      setMsg(res.note || "Túnel provisionado.");
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setProvisionLoading(false);
    }
  }

  async function saveConnectorToken(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      const res = await api("/v1/settings/deploy/tunnel-token", { method: "PUT", body: { token: connectorTokenInput } });
      setConnectorTokenInput("");
      setMsg(res.note || "Token del connector guardado (cifrado).");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function deleteConnectorToken() {
    setError("");
    setMsg("");
    try {
      await api("/v1/settings/deploy/tunnel-token", { method: "DELETE" });
      setMsg("Token del connector eliminado de la base de datos.");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  if (!status && loading) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "3rem" }}>
        <div className="spinner" style={{ marginBottom: "1rem" }} />
        <p className="muted">Cargando estado de despliegue…</p>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.75rem" }}>
          <div>
            <h1>Despliegue y Acceso</h1>
            <p style={{ margin: 0 }}>
              Configuración de la URL pública, certificados seguros y túnel Cloudflare para acceso HTTPS remoto.
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <Link href="/settings/git" className="btn btn-secondary btn-sm">
              Config. Git →
            </Link>
          </div>
        </div>
      </div>

      {error && <div className="err">{error}</div>}
      {msg && <div className="ok">{msg}</div>}

      {!status?.repo_accessible && (
        <div className="card" style={{ background: "var(--amber-tint)", borderColor: "var(--amber-500)" }}>
          <strong style={{ color: "var(--amber-500)" }}>Aviso de montaje de repositorio:</strong>
          <p style={{ margin: "0.25rem 0 0", fontSize: "0.85rem" }}>
            El repo no está montado en la API: solo lectura de variables de entorno. En Docker monta <code>/repo</code> como en <code>docker-compose.yml</code>.
          </p>
        </div>
      )}

      {/* URL Pública */}
      <div className="card">
        <div className="card-header">
          <h3 className="card-title">1. URL Pública</h3>
          <span className="badge badge-petrol">Dominio</span>
        </div>
        <p style={{ fontSize: "0.88rem" }}>
          Utilizada para generar enlaces de recuperación de contraseña y políticas de cookies seguras. Si configuras una URL <code>https://</code>, la API utilizará cookies <code>Secure</code>.
        </p>

        {status && (
          <ul className="clean-list" style={{ marginBottom: "1rem" }}>
            <li>
              <span>Entorno actual (contenedor):</span>
              <code>{status.env_public_url}</code>
            </li>
            <li>
              <span>URL efectiva en runtime:</span>
              <code>{status.effective_public_url}</code>
            </li>
            <li>
              <span>Cookie Secure efectiva:</span>
              <span className={`badge ${status.effective_cookie_secure ? "badge-emerald" : "badge-slate"}`}>
                {String(status.effective_cookie_secure)}
              </span>
            </li>
          </ul>
        )}

        <form onSubmit={savePreferences}>
          <label htmlFor="input-pub-url">URL Pública del servicio</label>
          <input
            id="input-pub-url"
            type="url"
            value={publicUrl}
            onChange={(e) => setPublicUrl(e.target.value)}
            placeholder="http://localhost:8067 o https://tu-dominio.com"
          />

          <label className="checkbox-label" style={{ marginTop: "0.5rem" }}>
            <input
              type="checkbox"
              checked={tunnelEnabled}
              onChange={(e) => setTunnelEnabled(e.target.checked)}
            />
            <span>Uso de Cloudflare Tunnel habilitado (informativo)</span>
          </label>

          <div style={{ marginTop: "0.75rem" }}>
            <button type="submit" className="btn-primary" disabled={!status?.repo_accessible}>
              Guardar Preferencias
            </button>
          </div>
        </form>
      </div>

      {/* Cloudflare Tunnel Automático */}
      <div className="card">
        <div className="card-header">
          <h3 className="card-title">2. Cloudflare Tunnel (Aprovisionamiento Automático)</h3>
          <span className="badge badge-emerald">HTTPS</span>
        </div>
        <p style={{ fontSize: "0.88rem" }}>
          Por defecto, askbox opera localmente en el puerto <code>8067</code>. Para exponer el servicio con HTTPS de forma segura mediante Cloudflare Tunnel, introduce tu token de API con permisos <strong>Account → Cloudflare Tunnel → Edit</strong> y <strong>Zone → DNS → Edit</strong>.
        </p>

        {status && (
          <ul className="clean-list" style={{ marginBottom: "1rem" }}>
            <li>
              <span>Tunnel ID:</span>
              <code>{status.tunnel_id || "No aprovisionado"}</code>
            </li>
            <li>
              <span>Hostname asignado:</span>
              <code>{status.tunnel_hostname || "—"}</code>
            </li>
            <li>
              <span>cloudflared en ejecución:</span>
              <span className={`badge ${status.cloudflared_running ? "badge-emerald" : "badge-slate"}`}>
                {status.cloudflared_running ? "Sí (Activo)" : "No"}
              </span>
            </li>
            <li>
              <span>Token API CF (BD cifrado):</span>
              <span>
                {status.cloudflare_api_token_configured
                  ? `Configurado (···${status.cloudflare_api_token_last4})`
                  : "No configurado"}
              </span>
            </li>
            <li>
              <span>Token Connector (BD):</span>
              <span>
                {status.tunnel_token_configured ? `Configurado (···${status.tunnel_token_last4})` : "No configurado"}
              </span>
            </li>
          </ul>
        )}

        <form onSubmit={saveCfApiToken} style={{ marginBottom: "1.25rem" }}>
          <label htmlFor="input-cf-token">API Token de Cloudflare (cifrado con AES-GCM)</label>
          <input
            id="input-cf-token"
            type="password"
            value={cfApiToken}
            onChange={(e) => setCfApiToken(e.target.value)}
            autoComplete="off"
            placeholder="Opcional si ya guardaste uno previamente"
          />
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
            <button type="submit" className="btn-primary btn-sm">
              Guardar Token API en BD
            </button>
            {status?.cloudflare_api_token_configured && (
              <button type="button" className="btn-danger btn-sm" onClick={deleteCfApiToken}>
                Borrar Token API
              </button>
            )}
          </div>
        </form>

        <form onSubmit={provisionTunnel}>
          <label htmlFor="input-tunnel-host">Hostname público deseado</label>
          <input
            id="input-tunnel-host"
            type="text"
            value={hostname}
            onChange={(e) => setHostname(e.target.value)}
            placeholder="askbox.tudominio.com"
            required
          />
          {status?.provision_hint && (
            <p className="muted" style={{ fontSize: "0.82rem", margin: "0.25rem 0" }}>
              {status.provision_hint}
            </p>
          )}
          <div style={{ marginTop: "0.75rem" }}>
            <button
              type="submit"
              className="btn-primary"
              disabled={!status?.repo_accessible || provisionLoading}
            >
              {provisionLoading ? "Aprovisionando túnel…" : "Crear / Actualizar Túnel →"}
            </button>
          </div>
        </form>

        {status?.env_hint && (
          <p style={{ marginTop: "1rem", fontSize: "0.85rem" }}>{status.env_hint}</p>
        )}
        <pre>{status?.compose_hint || "docker compose --profile tunnel up -d cloudflared"}</pre>
      </div>

      {/* Token del connector manual */}
      <div className="card">
        <div className="card-header">
          <h3 className="card-title">3. Token del Connector (Manual)</h3>
          <span className="badge badge-slate">Manual</span>
        </div>
        <p style={{ fontSize: "0.88rem" }}>
          Solo si creaste el túnel manualmente en el panel de Cloudflare Zero Trust y prefieres registrar el connector token sin aprovisionamiento automático.
        </p>
        <form onSubmit={saveConnectorToken}>
          <label htmlFor="input-conn-token">Token del Connector</label>
          <input
            id="input-conn-token"
            type="password"
            value={connectorTokenInput}
            onChange={(e) => setConnectorTokenInput(e.target.value)}
            autoComplete="off"
            placeholder="eyJ..."
          />
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
            <button type="submit" className="btn-primary btn-sm">
              Guardar Token Connector
            </button>
            {status?.tunnel_token_configured && (
              <button type="button" className="btn-danger btn-sm" onClick={deleteConnectorToken}>
                Borrar Token de BD
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
