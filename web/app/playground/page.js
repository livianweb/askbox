"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";

export default function Playground() {
  const router = useRouter();
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState("");
  const [tier, setTier] = useState("auto");
  const [prompt, setPrompt] = useState("");
  const [maxTokens, setMaxTokens] = useState(512);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api("/v1/projects")
      .then((p) => { setProjects(p); if (p[0]) setProjectId(p[0].id); })
      .catch((e) => (e.status === 401 ? router.replace("/login") : setError(e.message)));
  }, [router]);

  async function send(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(await api("/v1/ask", { method: "POST", body: { project_id: projectId || undefined, tier, prompt, max_output_tokens: Number(maxTokens) } }));
    } catch (err) {
      setError(`${err.status || ""} ${err.code || ""}: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={send}>
      <h2>Playground</h2>
      {projects.length === 0 && <p className="muted">Crea un proyecto primero en <a href="/projects">Proyectos y claves</a>.</p>}
      <div className="row">
        <label>Proyecto
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label>Tier
          <select value={tier} onChange={(e) => setTier(e.target.value)}>
            {["auto", "fast", "balanced", "strong"].map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label>Max tokens<input type="number" min={1} max={8192} value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} /></label>
      </div>
      <label>Prompt<textarea rows={6} value={prompt} onChange={(e) => setPrompt(e.target.value)} required maxLength={20000} /></label>
      <button disabled={busy || !projectId}>{busy ? "Enviando…" : "Enviar"}</button>
      {error && <p className="err">{error}</p>}
      {result && (
        <>
          <pre>{result.text}</pre>
          <small>{result.provider} · {result.model_used} · tier {result.tier_used} · {result.latency_ms} ms{result.cache_hit ? " · caché" : ""} · {result.request_id}</small>
        </>
      )}
    </form>
  );
}
