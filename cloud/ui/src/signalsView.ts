/* ------------------------------------------------------------------ *
 * What a view needs to know about signals that `signals.ts` does not
 * have to say.
 *
 * Two things live here, both pure, both shared by the flat canvas and the
 * 3D one so the two can never drift:
 *
 *   · `walkStep`, the rule for turning a trail that grew into departures.
 *     A trail that *shrank* is a replay — a reload, a scrub — and replays
 *     never animate;
 *   · `ArrivalWatch`, which notices that a signal is no longer in the air
 *     and reports where it landed.
 *
 * The second one exists because arrival is information: it is the moment
 * an agent reached that file. The 3D view answers it with a brief flare
 * on the destination, and that flare has to be caused by the hop actually
 * finishing — never by a clock. So it is derived from the director's own
 * output rather than from a timer, and an idle session produces none of
 * it at all, because an idle session has nothing in the air.
 * ------------------------------------------------------------------ */

import type { LiveSignal } from "./signals";

/* ------------------------------------------------------------------ *
 * Departures
 * ------------------------------------------------------------------ */

/** What one agent's queue last knew about its own trail. */
export interface Walked {
  /** Identity of the walk. A change means a different turn, or a scrub. */
  token: string;
  length: number;
}

export type WalkAction =
  /** Throw away whatever is queued: this is a replay, not progress. */
  | { kind: "reset" }
  /** Animate the hops from `from` to `length - 1`. */
  | { kind: "hops"; from: number }
  | { kind: "none" };

/**
 * What to do with a trail that has just been recomputed.
 *
 * Only growth animates, and only when the caller says it should: a turn
 * being replayed, a card rebuilt from history, or a scrub backwards must
 * arrive on the map already drawn rather than re-enacting minutes of the
 * past over the present.
 */
export function walkStep(
  before: Walked,
  token: string,
  length: number,
  animate: boolean,
): WalkAction {
  if (before.token !== token || length < before.length) return { kind: "reset" };
  if (length > before.length && animate) {
    /* Index 0 has no hop before it: a trail's first waypoint is where the
       agent started, not somewhere it travelled to. */
    return { kind: "hops", from: Math.max(1, before.length) };
  }
  return { kind: "none" };
}

/* ------------------------------------------------------------------ *
 * Arrivals
 * ------------------------------------------------------------------ */

/** A hop that has just finished, and the particle it finished on. */
export interface Arrival {
  /** The particle the signal reached. */
  id: string;
  /** Whose it was, as `r, g, b`. */
  rgb: string;
  /** When it landed, on the render loop's clock. */
  at: number;
}

interface Flight {
  n: number;
  to: string;
  rgb: string;
}

/**
 * Which hops finished this frame.
 *
 * The director hands back everything in the air, in one reused buffer. A
 * hop that was in that buffer last frame and is not in it now has arrived
 * — the queue drops a signal exactly when its age reaches 1 — so watching
 * the buffer shrink is watching agents land, with no second clock and no
 * change to the scheduling that owns the first one.
 *
 * Counted rather than merely tracked, because the same agent may be sent
 * along the same filament twice before the first one gets there, and two
 * hops that both land are two arrivals.
 */
export class ArrivalWatch {
  private held = new Map<string, Flight>();
  private next = new Map<string, Flight>();
  private out: Arrival[] = [];
  private pool: Arrival[] = [];

  /** Drop everything: a turn switch, a scrub, or reduced motion coming on. */
  clear(): void {
    this.held.clear();
    this.next.clear();
    this.out.length = 0;
  }

  get watching(): number {
    return this.held.size;
  }

  /**
   * Advance to `now` and return what landed. The array is reused, so it
   * is valid until the next call.
   *
   * Under reduced motion nothing arrives: a still signal is already a
   * highlight that appears and fades on the filament it crossed, and a
   * second flare at the far end would be one more thing moving on a
   * screen that asked for none.
   */
  observe(
    signals: readonly LiveSignal[],
    now: number,
    reduced = false,
  ): readonly Arrival[] {
    this.out.length = 0;
    if (reduced) {
      this.held.clear();
      return this.out;
    }

    const next = this.next;
    next.clear();
    for (let i = 0; i < signals.length; i += 1) {
      const signal = signals[i];
      const key = `${signal.agentId}\u0000${signal.from}\u0000${signal.to}`;
      const seen = next.get(key);
      if (seen) seen.n += 1;
      else next.set(key, { n: 1, to: signal.to, rgb: signal.rgb });
    }

    for (const [key, flight] of this.held) {
      const still = next.get(key);
      const landed = flight.n - (still ? still.n : 0);
      for (let i = 0; i < landed; i += 1) this.out.push(this.fill(flight, now));
    }

    /* Swap rather than copy: the hot loop must not allocate a map a
       frame. `held` becomes the scratch for the next call. */
    this.next = this.held;
    this.held = next;
    return this.out;
  }

  private fill(flight: Flight, now: number): Arrival {
    const index = this.out.length;
    let arrival = this.pool[index];
    if (!arrival) {
      arrival = { id: "", rgb: "", at: 0 };
      this.pool[index] = arrival;
    }
    arrival.id = flight.to;
    arrival.rgb = flight.rgb;
    arrival.at = now;
    return arrival;
  }
}

/* ------------------------------------------------------------------ *
 * The flare an arrival leaves
 * ------------------------------------------------------------------ */

/**
 * How long the destination stays lit. Shorter than the hop that caused it
 * (`SIGNAL_MS` is 420) so the flare reads as the *end* of the hop rather
 * than as a second event of its own.
 */
export const FLARE_MS = 340;
/** How far past the particle the flare expands, in particle radii. */
export const FLARE_REACH = 1.9;

/** 1 the instant it lands, 0 once it is over. Linear: it is 340ms. */
export function flareAt(landedAt: number, now: number): number {
  const age = (now - landedAt) / FLARE_MS;
  if (age < 0 || age >= 1) return 0;
  return 1 - age;
}
