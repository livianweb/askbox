"use client";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "../lib/api";
import BrandLogo from "./BrandLogo";

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
          <BrandLogo size={32} />
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
