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
  const [tokenInput, setTokenInput] = useState("");
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

  async function saveToken(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    try {
      const res = await api("/v1/settings/deploy/tunnel-token", { method: "PUT", body: { token: tokenInput } });
      setTokenInput("");
      setMsg(res.note || "Token guardado (cifrado).");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function deleteToken() {
    setError("");
    setMsg("");
    try {
      await api("/v1/settings/deploy/tunnel-token", { method: "DELETE" });
      setMsg("Token eliminado de la base de datos.");
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
            Uso Cloudflare Tunnel (informativo; ver instrucciones abajo)
          </label>
          <button type="submit" disabled={!status?.repo_accessible}>Guardar preferencias</button>
        </form>
      </section>

      <section>
        <h2>Cloudflare Tunnel (opcional)</h2>
        <p>
          Clon por defecto: <strong>sin</strong> túnel — accede por LAN en el puerto <code>8067</code>.
          Con túnel propio: crea un tunnel en Zero Trust, copia el token a <code>.env</code> como
          <code>CLOUDFLARE_TUNNEL_TOKEN</code> y arranca:
        </p>
        <pre>{status?.compose_hint || "docker compose --profile tunnel up -d"}</pre>
        <p>{status?.env_hint}</p>
        <p>
          Token en BD (opcional, cifrado): solo metadatos en la API
          {status?.tunnel_token_configured
            ? ` (···${status.tunnel_token_last4})`
            : " (no configurado)"}.
        </p>
        <form onSubmit={saveToken}>
          <label>
            Token del connector (se guarda cifrado; no se vuelve a mostrar)
            <input
              type="password"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              autoComplete="off"
            />
          </label>
          <button type="submit">Guardar token en BD</button>
          {status?.tunnel_token_configured && (
            <button type="button" className="secondary" onClick={deleteToken}>Borrar token de BD</button>
          )}
        </form>
      </section>
    </main>
  );
}
