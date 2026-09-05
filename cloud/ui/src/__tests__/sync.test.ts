import { describe, expect, it } from "vitest";
import { gtErrorOf, nextSession, shouldApply } from "../sessionSync";
import { createIngest, ingestFrame, isTerminal } from "../streamSync";
import { ev, frame, session } from "./helpers";

/* ------------------------------------------------------------------ *
 * The snapshot ordering guard — HAR-84 round 1, P0-1. Two GET /sessions
 * responses in flight, the older one still saying "running": applied last
 * it wedged the header on Working forever.
 * ------------------------------------------------------------------ */
describe("sessionSync — applying snapshots in issue order", () => {
  it("applies a response newer than the last one applied", () => {
    expect(shouldApply(3, 4)).toBe(true);
  });

  it("re-applies the newest response rather than dropping it", () => {
    expect(shouldApply(4, 4)).toBe(true);
  });

  it("discards a response that was issued before the one on screen", () => {
    expect(shouldApply(4, 3)).toBe(false);
    expect(shouldApply(9, 1)).toBe(false);
  });

  it("takes the snapshot when nothing contradicts it", () => {
    const prev = session({ status: "running", current_turn_id: "t1" });
    const next = session({ status: "idle", current_turn_id: null });
    expect(nextSession(prev, next, new Set())).toBe(next);
  });

  it("discards a snapshot still naming a turn the stream has ended", () => {
    const prev = session({ status: "idle", current_turn_id: null });
    const stale = session({ status: "running", current_turn_id: "t1" });
    expect(nextSession(prev, stale, new Set(["t1"]))).toBe(prev);
  });

  it("keeps a snapshot naming a turn that is still running", () => {
    const prev = session({ status: "idle" });
    const next = session({ status: "running", current_turn_id: "t2" });
    expect(nextSession(prev, next, new Set(["t1"]))).toBe(next);
  });

  it("has nothing to defend when there is no session on screen yet", () => {
    const next = session({ status: "running", current_turn_id: "t1" });
    expect(nextSession(null, next, new Set(["t1"]))).toBe(next);
  });
});

/* The `gt_unavailable` frame fires once, at index time. Reopen the session
   later and only the session row still knows why. */
describe("sessionSync — why GT is unavailable", () => {
  it("falls back to the session row while the stream has said nothing", () => {
    expect(gtErrorOf(undefined, "clone exceeded the index budget")).toBe(
      "clone exceeded the index budget",
    );
    expect(gtErrorOf(undefined, null)).toBeNull();
    expect(gtErrorOf(undefined, undefined)).toBeNull();
  });

  it("lets a live frame overrule the row it is newer than", () => {
    expect(gtErrorOf("indexer crashed", "stale reason")).toBe("indexer crashed");
    // gt_ready clears it, even though the row still carries the old reason.
    expect(gtErrorOf(null, "stale reason")).toBeNull();
  });

  it("treats an empty reason on either side as no reason", () => {
    expect(gtErrorOf("", "row reason")).toBeNull();
    expect(gtErrorOf(undefined, "")).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Frame ingest: de-duplication, the resume point, and what ends a stream.
 * ------------------------------------------------------------------ */
describe("streamSync — ingesting frames", () => {
  it("parses a frame and remembers its id", () => {
    const state = createIngest();
    const { event, terminal } = ingestFrame(
      state,
      frame(7, "assistant", { turn_id: "t1", content: "hi" }),
    );
    expect(event).toMatchObject({ id: 7, type: "assistant" });
    expect(terminal).toBe(false);
    expect(state.lastEventId).toBe(7);
  });

  it("drops a duplicate id rather than rendering it twice", () => {
    const state = createIngest();
    expect(ingestFrame(state, frame(3, "assistant", { turn_id: "t" })).event)
      .not.toBeNull();
    expect(ingestFrame(state, frame(3, "assistant", { turn_id: "t" })).event)
      .toBeNull();
    expect(state.seen.size).toBe(1);
  });

  it("resumes from the highest id seen, not the last one", () => {
    const state = createIngest();
    ingestFrame(state, frame(9, "assistant", { turn_id: "t" }));
    ingestFrame(state, frame(4, "assistant", { turn_id: "t" }));
    expect(state.lastEventId).toBe(9);
  });

  it("drops a malformed payload", () => {
    const state = createIngest();
    expect(ingestFrame(state, "not json").event).toBeNull();
    expect(ingestFrame(state, null).event).toBeNull();
    expect(ingestFrame(state, "").event).toBeNull();
    expect(ingestFrame(state, undefined).event).toBeNull();
    expect(state.lastEventId).toBe(0);
  });

  it("gives an id-less frame a synthetic negative id it never resumes from", () => {
    const state = createIngest();
    const a = ingestFrame(state, JSON.stringify({ type: "assistant", data: {} }));
    const b = ingestFrame(state, JSON.stringify({ type: "assistant", data: {} }));
    expect(a.event!.id).toBe(-1);
    expect(b.event!.id).toBe(-2);
    expect(state.lastEventId).toBe(0);
  });

  it("keeps an unknown frame type as `unknown` rather than dropping it", () => {
    const state = createIngest();
    const { event } = ingestFrame(state, frame(1, "brand_new_frame"));
    expect(event!.type).toBe("unknown");
  });

  it("ends the stream on lifecycle closed and failed", () => {
    const state = createIngest();
    expect(
      ingestFrame(state, frame(1, "lifecycle", { status: "closed", reason: "expired" }))
        .terminal,
    ).toBe(true);
    expect(
      ingestFrame(state, frame(2, "lifecycle", { status: "failed" })).terminal,
    ).toBe(true);
  });

  it("does not end the stream on a lifecycle phase or an idle session", () => {
    const state = createIngest();
    expect(ingestFrame(state, frame(1, "lifecycle", { status: "idle" })).terminal)
      .toBe(false);
    expect(
      ingestFrame(state, frame(2, "lifecycle", { status: "indexing" })).terminal,
    ).toBe(false);
    expect(
      ingestFrame(state, frame(3, "lifecycle", { status: "stopped" })).terminal,
    ).toBe(false);
  });

  it("handles agent_error as an ordinary frame — the run continues", () => {
    const state = createIngest();
    const { event, terminal } = ingestFrame(
      state,
      frame(5, "agent_error", { turn_id: "t1", error: "boom" }),
    );
    expect(event).toMatchObject({ type: "agent_error" });
    expect(event!.data).toMatchObject({ error: "boom" });
    expect(terminal).toBe(false);
  });

  it("recognises a terminal frame on its own", () => {
    expect(isTerminal(ev(1, "lifecycle", { status: "closed" }))).toBe(true);
    expect(isTerminal(ev(1, "lifecycle", { status: "running" }))).toBe(false);
    expect(isTerminal(ev(1, "agent_error", { error: "x" }))).toBe(false);
  });
});
