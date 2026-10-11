"use client";
import { useState } from "react";
import { api } from "../../lib/api";

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
      setMsg(r.message);
      if (r.reset_url) setDevUrl(r.reset_url); // solo si el servidor corre en modo desarrollo
    } catch (err) {
      setError(err.status === 429 ? "Demasiados intentos; espera un minuto." : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Recuperar contraseña</h2>
      <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" /></label>
      {error && <p className="err">{error}</p>}
      {msg && <p className="ok">{msg}</p>}
      {devUrl && <p><small>Modo desarrollo: </small><a href={devUrl}>abrir enlace de restablecimiento</a></p>}
      <div className="row">
        <button disabled={busy}>Enviar enlace</button>
      </div>
      <p><a href="/login">Volver a entrar</a> · <a href="/register">Crear cuenta</a></p>
      <small>Si el servidor no tiene SMTP, el enlace aparece en los logs de la API (<code>docker compose logs api</code>).</small>
    </form>
  );
}
