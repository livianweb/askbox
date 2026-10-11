"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "../../lib/api";

export default function Login() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/v1/auth/login", { method: "POST", body: { email, password } });
      setPassword("");
      router.push("/playground");
    } catch (err) {
      setError(err.message || "Credenciales incorrectas.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-container">
      <form className="card" onSubmit={submit}>
        <div style={{ textAlign: "center", marginBottom: "1.25rem" }}>
          <div className="brand-logo-icon" style={{ margin: "0 auto 0.75rem", width: "42px", height: "42px" }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
              <polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline>
              <line x1="12" y1="22.08" x2="12" y2="12"></line>
            </svg>
          </div>
          <h2>Iniciar Sesión</h2>
          <p className="muted" style={{ margin: 0 }}>Accede a tu instancia unificada de askbox</p>
        </div>

        <label htmlFor="login-email">Correo Electrónico</label>
        <input
          id="login-email"
          type="email"
          placeholder="tu@correo.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="username"
        />

        <label htmlFor="login-password">Contraseña</label>
        <input
          id="login-password"
          type="password"
          placeholder="••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          maxLength={256}
          autoComplete="current-password"
        />

        {error && <div className="err">{error}</div>}

        <div style={{ marginTop: "1rem" }}>
          <button type="submit" className="btn-primary" style={{ width: "100%" }} disabled={busy}>
            {busy ? "Entrando…" : "Iniciar Sesión →"}
          </button>
        </div>

        <div style={{ marginTop: "1.25rem", textAlign: "center", fontSize: "0.86rem", borderTop: "1px solid var(--border-subtle)", paddingTop: "1rem" }}>
          <Link href="/forgot" style={{ marginRight: "1rem" }}>¿Olvidaste tu contraseña?</Link>
          <span className="muted">·</span>
          <Link href="/register" style={{ marginLeft: "1rem" }}>Crear cuenta</Link>
        </div>
      </form>
    </div>
  );
}
