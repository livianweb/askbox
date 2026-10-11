"use client";
import { useState } from "react";
import Link from "next/link";
import { api } from "../../lib/api";
import BrandLogo from "../BrandLogo";

export default function Forgot() {
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState("");
  const [devUrl, setDevUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setMsg("");
    setDevUrl("");
    try {
      const r = await api("/v1/auth/forgot", { method: "POST", body: { email } });
      setMsg(r.message || "Enlace de restablecimiento generado.");
      if (r.reset_url) setDevUrl(r.reset_url); // solo si el servidor corre en modo desarrollo
    } catch (err) {
      setError(err.status === 429 ? "Demasiados intentos seguidos; espera un minuto antes de reintentar." : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-container">
      <form className="card" onSubmit={submit}>
        <div style={{ textAlign: "center", marginBottom: "1.25rem" }}>
          <BrandLogo size={48} className="auth-brand-logo" />
          <h2>Recuperar Contraseña</h2>
          <p className="muted" style={{ margin: 0 }}>
            Introduce tu email registrado para recibir el enlace de restablecimiento.
          </p>
        </div>

        <label htmlFor="forgot-email">Correo Electrónico</label>
        <input
          id="forgot-email"
          type="email"
          placeholder="tu@correo.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="username"
        />

        {error && <div className="err">{error}</div>}
        {msg && <div className="ok">{msg}</div>}

        {devUrl && (
          <div className="info-box">
            <strong>Modo desarrollo / servidor local:</strong>
            <div style={{ marginTop: "0.25rem" }}>
              <a href={devUrl}>Abrir enlace directo de restablecimiento →</a>
            </div>
          </div>
        )}

        <div style={{ marginTop: "1rem" }}>
          <button type="submit" className="btn-primary" style={{ width: "100%" }} disabled={busy}>
            {busy ? "Generando enlace…" : "Enviar enlace de recuperación →"}
          </button>
        </div>

        <div style={{ marginTop: "1.25rem", textAlign: "center", fontSize: "0.86rem", borderTop: "1px solid var(--border-subtle)", paddingTop: "1rem" }}>
          <Link href="/login" style={{ marginRight: "1rem" }}>Volver a entrar</Link>
          <span className="muted">·</span>
          <Link href="/register" style={{ marginLeft: "1rem" }}>Crear cuenta</Link>
        </div>

        <div style={{ marginTop: "1rem", fontSize: "0.78rem", color: "var(--text-muted)", lineHeight: 1.4 }}>
          💡 <strong>Nota del sistema:</strong> Si el servidor no tiene configurado un servicio SMTP saliente, el enlace de recuperación se registra de forma segura en los logs de la API (<code>docker compose logs api</code>).
        </div>
      </form>
    </div>
  );
}
