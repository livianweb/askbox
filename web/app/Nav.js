"use client";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api } from "../lib/api";

export default function Nav() {
  const [me, setMe] = useState(null);
  const path = usePathname();
  const router = useRouter();

  useEffect(() => {
    api("/v1/auth/me").then(setMe).catch(() => setMe(null));
  }, [path]);

  async function logout() {
    await api("/v1/auth/logout", { method: "POST" }).catch(() => {});
    setMe(null);
    router.push("/login");
  }

  return (
    <nav>
      <strong>askbox</strong>
      {me && (
        <>
          <a href="/projects">Proyectos y claves</a>
          <a href="/playground">Playground</a>
          <a href="/usage">Uso</a>
          <a href="/settings/deploy">Despliegue</a>
          <a href="/settings/git">Config. Git</a>
        </>
      )}
      <span className="spacer" />
      {me ? (
        <>
          <small>{me.email}</small>
          <button onClick={logout}>Salir</button>
        </>
      ) : (
        <a href="/login">Entrar</a>
      )}
    </nav>
  );
}
