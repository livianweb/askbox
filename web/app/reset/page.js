"use client";
import { useEffect, useState } from "react";
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
      await api("/v1/auth/reset", { method: "POST", body: { token, password, password_confirm: confirm } });
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
      <div className="card">
        <h2>Contraseña actualizada</h2>
        <p className="ok">Ya puedes entrar con tu nueva contraseña.</p>
        <p><a href="/login">Ir a entrar</a></p>
      </div>
    );
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Nueva contraseña</h2>
      {!token && <p className="err">Falta el token. Abre el enlace completo o <a href="/forgot">solicita uno nuevo</a>.</p>}
      <label>Nueva contraseña (mín. 8)<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} maxLength={256} autoComplete="new-password" /></label>
      <label>Repetir contraseña<input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required minLength={8} maxLength={256} autoComplete="new-password" /></label>
      {error && <p className="err">{error} {error.includes("caducado") && <a href="/forgot">Solicitar otro</a>}</p>}
      <div className="row">
        <button disabled={busy || !token}>Cambiar contraseña</button>
      </div>
      <p><a href="/login">Volver a entrar</a></p>
    </form>
  );
}
