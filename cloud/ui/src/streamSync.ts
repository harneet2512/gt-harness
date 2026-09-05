/* ------------------------------------------------------------------ *
 * Ingesting SSE frames.
 *
 * The socket drops and reconnects; a reconnect replays from `after_id`,
 * and a server that answers a fraction late replays a frame the page has
 * already rendered. Frames are therefore de-duplicated by envelope id,
 * the highest id seen is what the next reconnect resumes from, and a
 * lifecycle frame that ends the session ends the stream with it.
 *
 * Kept out of the hook so it can be tested without a browser.
 * ------------------------------------------------------------------ */

import { parseEventFrame, TERMINAL_STATUSES, type SessionEvent } from "./api";

/**
 * What the ingest has seen. Deliberately mutable: the hook holds one of
 * these in a ref for the life of the stream, exactly as it held the three
 * separate refs this replaced.
 */
export interface IngestState {
  /** Highest real envelope id seen; the `after_id` a reconnect resumes at. */
  lastEventId: number;
  /** Every real envelope id seen, so a replay is dropped rather than shown. */
  seen: Set<number>;
  /** Counter behind the synthetic negative ids given to id-less frames. */
  synthId: number;
}

export function createIngest(): IngestState {
  return { lastEventId: 0, seen: new Set(), synthId: 0 };
}

export interface IngestResult {
  /** The frame to hand on, or null when it was malformed or a duplicate. */
  event: SessionEvent | null;
  /** True once the server will send nothing more on this stream. */
  terminal: boolean;
}

const DROPPED: IngestResult = { event: null, terminal: false };

/**
 * Fold one raw `data:` payload into `state`. Mutates `state` and returns
 * what the caller should do with the frame.
 */
export function ingestFrame(state: IngestState, raw: unknown): IngestResult {
  const event = parseEventFrame(raw);
  if (!event) return DROPPED;

  if (Number.isFinite(event.id)) {
    if (state.seen.has(event.id)) return DROPPED;
    state.seen.add(event.id);
    if (event.id > state.lastEventId) state.lastEventId = event.id;
  } else {
    // Server omitted an id; keep it renderable with a synthetic key. Negative
    // so it can never collide with a real one, and never resumed from.
    event.id = -++state.synthId;
  }

  return { event, terminal: isTerminal(event) };
}

/**
 * True for the frame that ends the run. Only `lifecycle` closes the stream:
 * `agent_error` reports a turn that failed, not a session that did, and the
 * agent is expected to keep going after it.
 */
export function isTerminal(event: SessionEvent): boolean {
  return (
    event.type === "lifecycle" &&
    TERMINAL_STATUSES.has(String(event.data.status))
  );
}
