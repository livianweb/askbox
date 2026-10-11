"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";

export default function UsagePage() {
  const router = useRouter();
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/v1/projects")
      .then((p) => {
        setProjects(p);
        if (p[0]) setProjectId(p[0].id);
      })
      .catch((e) => (e.status === 401 ? router.replace("/login") : setError(e.message)));
  }, [router]);

  useEffect(() => {
    if (!projectId) return;
    api(`/v1/projects/${projectId}/usage?days=30`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [projectId]);

  const t = data?.totals;

  return (
    <div className="card">
      <h2>Uso del proyecto</h2>
      <p className="muted">Agregado diario (últimos 30 días). Los costes son estimados según <code>model_catalog</code>.</p>
      <label>
        Proyecto
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      </label>
      {error && <p className="err">{error}</p>}
      {t && (
        <ul>
          <li>Peticiones OK: {t.requests_ok} · errores: {t.requests_error}</li>
          <li>Caché: {t.cache_hits} hits</li>
          <li>Tokens: {t.input_tokens} in / {t.output_tokens} out</li>
          <li>Coste estimado (USD): {t.estimated_cost_usd.toFixed(4)}</li>
        </ul>
      )}
      {data?.daily?.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Día</th><th>OK</th><th>Err</th><th>Caché</th><th>Tokens</th><th>USD</th>
            </tr>
          </thead>
          <tbody>
            {data.daily.map((d) => (
              <tr key={d.day}>
                <td>{d.day}</td>
                <td>{d.requests_ok}</td>
                <td>{d.requests_error}</td>
                <td>{d.cache_hits}</td>
                <td>{d.input_tokens}/{d.output_tokens}</td>
                <td>{d.estimated_cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
