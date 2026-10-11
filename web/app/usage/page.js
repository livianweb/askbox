"use client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";

function maxOf(daily, key) {
  return Math.max(1, ...daily.map((d) => d[key] || 0));
}

export default function UsagePage() {
  const router = useRouter();
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState("");
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/v1/projects")
      .then((p) => {
        setProjects(p);
        if (p[0]) setProjectId(p[0].id);
      })
      .catch((e) => (e.status === 401 ? router.replace("/login") : setError(e.message)));
    api("/v1/alerts")
      .then((r) => setAlerts(r.alerts || []))
      .catch(() => setAlerts([]));
  }, [router]);

  useEffect(() => {
    if (!projectId) return;
    api(`/v1/projects/${projectId}/usage?days=${days}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [projectId, days]);

  const t = data?.totals;
  const dailyAsc = useMemo(
    () => (data?.daily ? [...data.daily].sort((a, b) => a.day.localeCompare(b.day)) : []),
    [data],
  );
  const maxReq = maxOf(dailyAsc, "requests_ok");
  const maxTok = maxOf(dailyAsc, "input_tokens") + maxOf(dailyAsc, "output_tokens");

  function exportCsv() {
    if (!data?.daily?.length) return;
    const header = "day,requests_ok,requests_error,cache_hits,input_tokens,output_tokens,estimated_cost_usd\n";
    const rows = data.daily
      .map(
        (d) =>
          `${d.day},${d.requests_ok},${d.requests_error},${d.cache_hits},${d.input_tokens},${d.output_tokens},${d.estimated_cost_usd}`,
      )
      .join("\n");
    const blob = new Blob([header + rows], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `askbox-usage-${projectId.slice(0, 8)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const projectAlerts = alerts.filter((a) => a.project_id === projectId);

  return (
    <div className="card">
      <h2>Uso del proyecto</h2>
      <p className="muted">Agregado diario. Los costes son estimados según <code>model_catalog</code>.</p>
      {projectAlerts.length > 0 && (
        <div className="err" style={{ marginBottom: "1rem" }}>
          <strong>Alertas de claves</strong>
          <ul>
            {projectAlerts.map((a) => (
              <li key={a.key_id}>{a.message}</li>
            ))}
          </ul>
          <a href="/projects">Revisar claves en Proyectos</a>
        </div>
      )}
      <div className="row">
        <label>
          Proyecto
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
        <label>
          Días
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 14, 30, 90].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={exportCsv} disabled={!data?.daily?.length}>
          Exportar CSV
        </button>
      </div>
      {error && <p className="err">{error}</p>}
      {t && (
        <ul>
          <li>Peticiones OK: {t.requests_ok} · errores: {t.requests_error}</li>
          <li>Caché: {t.cache_hits} hits</li>
          <li>Tokens: {t.input_tokens} in / {t.output_tokens} out</li>
          <li>Coste estimado (USD): {t.estimated_cost_usd.toFixed(4)}</li>
        </ul>
      )}
      {dailyAsc.length > 0 && (
        <>
          <h3>Peticiones OK por día</h3>
          <div className="usage-chart" aria-hidden="true">
            {dailyAsc.map((d) => (
              <div key={d.day} className="usage-bar-wrap" title={`${d.day}: ${d.requests_ok} OK`}>
                <div
                  className="usage-bar"
                  style={{ height: `${Math.round((d.requests_ok / maxReq) * 100)}%` }}
                />
                <small>{d.day.slice(5)}</small>
              </div>
            ))}
          </div>
          <h3>Tokens (in+out) por día</h3>
          <div className="usage-chart" aria-hidden="true">
            {dailyAsc.map((d) => {
              const tok = d.input_tokens + d.output_tokens;
              return (
                <div key={`tok-${d.day}`} className="usage-bar-wrap" title={`${d.day}: ${tok} tokens`}>
                  <div
                    className="usage-bar usage-bar-alt"
                    style={{ height: `${Math.round((tok / maxTok) * 100)}%` }}
                  />
                </div>
              );
            })}
          </div>
        </>
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
