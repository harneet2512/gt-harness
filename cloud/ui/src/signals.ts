/* ------------------------------------------------------------------ *
 * Signal: an agent's attention moving from one particle to the next.
 *
 * A step that resolved to a file follows the step before it. That hop is
 * drawn as a bright dot travelling the filament between them. When many
 * arrive at once they are queued and released one at a time, so a burst
 * of steps reads as a sequence rather than a flash.
 *
 * Every agent travels — the session's own and each worker or external
 * agent beside it — through one `SignalDirector`, which is what keeps a
 * room full of them legible: one queue per agent so the colours never
 * mix, one global release clock so two agents never fire on the same
 * frame, and one hard cap on how much may be in the air at once.
 * ------------------------------------------------------------------ */

export const SIGNAL_MS = 420;
export const SIGNAL_GAP_MS = 140;
export const TAIL = 0.16;

/** The primary agent's own colour, as `r, g, b`. */
export const PRIMARY_RGB = "240, 102, 47";

/** The id the session's own agent travels under. Never a worker id. */
export const PRIMARY_AGENT = "primary";

/**
 * How much may be in the air at once, across every agent.
 *
 * At its natural rate one agent holds three signals (`SIGNAL_MS` over
 * `SIGNAL_GAP_MS`), so four agents would put twelve dots on the map and
 * the map would read as weather. Eight is two or three each: enough that
 * every agent is plainly moving, few enough that you can still count them.
 */
export const MAX_IN_FLIGHT = 8;

/**
 * The shortest gap between two *different* agents' departures. Two frames
 * at 60fps: below that the eye reads one event rather than two, and four
 * agents that all stepped at once would strobe.
 */
export const STAGGER_MS = 32;

/** How far an agent's backlog may grow before the oldest hops are cut. */
export const MAX_PENDING = 24;

/* ---- reduced motion ------------------------------------------------ *
 * Nothing travels. A hop becomes a still highlight on the filament it
 * would have crossed, held and then gone: the same information, at rest.
 * ------------------------------------------------------------------- */

export const STILL_MS = 1100;
/** Highlights arrive slowly enough to be read one at a time. */
export const STILL_STAGGER_MS = 260;

export interface LiveSignal {
  /** Whose hop this is: `PRIMARY_AGENT`, or an agent id. */
  agentId: string;
  from: string;
  to: string;
  /** 0 at the source, 1 at the target. Pinned at 1 when still. */
  progress: number;
  /** 1 at full strength, falling to 0 as a still highlight leaves. */
  fade: number;
  /** Reduced motion: paint the filament, do not move along it. */
  still: boolean;
  /** `r, g, b` — whose trail this is. Agents each get their own. */
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
 * One agent's stream of hops.
 *
 * Not a hook and not a component: a plain object the render loop polls, so
 * an animation never costs a React render. Release is split from advance
 * (`wants`/`release` against `update`) because *when* a hop leaves is a
 * decision for the director — the only thing that can see the other
 * agents.
 */
export class SignalQueue {
  readonly agentId: string;
  /** Every signal this queue emits is painted in this colour. */
  rgb: string;

  constructor(agentId: string = PRIMARY_AGENT, rgb: string = PRIMARY_RGB) {
    this.agentId = agentId;
    this.rgb = rgb;
  }

  private pending: Queued[] = [];
  private travelling: Travelling[] = [];
  /** Negative infinity so the first signal leaves without waiting a gap. */
  private lastStart = Number.NEGATIVE_INFINITY;
  private live: LiveSignal[] = [];
  /** Reused signal objects: the hot loop allocates nothing. */
  private pool: LiveSignal[] = [];

  push(from: string, to: string): void {
    if (!from || !to || from === to) return;
    this.pending.push({ from, to });
    /* A card rebuilt from a long history can hand over hundreds of hops at
       once. Draining them all would animate minutes of the past over the
       present, so only the most recent survive. */
    if (this.pending.length > MAX_PENDING) {
      this.pending.splice(0, this.pending.length - MAX_PENDING);
    }
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

  get inFlight(): number {
    return this.travelling.length;
  }

  get waiting(): number {
    return this.pending.length;
  }

  /** True when this agent has a hop ready and its own rhythm allows it. */
  wants(now: number, gapMs = SIGNAL_GAP_MS): boolean {
    return this.pending.length > 0 && now - this.lastStart >= gapMs;
  }

  /** Send the next hop. Only the director calls this. */
  release(now: number): boolean {
    const next = this.pending.shift();
    if (!next) return false;
    this.travelling.push({ from: next.from, to: next.to, startedAt: now });
    this.lastStart = now;
    return true;
  }

  /** Advance to `now` and return what is currently in flight. */
  update(now: number, reduced = false): readonly LiveSignal[] {
    if (this.travelling.length === 0) {
      if (this.live.length > 0) this.live.length = 0;
      return this.live;
    }

    const life = reduced ? STILL_MS : SIGNAL_MS;
    this.live.length = 0;
    let kept = 0;
    for (let i = 0; i < this.travelling.length; i += 1) {
      const signal = this.travelling[i];
      const age = (now - signal.startedAt) / life;
      if (age >= 1) continue;
      this.travelling[kept] = signal;
      kept += 1;
      this.live.push(this.fill(signal, age, reduced));
    }
    this.travelling.length = kept;
    return this.live;
  }

  /** A pooled `LiveSignal`, valid until the next `update`. */
  private fill(signal: Travelling, age: number, reduced: boolean): LiveSignal {
    const index = this.live.length;
    let out = this.pool[index];
    if (!out) {
      out = {
        agentId: this.agentId,
        from: "",
        to: "",
        progress: 0,
        fade: 1,
        still: false,
        rgb: this.rgb,
      };
      this.pool[index] = out;
    }
    out.from = signal.from;
    out.to = signal.to;
    out.rgb = this.rgb;
    out.still = reduced;
    out.progress = reduced ? 1 : age;
    /* A travelling dot ends by arriving, so it needs no fade. A still
       highlight would pop, so it leaves over its last third. */
    out.fade = reduced ? Math.min(1, (1 - age) * 3) : 1;
    return out;
  }
}

export interface DirectorOptions {
  maxInFlight?: number;
  staggerMs?: number;
  gapMs?: number;
  reduced?: boolean;
}

/**
 * Every agent's signals, on one clock.
 *
 * The whole design problem is four minds at once reading as four minds
 * rather than as noise, and three rules do it:
 *
 *   · **one queue per agent**, so a hue never travels under another's name;
 *   · **one departure per tick, spaced**, so two agents never leave on the
 *     same frame and a burst reads as a sequence;
 *   · **a ceiling on what is in the air**, so a busy session gets slower
 *     rather than louder.
 *
 * Turn-taking is round-robin from a rolling cursor: an agent that stepped
 * a hundred times cannot crowd out one that stepped twice.
 */
export class SignalDirector {
  private queues = new Map<string, SignalQueue>();
  /** Insertion order, which is spawn order: the round-robin ring. */
  private ring: string[] = [];
  private cursor = 0;
  private lastRelease = Number.NEGATIVE_INFINITY;
  private out: LiveSignal[] = [];

  private readonly maxInFlight: number;
  private readonly staggerMs: number;
  private readonly gapMs: number;
  private reduced: boolean;

  constructor(options: DirectorOptions = {}) {
    this.maxInFlight = options.maxInFlight ?? MAX_IN_FLIGHT;
    this.staggerMs = options.staggerMs ?? STAGGER_MS;
    this.gapMs = options.gapMs ?? SIGNAL_GAP_MS;
    this.reduced = options.reduced ?? false;
  }

  /** Switching to or from reduced motion never leaves motion mid-flight. */
  setReduced(reduced: boolean): void {
    if (reduced === this.reduced) return;
    this.reduced = reduced;
    for (const queue of this.queues.values()) queue.clear();
  }

  get isReduced(): boolean {
    return this.reduced;
  }

  /** This agent's queue, created on first sight. Its hue may change. */
  queue(agentId: string, rgb: string = PRIMARY_RGB): SignalQueue {
    let queue = this.queues.get(agentId);
    if (!queue) {
      queue = new SignalQueue(agentId, rgb);
      this.queues.set(agentId, queue);
      this.ring.push(agentId);
    } else if (queue.rgb !== rgb) {
      queue.rgb = rgb;
    }
    return queue;
  }

  push(agentId: string, rgb: string, from: string, to: string): void {
    this.queue(agentId, rgb).push(from, to);
  }

  clear(agentId?: string): void {
    if (agentId === undefined) {
      for (const queue of this.queues.values()) queue.clear();
      return;
    }
    this.queues.get(agentId)?.clear();
  }

  /** Forget every agent not in `ids`. The primary is always kept. */
  retain(ids: Iterable<string>): void {
    const keep = new Set(ids);
    keep.add(PRIMARY_AGENT);
    for (const id of [...this.queues.keys()]) {
      if (!keep.has(id)) this.queues.delete(id);
    }
    this.ring = this.ring.filter((id) => this.queues.has(id));
    if (this.cursor >= this.ring.length) this.cursor = 0;
  }

  get agentIds(): readonly string[] {
    return this.ring;
  }

  get busy(): boolean {
    for (const queue of this.queues.values()) if (queue.busy) return true;
    return false;
  }

  get inFlight(): number {
    let n = 0;
    for (const queue of this.queues.values()) n += queue.inFlight;
    return n;
  }

  /** Advance every agent to `now` and return everything in the air. */
  update(now: number): readonly LiveSignal[] {
    this.dispatch(now);

    this.out.length = 0;
    for (const id of this.ring) {
      const queue = this.queues.get(id);
      if (!queue) continue;
      const live = queue.update(now, this.reduced);
      for (let i = 0; i < live.length; i += 1) this.out.push(live[i]);
    }
    return this.out;
  }

  /**
   * At most one departure per call, and never sooner than a stagger after
   * the last one. One per call is the point: `update` runs once a frame,
   * so two agents cannot pulse on the same frame however hard they try.
   */
  private dispatch(now: number): void {
    const stagger = this.reduced ? STILL_STAGGER_MS : this.staggerMs;
    if (now - this.lastRelease < stagger) return;
    if (this.ring.length === 0) return;
    if (this.inFlight >= this.maxInFlight) return;

    const gap = this.reduced
      ? Math.max(this.gapMs, STILL_STAGGER_MS)
      : this.gapMs;

    for (let i = 0; i < this.ring.length; i += 1) {
      const index = (this.cursor + i) % this.ring.length;
      const queue = this.queues.get(this.ring[index]);
      if (!queue || !queue.wants(now, gap)) continue;
      queue.release(now);
      this.lastRelease = now;
      this.cursor = (index + 1) % this.ring.length;
      return;
    }
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
