export const THEME_STORAGE_KEY = "askbox-theme";

/** @typedef {"light" | "dark" | "system"} ThemePreference */

export function getSystemTheme() {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** @returns {ThemePreference} */
export function getStoredPreference() {
  if (typeof window === "undefined") return "system";
  const raw = localStorage.getItem(THEME_STORAGE_KEY);
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return "system";
}

/** @param {ThemePreference} preference */
export function resolveTheme(preference) {
  if (preference === "light" || preference === "dark") return preference;
  return getSystemTheme();
}

/** @param {"light" | "dark"} resolved */
export function applyResolvedTheme(resolved) {
  document.documentElement.setAttribute("data-theme", resolved);
}

/** @param {ThemePreference} preference */
export function setThemePreference(preference) {
  localStorage.setItem(THEME_STORAGE_KEY, preference);
  applyResolvedTheme(resolveTheme(preference));
}

export const THEME_INIT_SCRIPT = `(function(){try{var k='askbox-theme';var s=localStorage.getItem(k);var t;if(s==='light'||s==='dark'){t=s;}else{t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`;
