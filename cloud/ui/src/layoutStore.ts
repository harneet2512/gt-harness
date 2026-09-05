/* ------------------------------------------------------------------ *
 * Layout persistence.
 *
 * A settled force layout is expensive to earn and used to be thrown away
 * by a reload, which then re-derived a *different* picture. Positions and
 * the camera are therefore written per session to localStorage, keyed by
 * file path (not by particle index) so they survive a graph refetch.
 *
 * Every access is wrapped: localStorage throws in a private window and on
 * a full quota, and a corrupted value must never take the page down.
 * ------------------------------------------------------------------ */

import type { ParticleField } from "./graph";

const LAYOUT_PREFIX = "synapse:layout:";
const CAMERA_PREFIX = "synapse:camera:";

/** `path -> [x, y]`, in simulation space. */
export type SavedLayout = Record<string, [number, number]>;

export interface SavedCamera {
  k: number;
  x: number;
  y: number;
}

function read(key: string): unknown {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? null : JSON.parse(raw);
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private window, or the quota is full: the layout is a nicety */
  }
}

/** One decimal is well under a pixel at any zoom, and halves the payload. */
function round(value: number): number {
  return Math.round(value * 10) / 10;
}

export function loadLayout(sessionId: string | null): SavedLayout | null {
  if (!sessionId) return null;
  const parsed = read(LAYOUT_PREFIX + sessionId);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return null;
  }
  const out: SavedLayout = {};
  for (const [path, at] of Object.entries(parsed as Record<string, unknown>)) {
    if (
      Array.isArray(at) &&
      typeof at[0] === "number" &&
      typeof at[1] === "number" &&
      Number.isFinite(at[0]) &&
      Number.isFinite(at[1])
    ) {
      out[path] = [at[0], at[1]];
    }
  }
  return Object.keys(out).length > 0 ? out : null;
}

export function saveLayout(
  sessionId: string | null,
  field: ParticleField,
): void {
  if (!sessionId || field.particles.length === 0) return;
  const out: SavedLayout = {};
  for (const particle of field.particles) {
    if (particle.x === undefined || particle.y === undefined) continue;
    if (!Number.isFinite(particle.x) || !Number.isFinite(particle.y)) continue;
    out[particle.path] = [round(particle.x), round(particle.y)];
  }
  if (Object.keys(out).length === 0) return;
  write(LAYOUT_PREFIX + sessionId, out);
}

/**
 * Place particles that have no position yet from the saved layout. Only
 * unplaced ones: a live simulation always outranks a stored snapshot.
 * Returns how many were placed.
 */
export function applySavedLayout(
  sessionId: string | null,
  field: ParticleField,
): number {
  const saved = loadLayout(sessionId);
  if (!saved) return 0;
  let placed = 0;
  for (const particle of field.particles) {
    if (particle.x !== undefined) continue;
    const at = saved[particle.path];
    if (!at) continue;
    particle.x = at[0];
    particle.y = at[1];
    particle.vx = 0;
    particle.vy = 0;
    placed += 1;
  }
  return placed;
}

export function loadCamera(sessionId: string | null): SavedCamera | null {
  if (!sessionId) return null;
  const parsed = read(CAMERA_PREFIX + sessionId) as Partial<SavedCamera> | null;
  if (!parsed || typeof parsed !== "object") return null;
  const { k, x, y } = parsed;
  if (
    typeof k !== "number" ||
    typeof x !== "number" ||
    typeof y !== "number" ||
    !Number.isFinite(k) ||
    !Number.isFinite(x) ||
    !Number.isFinite(y) ||
    k <= 0
  ) {
    return null;
  }
  return { k, x, y };
}

export function saveCamera(
  sessionId: string | null,
  camera: SavedCamera,
): void {
  if (!sessionId) return;
  write(CAMERA_PREFIX + sessionId, {
    k: Math.round(camera.k * 1e4) / 1e4,
    x: round(camera.x),
    y: round(camera.y),
  });
}
