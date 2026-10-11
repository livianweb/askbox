"use client";

import { useCallback, useEffect, useState } from "react";
import {
  applyResolvedTheme,
  getStoredPreference,
  getSystemTheme,
  setThemePreference,
} from "../lib/theme";

const OPTIONS = [
  { value: "light", label: "Claro", title: "Tema claro" },
  { value: "dark", label: "Oscuro", title: "Tema oscuro" },
  { value: "system", label: "Sistema", title: "Seguir preferencia del sistema" },
];

export default function ThemeToggle() {
  const [preference, setPreference] = useState("system");

  useEffect(() => {
    setPreference(getStoredPreference());
  }, []);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (getStoredPreference() === "system") {
        applyResolvedTheme(getSystemTheme());
      }
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const select = useCallback((value) => {
    setThemePreference(value);
    setPreference(value);
  }, []);

  return (
    <div className="theme-toggle" role="group" aria-label="Tema de la interfaz">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={`theme-toggle-btn ${preference === opt.value ? "active" : ""}`}
          title={opt.title}
          aria-pressed={preference === opt.value}
          onClick={() => select(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
