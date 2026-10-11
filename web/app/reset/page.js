"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "../../lib/api";

export default function Reset() {
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setToken(new URLSearchParams(window.location.search).get("token") || "");
  }, []);

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (password !== confirm) {
      setError("Las contraseñas no coinciden.");
      return;
    }
    setBusy(true);
    try {
      await api("/v1/auth/reset", {
        method: "POST",
        body: { token, password, password_confirm: confirm },
      });
      setPassword("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="auth-container">
        <div className="card" style={{ textAlign: "center", padding: "2rem" }}>
          <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>✓</div>
          <h2>Contraseña actualizada</h2>
          <p className="ok">Tu contraseña se ha restablecido correctamente.</p>
          <div style={{ marginTop: "1.25rem" }}>
            <Link href="/login" className="btn-primary">
              Iniciar Sesión ahora →
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-container">
      <form className="card" onSubmit={submit}>
        <div style={{ textAlign: "center", marginBottom: "1.25rem" }}>
          <h2>Nueva Contraseña</h2>
          <p className="muted" style={{ margin: 0 }}>
            Establece tu nueva clave de acceso
          </p>
        </div>

        {!token && (
          <div className="err">
            Falta el token de restablecimiento. Asegúrate de abrir el enlace completo del correo o <Link href="/forgot">solicita uno nuevo</Link>.
          </div>
        )}

        <label htmlFor="reset-password">Nueva Contraseña (mínimo 8 caracteres)</label>
        <input
          id="reset-password"
          type="password"
          placeholder="••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          maxLength={256}
          autoComplete="new-password"
        />

        <label htmlFor="reset-confirm">Confirmar Nueva Contraseña</label>
        <input
          id="reset-confirm"
          type="password"
          placeholder="••••••••"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
          minLength={8}
          maxLength={256}
          autoComplete="new-password"
        />

        {error && (
          <div className="err">
            {error} {error.includes("caducado") && <Link href="/forgot">Solicitar otro</Link>}
          </div>
        )}

        <div style={{ marginTop: "1rem" }}>
          <button type="submit" className="btn-primary" style={{ width: "100%" }} disabled={busy || !token}>
            {busy ? "Actualizando…" : "Cambiar Contraseña →"}
          </button>
        </div>

        <div style={{ marginTop: "1.25rem", textAlign: "center", fontSize: "0.86rem", borderTop: "1px solid var(--border-subtle)", paddingTop: "1rem" }}>
          <Link href="/login">Volver a iniciar sesión</Link>
        </div>
      </form>
    </div>
  );
}
