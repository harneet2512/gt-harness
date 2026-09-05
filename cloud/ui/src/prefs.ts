/* ------------------------------------------------------------------ *
 * What the reader chose once and should never have to choose again.
 *
 * The landing page is a prompt, not a form: the model, the ground-truth
 * mode and the two per-turn budgets live behind a gear and are remembered
 * in `localStorage` under `synapse:prefs`. Everything here is pure except
 * the two functions that actually touch storage, and those degrade to the
 * defaults wherever storage is missing or refuses to answer.
 * ------------------------------------------------------------------ */

import { isGtMode, WALL_SECONDS_MAX, WALL_SECONDS_MIN, type GtMode } from "./api";

export const PREFS_KEY = "synapse:prefs";
/** Per-session "is the graph panel open", keyed by session id. */
export const GRAPH_KEY = "synapse:graph";

/** The models the picker offers by name. Anything else is free text. */
export const MODELS = [
  "nvidia/nemotron-3-super-120b-a12b:free",
  "google/gemma-4-31b-it:free",
  "minimax/minimax-m3:free",
  "deepseek/deepseek-v4-flash",
] as const;

export const STEP_LIMIT_MIN = 1;
export const STEP_LIMIT_MAX = 500;

export interface Prefs {
  model: string;
  gtMode: GtMode;
  /** Model calls per turn. */
  stepLimit: number;
  /** Wall-clock seconds per turn; null means "whatever the server defaults to". */
  wallSeconds: number | null;
}

export const DEFAULT_PREFS: Prefs = {
  model: MODELS[0],
  gtMode: "advisory",
  stepLimit: 60,
  wallSeconds: null,
};

function clampInt(
  value: unknown,
  min: number,
  max: number,
  fallback: number,
): number {
  const n =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number.parseInt(value, 10)
        : Number.NaN;
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(n)));
}

/**
 * A stored blob is data from an older build of this page — it is merged onto
 * the defaults field by field rather than trusted, so one stale key cannot
 * take the whole prompt down with it.
 */
export function normalizePrefs(raw: unknown): Prefs {
  if (typeof raw !== "object" || raw === null) return DEFAULT_PREFS;
  const rec = raw as Record<string, unknown>;

  const model =
    typeof rec.model === "string" && rec.model.trim() !== ""
      ? rec.model.trim()
      : DEFAULT_PREFS.model;

  const gtMode: GtMode =
    typeof rec.gtMode === "string" && isGtMode(rec.gtMode)
      ? rec.gtMode
      : DEFAULT_PREFS.gtMode;

  const stepLimit = clampInt(
    rec.stepLimit,
    STEP_LIMIT_MIN,
    STEP_LIMIT_MAX,
    DEFAULT_PREFS.stepLimit,
  );

  /* Null is a real value here — "let the server decide" — so it survives,
     and anything unparseable falls back to it rather than to a number the
     server never chose. */
  const wallSeconds =
    rec.wallSeconds === null || rec.wallSeconds === undefined || rec.wallSeconds === ""
      ? null
      : clampInt(rec.wallSeconds, WALL_SECONDS_MIN, WALL_SECONDS_MAX, -1);

  return {
    model,
    gtMode,
    stepLimit,
    wallSeconds: wallSeconds === -1 ? null : wallSeconds,
  };
}

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    /* Storage disabled by policy: the defaults are still a working prompt. */
    return null;
  }
}

export function loadPrefs(): Prefs {
  const store = storage();
  if (!store) return DEFAULT_PREFS;
  try {
    const raw = store.getItem(PREFS_KEY);
    return raw ? normalizePrefs(JSON.parse(raw)) : DEFAULT_PREFS;
  } catch {
    return DEFAULT_PREFS;
  }
}

export function savePrefs(prefs: Prefs): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* Quota or private mode — the session still runs with what is in memory. */
  }
}

/* ------------------------------------------------------------------ *
 * The graph panel's open/closed state, per session.
 * ------------------------------------------------------------------ */

/** Merge one session's choice into the stored map. Pure, so it is testable. */
export function withGraphOpen(
  raw: unknown,
  sessionId: string,
  open: boolean,
): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  if (typeof raw === "object" && raw !== null) {
    for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
      if (typeof value === "boolean") out[key] = value;
    }
  }
  out[sessionId] = open;
  return out;
}

/** The remembered choice, or null where this session has never said. */
export function readGraphOpen(raw: unknown, sessionId: string): boolean | null {
  if (typeof raw !== "object" || raw === null) return null;
  const value = (raw as Record<string, unknown>)[sessionId];
  return typeof value === "boolean" ? value : null;
}

export function loadGraphOpen(sessionId: string): boolean | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(GRAPH_KEY);
    return raw ? readGraphOpen(JSON.parse(raw), sessionId) : null;
  } catch {
    return null;
  }
}

export function saveGraphOpen(sessionId: string, open: boolean): void {
  const store = storage();
  if (!store) return;
  try {
    const raw = store.getItem(GRAPH_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    store.setItem(GRAPH_KEY, JSON.stringify(withGraphOpen(parsed, sessionId, open)));
  } catch {
    /* nothing to remember with — the panel still toggles for this visit */
  }
}
