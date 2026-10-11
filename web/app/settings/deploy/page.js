"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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

  if (!status && loading) return <main><p>Cargando…</p></main>;

  return (
    <main className="narrow">
      <h1>Despliegue y acceso</h1>
      <p>
        <a href="/settings/git">Config. Git</a>
      </p>
      {error && <p className="error">{error}</p>}
      {msg && <p className="ok">{msg}</p>}

      {!status?.repo_accessible && (
        <p className="warn">
          El repo no está montado en la API: solo lectura de variables de entorno. En Docker monta <code>/repo</code> como en
          <code>docker-compose.yml</code>.
        </p>
      )}

      <section>
        <h2>URL pública</h2>
        <p>
          Enlaces de recuperación de contraseña y cookies seguras. Si guardas aquí una URL <code>https://</code>, la API
          usará cookies <code>Secure</code> aunque <code>ASKBOX_COOKIE_SECURE</code> en el contenedor siga en false hasta que
          reinicies con el <code>.env</code> alineado.
        </p>
        {status && (
          <ul>
            <li>Entorno (contenedor): <code>{status.env_public_url}</code></li>
            <li>Efectiva ahora: <code>{status.effective_public_url}</code></li>
            <li>Cookie Secure efectiva: <code>{String(status.effective_cookie_secure)}</code></li>
          </ul>
        )}
        <form onSubmit={savePreferences}>
          <label>
            public_url
            <input
              type="url"
              value={publicUrl}
              onChange={(e) => setPublicUrl(e.target.value)}
              placeholder="http://localhost:8067 o https://tu-dominio"
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={tunnelEnabled}
              onChange={(e) => setTunnelEnabled(e.target.checked)}
            />
            Uso Cloudflare Tunnel (informativo)
          </label>
          <button type="submit" disabled={!status?.repo_accessible}>Guardar preferencias</button>
        </form>
      </section>

      <section>
        <h2>Cloudflare Tunnel (automático)</h2>
        <p>
          Clon por defecto: <strong>sin</strong> túnel — accede por LAN en el puerto <code>8067</code>.
          Para exponer por HTTPS con un clic: token de API Cloudflare con permisos
          <strong> Account → Cloudflare Tunnel → Edit</strong> y <strong>Zone → DNS → Edit</strong> en tu dominio,
          más el hostname público (p. ej. <code>askbox.tudominio.com</code>).
        </p>
        {status && (
          <ul>
            <li>Tunnel ID: <code>{status.tunnel_id || "—"}</code></li>
            <li>Hostname: <code>{status.tunnel_hostname || "—"}</code></li>
            <li>cloudflared en ejecución: <code>{status.cloudflared_running ? "sí" : "no"}</code></li>
            <li>
              Token API CF (BD):{" "}
              {status.cloudflare_api_token_configured
                ? `···${status.cloudflare_api_token_last4}`
                : "no configurado"}
            </li>
            <li>
              Token connector (BD):{" "}
              {status.tunnel_token_configured ? `···${status.tunnel_token_last4}` : "no configurado"}
            </li>
          </ul>
        )}
        <form onSubmit={saveCfApiToken}>
          <label>
            API token Cloudflare (se guarda cifrado; no se vuelve a mostrar)
            <input
              type="password"
              value={cfApiToken}
              onChange={(e) => setCfApiToken(e.target.value)}
              autoComplete="off"
              placeholder="Opcional si ya guardaste uno"
            />
          </label>
          <button type="submit">Guardar token API</button>
          {status?.cloudflare_api_token_configured && (
            <button type="button" className="secondary" onClick={deleteCfApiToken}>Borrar token API</button>
          )}
        </form>
        <form onSubmit={provisionTunnel}>
          <label>
            Hostname deseado
            <input
              type="text"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder="askbox.tudominio.com"
              required
            />
          </label>
          <p className="hint">{status?.provision_hint}</p>
          <button type="submit" disabled={!status?.repo_accessible || provisionLoading}>
            {provisionLoading ? "Provisionando…" : "Crear / actualizar túnel"}
          </button>
        </form>
        <p>{status?.env_hint}</p>
        <pre>{status?.compose_hint || "docker compose --profile tunnel up -d cloudflared"}</pre>
      </section>

      <section>
        <h2>Token del connector (manual)</h2>
        <p>Solo si creaste el túnel en el panel de Zero Trust y quieres guardar el connector sin provision automático.</p>
        <form onSubmit={saveConnectorToken}>
          <label>
            Token del connector
            <input
              type="password"
              value={connectorTokenInput}
              onChange={(e) => setConnectorTokenInput(e.target.value)}
              autoComplete="off"
            />
          </label>
          <button type="submit">Guardar token en BD</button>
          {status?.tunnel_token_configured && (
            <button type="button" className="secondary" onClick={deleteConnectorToken}>Borrar token de BD</button>
          )}
        </form>
      </section>
    </main>
  );
}
