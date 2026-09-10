import type { Vec3 } from "./graph3d";
export const TIMING = {
  hover: 120,
  tooltip: 100,
  panel: 220,
  building: 480,
  district: 600,
  follow: 800,
  local: 900,
  neighborhood: 1500,
  repository: 2300,
} as const;
export function ease(t: number): number {
  t = Math.max(0, Math.min(1, t));
  // Invert x of cubic-bezier(.22,1,.36,1), then evaluate y.
  let lo = 0,
    hi = 1,
    u = t;
  for (let i = 0; i < 14; i++) {
    u = (lo + hi) / 2;
    const x = 3 * (1 - u) ** 2 * u * 0.22 + 3 * (1 - u) * u * u * 0.36 + u ** 3;
    if (x < t) lo = u;
    else hi = u;
  }
  return t === 0 || t === 1 ? t : 1 - (1 - u) ** 3;
}
export function travel(a: Vec3, b: Vec3, t: number): Vec3 {
  const u = ease(t),
    h = Math.min(90, 20 + Math.hypot(b.x - a.x, b.z - a.z) * 0.18);
  return {
    x: a.x + (b.x - a.x) * u,
    z: a.z + (b.z - a.z) * u,
    y: a.y + (b.y - a.y) * u + 3 * (1 - u) * u * h,
  };
}
export class CameraMotion {
  private tween: {
    from: Vec3;
    to: Vec3;
    start: number;
    duration: number;
  } | null = null;
  begin(
    from: Vec3,
    to: Vec3,
    start: number,
    duration: number,
    reduced = false,
  ) {
    this.tween = {
      from: { ...from },
      to: { ...to },
      start,
      duration: reduced ? 100 : duration,
    };
  }
  cancel() {
    this.tween = null;
  }
  get busy() {
    return this.tween !== null;
  }
  sample(now: number): Vec3 | null {
    const q = this.tween;
    if (!q) return null;
    const t = Math.min(1, Math.max(0, (now - q.start) / q.duration)),
      u = ease(t);
    if (t === 1) this.tween = null;
    return {
      x: q.from.x + (q.to.x - q.from.x) * u,
      y: q.from.y + (q.to.y - q.from.y) * u,
      z: q.from.z + (q.to.z - q.from.z) * u,
    };
  }
}
