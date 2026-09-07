/* ------------------------------------------------------------------ *
 * The canvas palette.
 *
 * Every colour the page paints is a custom property on :root, so the two
 * themes are one attribute. The canvas cannot read a CSS variable, so it
 * reads them once here and again whenever the theme changes — never per
 * frame, which is the whole reason this is a module and not a hook.
 * ------------------------------------------------------------------ */

export interface Palette {
  paper: string;
  grid: string;
  ink: string;
  ink2: string;
  /** `r, g, b`, the form the painter interpolates alpha into. */
  accent: string;
  change: string;
}

/**
 * The dark terminal, hard-coded. It is what the canvas paints before the
 * document has a computed style to read — a test environment, the first
 * frame — and it is the theme the app opens in.
 */
export const DEFAULT_PALETTE: Palette = {
  paper: "#0f1113",
  grid: "#1a1d21",
  ink: "#e6e6e6",
  ink2: "#8b8f97",
  accent: "217, 119, 87",
  change: "86, 182, 194",
};

let current: Palette = DEFAULT_PALETTE;

/**
 * The canvas paints on demand, not on a clock: after a theme change nothing
 * would ask it to repaint, and it would sit in the old colours until the
 * next frame something else caused. Subscribers are told instead.
 */
const listeners = new Set<() => void>();

export function palette(): Palette {
  return current;
}

/** Call `fn` whenever the palette changes. Returns the unsubscribe. */
export function onPalette(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function read(style: CSSStyleDeclaration, name: string, fallback: string): string {
  const value = style.getPropertyValue(name).trim();
  return value === "" ? fallback : value;
}

/**
 * Re-read the tokens off <html>. Call it on mount and after a theme
 * change; the next frame paints in the new colours.
 */
export function refreshPalette(): Palette {
  if (typeof window === "undefined" || typeof getComputedStyle !== "function") {
    return current;
  }
  try {
    const style = getComputedStyle(document.documentElement);
    current = {
      paper: read(style, "--paper", DEFAULT_PALETTE.paper),
      grid: read(style, "--grid", DEFAULT_PALETTE.grid),
      ink: read(style, "--ink", DEFAULT_PALETTE.ink),
      ink2: read(style, "--muted", DEFAULT_PALETTE.ink2),
      accent: read(style, "--accent-rgb", DEFAULT_PALETTE.accent),
      change: read(style, "--change-rgb", DEFAULT_PALETTE.change),
    };
  } catch {
    /* keep whatever we had */
  }
  for (const fn of listeners) fn();
  return current;
}

/* ------------------------------------------------------------------ *
 * The same colours, as numbers.
 *
 * A canvas takes `rgba(...)` strings; a shader takes floats. Parsing is
 * therefore not a 3D concern that happens to live here — it is the other
 * half of what this module already does, and keeping it here is what
 * stops the two views reading the same token differently.
 * ------------------------------------------------------------------ */

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

const BLACK: Rgb = { r: 0, g: 0, b: 0 };

/** `#0f1113` or `#abc`, to 0..1 channels. Black for anything else. */
export function hexRgb(hex: string): Rgb {
  const raw = hex.trim().replace("#", "");
  const full =
    raw.length === 3
      ? raw[0] + raw[0] + raw[1] + raw[1] + raw[2] + raw[2]
      : raw;
  if (full.length !== 6 || !/^[0-9a-fA-F]{6}$/.test(full)) return BLACK;
  const value = Number.parseInt(full, 16);
  return {
    r: ((value >> 16) & 255) / 255,
    g: ((value >> 8) & 255) / 255,
    b: (value & 255) / 255,
  };
}

/** `"217, 119, 87"` — the form the painter interpolates alpha into. */
export function listRgb(list: string): Rgb {
  const parts = list.split(",");
  if (parts.length < 3) return BLACK;
  const channel = (at: number): number => {
    const n = Number.parseFloat(parts[at]);
    return Number.isFinite(n) ? Math.min(1, Math.max(0, n / 255)) : 0;
  };
  return { r: channel(0), g: channel(1), b: channel(2) };
}
