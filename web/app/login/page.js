"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
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
      router.push("/projects");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Entrar</h2>
      <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" /></label>
      <label>Contraseña<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required maxLength={256} autoComplete="current-password" /></label>
      {error && <p className="err">{error}</p>}
      <div className="row">
        <button disabled={busy}>Entrar</button>
      </div>
      <p><a href="/forgot">¿Olvidaste tu contraseña?</a> · <a href="/register">Crear cuenta</a></p>
    </form>
  );
}
