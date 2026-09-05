/* ------------------------------------------------------------------ *
 * Reconciling session snapshots.
 *
 * Two `GET /sessions/{id}` responses are routinely in flight at once —
 * `stop()` fires one and the `turn_finished` frame fires another a moment
 * later. They can come back out of order, and the older one still says
 * `running`: applied last, it wedges the header on "Working" forever
 * (HAR-84 round 1, P0-1).
 *
 * Two rules, both pure, both tested:
 *   1. apply responses in issue order;
 *   2. discard a snapshot naming a turn the stream has already ended.
 * ------------------------------------------------------------------ */

import type { Session } from "./api";

/**
 * True when a response issued as `seq` is still the freshest one seen.
 * Equality applies: re-applying the newest snapshot is harmless, and a
 * caller that has not yet applied anything starts both at 0.
 */
export function shouldApply(appliedSeq: number, seq: number): boolean {
  return seq >= appliedSeq;
}

/**
 * Why GT is unavailable, from whichever source has spoken.
 *
 * `live` is what this page's stream has said: `undefined` while it has said
 * nothing, a string for a `gt_unavailable` frame, `null` once `gt_ready`
 * has cleared it. It outranks `row` — `Session.gt_error` is the reason as
 * of the last snapshot, and a frame is newer than any snapshot by
 * construction. Empty strings on either side count as no reason at all.
 */
export function gtErrorOf(
  live: string | null | undefined,
  row: string | null | undefined,
): string | null {
  if (live !== undefined) return live || null;
  return row || null;
}

/**
 * The session row to keep. `next` is the snapshot that just arrived;
 * `prev` is what is on screen. A snapshot still pointing at a turn the
 * stream has ended is stale by definition — the server had not committed
 * the end of that turn when it answered — so `prev` stands.
 */
export function nextSession(
  prev: Session | null,
  next: Session,
  finishedTurns: ReadonlySet<string>,
): Session {
  const turn = next.current_turn_id;
  if (prev && turn && finishedTurns.has(turn)) return prev;
  return next;
}
