/* ------------------------------------------------------------------ *
 * The theme.
 *
 * Two terminals: dark by default, light behind `/theme`. Everything the
 * page paints comes from the custom properties in `base.css`, so this is
 * one attribute on <html> and nothing else — no class churn, no re-render,
 * and the canvas re-reads the same variables (`palette.ts`).
 * ------------------------------------------------------------------ */

export type Theme = "dark" | "light";

export const THEMES: readonly Theme[] = ["dark", "light"];

export const THEME_KEY = "synapse:theme";

export function isTheme(value: unknown): value is Theme {
  return value === "dark" || value === "light";
}

/** The theme this browser last chose. Dark until someone says otherwise. */
export function loadTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    return isTheme(stored) ? stored : "dark";
  } catch {
    return "dark";
  }
}

export function saveTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* private window: the theme lasts as long as the tab */
  }
}

/** Put it on the document, where every rule and the canvas can see it. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
}

export function otherTheme(theme: Theme): Theme {
  return theme === "dark" ? "light" : "dark";
}

/** `/theme` with no argument toggles; `/theme light` names one. */
export function themeFromArg(arg: string, current: Theme): Theme {
  const wanted = arg.trim().toLowerCase();
  return isTheme(wanted) ? wanted : otherTheme(current);
}
