import type { SessionEvent } from "./api";

/* ------------------------------------------------------------------ *
 * Narrow readers over the raw event envelopes. Per-turn activity is
 * reconstructed in `chatState`, and per-step survey data in `survey`;
 * what is left here is the session-scoped lifecycle view.
 * ------------------------------------------------------------------ */

export function field(event: SessionEvent, key: string): unknown {
  return (event.data as Record<string, unknown>)[key];
}

export function text(event: SessionEvent, key: string): string {
  const value = field(event, key);
  return typeof value === "string" ? value : "";
}

/** Lifecycle frames reporting GroundTruth state (`gt_ready`, `gt_unavailable`). */
export function gtEvents(events: readonly SessionEvent[]): SessionEvent[] {
  return events.filter(
    (event) =>
      event.type === "lifecycle" && text(event, "status").startsWith("gt_"),
  );
}
