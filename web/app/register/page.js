"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "../../lib/api";
import BrandLogo from "../BrandLogo";

export default function Register() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [monoUserInstance, setMonoUserInstance] = useState(null);

  useEffect(() => {
    api("/v1/auth/registration-info")
      .then((data) => setMonoUserInstance(data.allow_register === false))
      .catch(() => setMonoUserInstance(true));
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
      await api("/v1/auth/register", {
        method: "POST",
        body: { email, password, password_confirm: confirm },
      });
      setPassword("");
      setConfirm("");
      router.push("/playground");
    } catch (err) {
      setError(
        err.code === "registration_closed"
          ? "El registro está cerrado: esta instancia de askbox ya tiene su cuenta de administrador configurada. Si es tu cuenta, entra o recupera la contraseña."
          : err.message,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-container">
      <form className="card" onSubmit={submit}>
        <div style={{ textAlign: "center", marginBottom: "1.25rem" }}>
          <BrandLogo size={144} className="auth-brand-logo" />
          <h2>Crear Cuenta</h2>
          <p className="muted" style={{ margin: 0 }}>
            Configura el acceso a tu pasarela askbox
          </p>
        </div>

        <label htmlFor="reg-email">Correo Electrónico</label>
        <input
          id="reg-email"
          type="email"
          placeholder="tu@correo.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="username"
        />

        <label htmlFor="reg-password">Contraseña (mínimo 8 caracteres)</label>
        <input
          id="reg-password"
          type="password"
          placeholder="••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          maxLength={256}
          autoComplete="new-password"
        />

        <label htmlFor="reg-confirm">Confirmar Contraseña</label>
        <input
          id="reg-confirm"
          type="password"
          placeholder="••••••••"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
          minLength={8}
          maxLength={256}
          autoComplete="new-password"
        />

        {error && <div className="err">{error}</div>}

        <div style={{ marginTop: "1rem" }}>
          <button type="submit" className="btn-primary" style={{ width: "100%" }} disabled={busy}>
            {busy ? "Registrando…" : "Crear Cuenta →"}
          </button>
        </div>

        <div style={{ marginTop: "1.25rem", textAlign: "center", fontSize: "0.86rem", borderTop: "1px solid var(--border-subtle)", paddingTop: "1rem" }}>
          <Link href="/login" style={{ marginRight: "1rem" }}>Ya tengo cuenta</Link>
          <span className="muted">·</span>
          <Link href="/forgot" style={{ marginLeft: "1rem" }}>¿Olvidaste tu contraseña?</Link>
        </div>

        {monoUserInstance === true && (
          <div style={{ marginTop: "1rem", fontSize: "0.78rem", color: "var(--text-muted)", lineHeight: 1.4 }}>
            🔒 <strong>Instancia mono-usuario:</strong> Por diseño de seguridad, el registro solo permite crear el primer usuario administrador inicial.
          </div>
        )}
      </form>
    </div>
  );
}
