/* ------------------------------------------------------------------ *
 * Signal: the agent's attention moving from one particle to the next.
 *
 * A step that resolved to a file follows the step before it. That hop is
 * drawn as a bright dot travelling the filament between them. When many
 * arrive at once they are queued and released one at a time, so a burst
 * of steps reads as a sequence rather than a flash.
 * ------------------------------------------------------------------ */

export const SIGNAL_MS = 420;
export const SIGNAL_GAP_MS = 140;
export const TAIL = 0.16;

/** The primary agent's own colour, as `r, g, b`. */
export const PRIMARY_RGB = "240, 102, 47";

export interface LiveSignal {
  from: string;
  to: string;
  /** 0 at the source, 1 at the target. */
  progress: number;
  /** `r, g, b` — whose trail this is. Workers each get their own. */
  rgb: string;
}

interface Queued {
  from: string;
  to: string;
}

interface Travelling extends Queued {
  startedAt: number;
}

/**
 * Not a hook and not a component: a plain object the render loop polls, so
 * an animation never costs a React render.
 */
export class SignalQueue {
  /** Every signal this queue emits is painted in this colour. */
  readonly rgb: string;

  constructor(rgb: string = PRIMARY_RGB) {
    this.rgb = rgb;
  }

  private pending: Queued[] = [];
  private travelling: Travelling[] = [];
  /** Negative infinity so the first signal leaves without waiting a gap. */
  private lastStart = Number.NEGATIVE_INFINITY;
  private live: LiveSignal[] = [];

  push(from: string, to: string): void {
    if (!from || !to || from === to) return;
    this.pending.push({ from, to });
  }

  /** Drop everything: a turn switch or a scrub replays without animating. */
  clear(): void {
    this.pending = [];
    this.travelling = [];
    this.live = [];
    this.lastStart = Number.NEGATIVE_INFINITY;
  }

  get busy(): boolean {
    return this.pending.length > 0 || this.travelling.length > 0;
  }

  /** Advance to `now` and return what is currently in flight. */
  update(now: number): readonly LiveSignal[] {
    while (
      this.pending.length > 0 &&
      now - this.lastStart >= SIGNAL_GAP_MS
    ) {
      const next = this.pending.shift();
      if (!next) break;
      this.travelling.push({ ...next, startedAt: now });
      this.lastStart = now;
    }

    if (this.travelling.length === 0) {
      if (this.live.length > 0) this.live = [];
      return this.live;
    }

    const live: LiveSignal[] = [];
    const still: Travelling[] = [];
    for (const signal of this.travelling) {
      const progress = (now - signal.startedAt) / SIGNAL_MS;
      if (progress >= 1) continue;
      still.push(signal);
      live.push({
        from: signal.from,
        to: signal.to,
        progress,
        rgb: this.rgb,
      });
    }

    this.travelling = still;
    this.live = live;
    return live;
  }
}

/* ------------------------------------------------------------------ *
 * The curve a filament — and therefore a signal — follows.
 * ------------------------------------------------------------------ */

const BOW = 0.08;

export interface Curve {
  ax: number;
  ay: number;
  cx: number;
  cy: number;
  bx: number;
  by: number;
}

/**
 * A quadratic whose control point sits 8% of the length off the midpoint,
 * perpendicular to the line. The offset always leans the same way so a
 * filament does not flip as the layout settles.
 */
export function curveOf(
  ax: number,
  ay: number,
  bx: number,
  by: number,
): Curve {
  const dx = bx - ax;
  const dy = by - ay;
  const length = Math.hypot(dx, dy) || 1;
  const offset = length * BOW;
  return {
    ax,
    ay,
    bx,
    by,
    cx: (ax + bx) / 2 + (-dy / length) * offset,
    cy: (ay + by) / 2 + (dx / length) * offset,
  };
}

export function pointOn(curve: Curve, t: number): [number, number] {
  const u = 1 - t;
  return [
    u * u * curve.ax + 2 * u * t * curve.cx + t * t * curve.bx,
    u * u * curve.ay + 2 * u * t * curve.cy + t * t * curve.by,
  ];
}
