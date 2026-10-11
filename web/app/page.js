"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "../lib/api";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    api("/v1/auth/me")
      .then(() => router.replace("/playground"))
      .catch(() => router.replace("/login"));
  }, [router]);
  return <p className="muted">Cargando…</p>;
}
