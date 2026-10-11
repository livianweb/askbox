export const THEME_COOKIE_NAME = "askbox-theme";
/** @deprecated Migración desde localStorage; no escribir de nuevo. */
export const THEME_STORAGE_KEY = "askbox-theme";

const COOKIE_MAX_AGE_SEC = 60 * 60 * 24 * 400; // ~400 días

export function getSystemTheme() {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** @returns {"light" | "dark" | null} null = sin preferencia explícita (seguir sistema) */
export function getThemeCookie() {
  if (typeof document === "undefined") return null;
  const parts = document.cookie.split(";");
  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed.startsWith(`${THEME_COOKIE_NAME}=`)) continue;
    const value = decodeURIComponent(trimmed.slice(THEME_COOKIE_NAME.length + 1));
    if (value === "light" || value === "dark") return value;
    return null;
  }
  return null;
}

/** Tema aplicado en pantalla: cookie explícita o preferencia del sistema. */
export function getResolvedTheme() {
  const fromCookie = getThemeCookie();
  if (fromCookie) return fromCookie;
  return getSystemTheme();
}

/** @param {"light" | "dark"} resolved */
export function applyResolvedTheme(resolved) {
  document.documentElement.setAttribute("data-theme", resolved);
}

/** @param {"light" | "dark"} theme */
export function setThemeCookie(theme) {
  const secure = typeof window !== "undefined" && window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${THEME_COOKIE_NAME}=${encodeURIComponent(theme)}; path=/; max-age=${COOKIE_MAX_AGE_SEC}; SameSite=Lax${secure}`;
  try {
    localStorage.removeItem(THEME_STORAGE_KEY);
  } catch {
    /* ignore */
  }
  applyResolvedTheme(theme);
}

/** Una vez: localStorage antiguo → cookie si era light/dark. */
export function migrateLegacyThemeStorage() {
  if (typeof window === "undefined") return;
  if (getThemeCookie()) {
    try {
      localStorage.removeItem(THEME_STORAGE_KEY);
    } catch {
      /* ignore */
    }
    return;
  }
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (raw === "light" || raw === "dark") {
      setThemeCookie(raw);
    } else if (raw === "system") {
      localStorage.removeItem(THEME_STORAGE_KEY);
    }
  } catch {
    /* ignore */
  }
}

export const THEME_INIT_SCRIPT = `(function(){try{var n='askbox-theme';var t=null;var p=document.cookie.split(';');for(var i=0;i<p.length;i++){var s=p[i].trim();if(s.indexOf(n+'=')===0){var v=decodeURIComponent(s.slice(n.length+1));if(v==='light'||v==='dark')t=v;break;}}if(!t){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`;
