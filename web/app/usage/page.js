"use client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
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
  const [loading, setLoading] = useState(false);

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
    setLoading(true);
    api(`/v1/projects/${projectId}/usage?days=${days}`)
      .then((res) => {
        setData(res);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
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
  const totalReqs = (t?.requests_ok || 0) + (t?.requests_error || 0);
  const hitRate = totalReqs > 0 ? Math.round(((t?.cache_hits || 0) / totalReqs) * 100) : 0;
  const successRate = totalReqs > 0 ? Math.round(((t?.requests_ok || 0) / totalReqs) * 100) : 100;

  return (
    <div>
      {/* Header */}
      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.75rem" }}>
          <div>
            <h1>Métricas y Consumo</h1>
            <p style={{ margin: 0 }}>
              Telemetría agregada de consultas a modelos, aciertos de caché, volumen de tokens y costes estimados USD según <code>model_catalog</code>.
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              type="button"
              className="btn-secondary btn-sm"
              onClick={exportCsv}
              disabled={!data?.daily?.length}
            >
              ⬇ Exportar CSV
            </button>
          </div>
        </div>
      </div>

      {/* Alertas de Claves si existen */}
      {projectAlerts.length > 0 && (
        <div className="card" style={{ background: "var(--rose-tint)", borderColor: "var(--rose-500)", marginBottom: "1.25rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h4 style={{ color: "var(--rose-500)", margin: 0 }}>
              ⚠ Alertas de claves activas ({projectAlerts.length})
            </h4>
            <Link href="/projects" className="btn btn-danger btn-sm">
              Revisar claves en Proyectos →
            </Link>
          </div>
          <ul style={{ margin: "0.5rem 0 0", paddingLeft: "1.2rem", fontSize: "0.85rem", color: "var(--text-primary)" }}>
            {projectAlerts.map((a) => (
              <li key={a.key_id}>{a.message}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Selector de Proyecto y Días */}
      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <div className="row" style={{ marginTop: 0, alignItems: "center" }}>
          <div style={{ flex: "2 1 260px" }}>
            <label htmlFor="usage-proj-select">Proyecto</label>
            <select
              id="usage-proj-select"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              disabled={projects.length === 0}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div style={{ flex: "1 1 140px" }}>
            <label htmlFor="usage-days-select">Ventana de tiempo</label>
            <select
              id="usage-days-select"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              <option value={7}>Últimos 7 días</option>
              <option value={14}>Últimos 14 días</option>
              <option value={30}>Últimos 30 días</option>
              <option value={90}>Últimos 90 días</option>
            </select>
          </div>
        </div>
        {error && <div className="err" style={{ marginTop: "0.5rem" }}>{error}</div>}
      </div>

      {loading && (
        <div className="card" style={{ textAlign: "center", padding: "2rem" }}>
          <div className="spinner" style={{ marginBottom: "0.5rem" }} />
          <p className="muted">Cargando métricas del proyecto…</p>
        </div>
      )}

      {/* KPI Cards Grid */}
      {!loading && t && (
        <div className="grid-4" style={{ marginBottom: "1.5rem" }}>
          <div className="card" style={{ margin: 0 }}>
            <small className="muted" style={{ textTransform: "uppercase", fontSize: "0.72rem", fontWeight: 600 }}>
              Peticiones Totales
            </small>
            <div style={{ fontSize: "1.6rem", fontWeight: 700, margin: "0.2rem 0", color: "var(--text-primary)" }}>
              {totalReqs.toLocaleString()}
            </div>
            <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
              <span className="badge badge-emerald" style={{ fontSize: "0.72rem" }}>
                {t.requests_ok} OK
              </span>
              {t.requests_error > 0 && (
                <span className="badge badge-rose" style={{ fontSize: "0.72rem" }}>
                  {t.requests_error} err
                </span>
              )}
              <span className="muted" style={{ fontSize: "0.75rem", marginLeft: "auto" }}>
                {successRate}% éxito
              </span>
            </div>
          </div>

          <div className="card" style={{ margin: 0 }}>
            <small className="muted" style={{ textTransform: "uppercase", fontSize: "0.72rem", fontWeight: 600 }}>
              Aciertos de Caché
            </small>
            <div style={{ fontSize: "1.6rem", fontWeight: 700, margin: "0.2rem 0", color: "var(--petrol-400)" }}>
              {t.cache_hits.toLocaleString()}
            </div>
            <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
              <span className="badge badge-petrol" style={{ fontSize: "0.72rem" }}>
                {hitRate}% Hit Ratio
              </span>
              <span className="muted" style={{ fontSize: "0.75rem" }}>
                Ahorro de latencia
              </span>
            </div>
          </div>

          <div className="card" style={{ margin: 0 }}>
            <small className="muted" style={{ textTransform: "uppercase", fontSize: "0.72rem", fontWeight: 600 }}>
              Tokens Totales
            </small>
            <div style={{ fontSize: "1.6rem", fontWeight: 700, margin: "0.2rem 0", color: "var(--accent-cyan)" }}>
              {(t.input_tokens + t.output_tokens).toLocaleString()}
            </div>
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
              {t.input_tokens.toLocaleString()} in / {t.output_tokens.toLocaleString()} out
            </div>
          </div>

          <div className="card" style={{ margin: 0 }}>
            <small className="muted" style={{ textTransform: "uppercase", fontSize: "0.72rem", fontWeight: 600 }}>
              Coste Estimado USD
            </small>
            <div style={{ fontSize: "1.6rem", fontWeight: 700, margin: "0.2rem 0", color: "var(--amber-500)" }}>
              ${t.estimated_cost_usd.toFixed(4)}
            </div>
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
              Tarifas según catálogo
            </div>
          </div>
        </div>
      )}

      {/* Gráficos diarios */}
      {!loading && dailyAsc.length > 0 && (
        <div className="grid-2">
          <div className="card" style={{ margin: 0 }}>
            <div className="card-header">
              <h3 className="card-title">Peticiones OK por Día</h3>
              <span className="badge badge-petrol">Volumen</span>
            </div>
            <div className="usage-chart" aria-hidden="true">
              {dailyAsc.map((d) => (
                <div key={d.day} className="usage-bar-wrap" title={`${d.day}: ${d.requests_ok} peticiones OK`}>
                  <div
                    className="usage-bar"
                    style={{ height: `${Math.max(4, Math.round((d.requests_ok / maxReq) * 100))}%` }}
                  />
                  <small>{d.day.slice(5)}</small>
                </div>
              ))}
            </div>
          </div>

          <div className="card" style={{ margin: 0 }}>
            <div className="card-header">
              <h3 className="card-title">Tokens (In + Out) por Día</h3>
              <span className="badge badge-emerald">Consumo</span>
            </div>
            <div className="usage-chart" aria-hidden="true">
              {dailyAsc.map((d) => {
                const tok = d.input_tokens + d.output_tokens;
                return (
                  <div key={`tok-${d.day}`} className="usage-bar-wrap" title={`${d.day}: ${tok.toLocaleString()} tokens`}>
                    <div
                      className="usage-bar usage-bar-alt"
                      style={{ height: `${Math.max(4, Math.round((tok / maxTok) * 100))}%` }}
                    />
                    <small>{d.day.slice(5)}</small>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Tabla detallada de consumo */}
      {!loading && data?.daily?.length > 0 && (
        <div className="card" style={{ marginTop: "1.5rem" }}>
          <div className="card-header">
            <h3 className="card-title">Detalle Diario de Consultas</h3>
            <span className="badge badge-slate">{data.daily.length} registros</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Peticiones OK</th>
                  <th>Errores</th>
                  <th>Caché Hits</th>
                  <th>Tokens (In / Out)</th>
                  <th>Coste Estimado</th>
                </tr>
              </thead>
              <tbody>
                {data.daily.map((d) => (
                  <tr key={d.day}>
                    <td><strong>{d.day}</strong></td>
                    <td><span className="badge badge-emerald">{d.requests_ok}</span></td>
                    <td>{d.requests_error > 0 ? <span className="badge badge-rose">{d.requests_error}</span> : "0"}</td>
                    <td>{d.cache_hits > 0 ? <span className="badge badge-petrol">{d.cache_hits}</span> : "0"}</td>
                    <td>{d.input_tokens.toLocaleString()} / {d.output_tokens.toLocaleString()}</td>
                    <td>${d.estimated_cost_usd.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!loading && (!data?.daily || data.daily.length === 0) && (
        <div className="card empty-state">
          <div className="empty-state-icon">📊</div>
          <h3>Sin actividad registrada</h3>
          <p className="muted">
            Este proyecto aún no tiene registros de uso en el período seleccionado. Realiza consultas desde el <Link href="/playground">Playground</Link> o vía API para ver estadísticas.
          </p>
        </div>
      )}
    </div>
  );
}
