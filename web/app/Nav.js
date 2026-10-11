"use client";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "../lib/api";

export default function Nav() {
  const [me, setMe] = useState(null);
  const [alertCount, setAlertCount] = useState(0);
  const path = usePathname();
  const router = useRouter();

  useEffect(() => {
    api("/v1/auth/me")
      .then(setMe)
      .catch(() => setMe(null));
  }, [path]);

  useEffect(() => {
    if (!me) {
      setAlertCount(0);
      return;
    }
    api("/v1/alerts")
      .then((r) => setAlertCount((r.alerts || []).length))
      .catch(() => setAlertCount(0));
  }, [me, path]);

  async function logout() {
    await api("/v1/auth/logout", { method: "POST" }).catch(() => {});
    setMe(null);
    router.push("/login");
  }

  const isCurrent = (route) => {
    if (route === "/") return path === "/";
    return path.startsWith(route);
  };

  return (
    <header className="app-header">
      <div className="nav-container">
        <Link href="/" className="brand-wrap">
          <div className="brand-logo-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
              <polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline>
              <line x1="12" y1="22.08" x2="12" y2="12"></line>
            </svg>
          </div>
          <span className="brand-text">askbox</span>
          <span className="brand-badge">Gateway LLM</span>
        </Link>

        {me ? (
          <nav className="nav-links">
            <Link href="/" className={`nav-link ${isCurrent("/") ? "active" : ""}`}>
              Inicio
            </Link>
            <Link href="/playground" className={`nav-link ${isCurrent("/playground") ? "active" : ""}`}>
              Playground
            </Link>
            <Link href="/projects" className={`nav-link ${isCurrent("/projects") ? "active" : ""}`}>
              Proyectos y claves
            </Link>
            <Link href="/usage" className={`nav-link ${isCurrent("/usage") ? "active" : ""}`}>
              Uso
              {alertCount > 0 && (
                <span className="badge badge-rose" style={{ padding: "0.1rem 0.35rem", fontSize: "0.7rem" }}>
                  {alertCount}
                </span>
              )}
            </Link>
            <Link href="/settings/deploy" className={`nav-link ${isCurrent("/settings/deploy") ? "active" : ""}`}>
              Despliegue
            </Link>
            <Link href="/settings/git" className={`nav-link ${isCurrent("/settings/git") ? "active" : ""}`}>
              Config. Git
            </Link>
            <a href="/api/docs" target="_blank" rel="noopener noreferrer" className="nav-link" title="Swagger UI REST Docs">
              API Docs ↗
            </a>
          </nav>
        ) : (
          <nav className="nav-links">
            <Link href="/login" className={`nav-link ${path === "/login" ? "active" : ""}`}>
              Entrar
            </Link>
            <Link href="/register" className={`nav-link ${path === "/register" ? "active" : ""}`}>
              Crear cuenta
            </Link>
            <a href="/api/docs" target="_blank" rel="noopener noreferrer" className="nav-link" title="Swagger UI REST Docs">
              API Docs ↗
            </a>
          </nav>
        )}

        <div className="nav-user-actions">
          {me ? (
            <>
              <span className="user-email-pill" title={me.email}>
                {me.email}
              </span>
              <button type="button" onClick={logout} className="btn-secondary btn-sm">
                Salir
              </button>
            </>
          ) : (
            <Link href="/login" className="btn-primary btn-sm">
              Acceder
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
