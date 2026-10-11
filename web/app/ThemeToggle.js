"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getResolvedTheme,
  migrateLegacyThemeStorage,
  setThemeCookie,
} from "../lib/theme";

function MoonIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.75" />
      <path
        d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function ThemeToggle() {
  const [resolved, setResolved] = useState("dark");

  const syncResolved = useCallback(() => {
    setResolved(getResolvedTheme());
  }, []);

  useEffect(() => {
    migrateLegacyThemeStorage();
    syncResolved();
  }, [syncResolved]);

  const toggle = useCallback(() => {
    const next = resolved === "light" ? "dark" : "light";
    setThemeCookie(next);
    setResolved(next);
  }, [resolved]);

  const isLight = resolved === "light";
  const label = isLight ? "Activar tema oscuro" : "Activar tema claro";

  return (
    <button
      type="button"
      className="theme-toggle-btn"
      onClick={toggle}
      title={label}
      aria-label={label}
    >
      {isLight ? <MoonIcon /> : <SunIcon />}
    </button>
  );
}
