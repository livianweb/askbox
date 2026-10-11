"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";

export default function Register() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (password !== confirm) {
      setError("Las contraseñas no coinciden.");
      return;
    }
    setBusy(true);
    try {
      await api("/v1/auth/register", { method: "POST", body: { email, password, password_confirm: confirm } });
      setPassword("");
      setConfirm("");
      router.push("/projects");
    } catch (err) {
      setError(err.code === "registration_closed"
        ? "El registro está cerrado: esta instancia ya tiene un usuario. Si es tu cuenta, entra o recupera la contraseña."
        : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Crear cuenta</h2>
      <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" /></label>
      <label>Contraseña (mín. 8)<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} maxLength={256} autoComplete="new-password" /></label>
      <label>Repetir contraseña<input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required minLength={8} maxLength={256} autoComplete="new-password" /></label>
      {error && <p className="err">{error}</p>}
      <div className="row">
        <button disabled={busy}>Registrar</button>
      </div>
      <p><a href="/login">Ya tengo cuenta</a> · <a href="/forgot">¿Olvidaste tu contraseña?</a></p>
      <small>Instancia mono-usuario: el registro solo funciona para el primer usuario (salvo ASKBOX_ALLOW_REGISTER=true).</small>
    </form>
  );
}
