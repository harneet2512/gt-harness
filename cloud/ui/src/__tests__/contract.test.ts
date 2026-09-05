import { describe, expect, it } from "vitest";
import {
  CAP_REASONS,
  capLabel,
  EVENT_TYPES,
  GT_MODE_HELP,
  GT_MODES,
  isGtMode,
  lifecycleToSessionStatus,
  streamUrl,
  WALL_SECONDS_MAX,
  WALL_SECONDS_MIN,
} from "../api";
import {
  COMPOSER_MAX_ROWS,
  costUntracked,
  exitNote,
  failedReason,
  lockedRows,
  formatDuration,
  gtBadgeLabel,
  receiptWall,
  sessionClosedBlurb,
  sessionClosedLabel,
  sessionTotals,
  turnOutcomeNote,
} from "../format";
import {
  isOverlayMode,
  layoutModeFor,
  NARROW_MAX_PX,
  STACK_MAX_PX,
} from "../layoutMode";
import { receipt, session } from "./helpers";

/* ------------------------------------------------------------------ *
 * Layout — the same two numbers the media queries use.
 * ------------------------------------------------------------------ */
describe("layoutMode", () => {
  it("puts each of the QA widths in the mode it was designed for", () => {
    expect(layoutModeFor(1440)).toBe("wide");
    expect(layoutModeFor(1100)).toBe("wide");
    expect(layoutModeFor(1099)).toBe("narrow");
    expect(layoutModeFor(900)).toBe("narrow");
    expect(layoutModeFor(760)).toBe("narrow");
    expect(layoutModeFor(759)).toBe("stacked");
    expect(layoutModeFor(700)).toBe("stacked");
  });

  it("agrees with its own breakpoints exactly at the boundary", () => {
    expect(layoutModeFor(NARROW_MAX_PX)).toBe("wide");
    expect(layoutModeFor(NARROW_MAX_PX - 1)).toBe("narrow");
    expect(layoutModeFor(STACK_MAX_PX)).toBe("narrow");
    expect(layoutModeFor(STACK_MAX_PX - 1)).toBe("stacked");
  });

  it("falls back to the full layout when there is no width to read", () => {
    expect(layoutModeFor(0)).toBe("wide");
    expect(layoutModeFor(Number.NaN)).toBe("wide");
  });

  it("knows which modes float their panels", () => {
    expect(isOverlayMode("wide")).toBe(false);
    expect(isOverlayMode("narrow")).toBe(true);
    expect(isOverlayMode("stacked")).toBe(true);
  });

});

/* ------------------------------------------------------------------ *
 * Closed sessions.
 * ------------------------------------------------------------------ */
describe("closed sessions", () => {
  it("says why a session is closed", () => {
    expect(sessionClosedLabel("closed", "expired")).toBe("closed · expired");
    expect(sessionClosedLabel("closed", "user")).toBe("closed · by you");
    expect(sessionClosedLabel("closed", "failed")).toBe("closed · failed");
  });

  it("still says closed when the server gave no reason", () => {
    expect(sessionClosedLabel("closed", null)).toBe("closed");
    expect(sessionClosedLabel("closed", undefined)).toBe("closed");
    expect(sessionClosedLabel("closed", "something_new")).toBe("closed");
  });

  it("calls a failed session failed, whatever the reason field says", () => {
    expect(sessionClosedLabel("failed", null)).toBe("failed");
    expect(sessionClosedLabel("failed", "expired")).toBe("failed");
  });

  it("says nothing at all about an open session", () => {
    for (const status of ["idle", "running", "creating"]) {
      expect(sessionClosedLabel(status, null)).toBeNull();
      expect(sessionClosedBlurb(status, null)).toBeNull();
    }
  });

  it("offers a sentence for the banner as well as a chip", () => {
    expect(sessionClosedBlurb("closed", "expired")).toMatch(/expired/);
    expect(sessionClosedBlurb("closed", "user")).toMatch(/You closed/);
    expect(sessionClosedBlurb("failed", null)).toBe("This session failed.");
  });

  it("maps a lifecycle frame onto the status it settles the session at", () => {
    expect(lifecycleToSessionStatus("closed")).toBe("closed");
    expect(lifecycleToSessionStatus("failed")).toBe("failed");
    // Progress phases are not a settled status.
    expect(lifecycleToSessionStatus("indexing")).toBeNull();
    expect(lifecycleToSessionStatus("stopped")).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Caps: out of steps and out of time read the same way.
 * ------------------------------------------------------------------ */
describe("finish reasons", () => {
  it("treats a time limit exactly as a step limit", () => {
    expect(CAP_REASONS.has("step_limit")).toBe(true);
    expect(CAP_REASONS.has("time_limit")).toBe(true);
    expect(CAP_REASONS.has("reply")).toBe(false);
    expect(CAP_REASONS.has("stopped")).toBe(false);
    expect(CAP_REASONS.has("error")).toBe(false);
  });

  it("names the cap the reader ran into", () => {
    expect(capLabel("time_limit")).toBe("time budget");
    expect(capLabel("step_limit")).toBe("step limit");
  });
});

/* ------------------------------------------------------------------ *
 * Wall clock and cost.
 * ------------------------------------------------------------------ */
describe("wall totals and cost", () => {
  it("reads a session total as steps, wall clock and cost", () => {
    expect(
      sessionTotals(session({ steps: 12, cost: 0, total_wall_seconds: 250 })),
    ).toBe("12 steps · 4m 10s · $0.000");
    expect(
      sessionTotals(session({ steps: 1, cost: 0.125, total_wall_seconds: 9 })),
    ).toBe("1 step · 9s · $0.125");
  });

  it("leaves out a total the server has not reported", () => {
    expect(sessionTotals(session({ steps: 0, cost: 0 }))).toBe("$0.000");
    expect(
      sessionTotals(session({ steps: 3, cost: 0, total_wall_seconds: null })),
    ).toBe("3 steps · $0.000");
  });

  it("prefers the server's wall_seconds over the clock arithmetic", () => {
    expect(receiptWall(receipt({ wall_seconds: 250 }))).toBe("4m 10s");
    // started_at/finished_at say 60s; wall_seconds is the authority.
    expect(receiptWall(receipt({ started_at: 100, finished_at: 160 }))).toBe(
      "1m 00s",
    );
    expect(
      receiptWall(receipt({ started_at: null, finished_at: null })),
    ).toBe("—");
  });

  it("treats a zero wall_seconds as unreported, not as instant", () => {
    // The column is NOT NULL DEFAULT 0.0, so rows written before the server
    // measured turns report 0 for a minute of work.
    expect(
      receiptWall(receipt({ wall_seconds: 0, started_at: 100, finished_at: 160 })),
    ).toBe("1m 00s");
    expect(
      receiptWall(
        receipt({ wall_seconds: 0, started_at: null, finished_at: null }),
      ),
    ).toBe("—");
  });

  it("keeps the per-turn time budget inside the bounds the server enforces", () => {
    expect(WALL_SECONDS_MIN).toBe(60);
    expect(WALL_SECONDS_MAX).toBe(3600);
    expect(WALL_SECONDS_MIN).toBeLessThan(WALL_SECONDS_MAX);
  });

  it("calls the cost column untracked when every turn cost exactly zero", () => {
    expect(costUntracked([0, 0, 0])).toBe(true);
    expect(costUntracked([0, 0.04])).toBe(false);
    expect(costUntracked([0.04])).toBe(false);
    // Nothing to conclude from nothing.
    expect(costUntracked([])).toBe(false);
    expect(costUntracked([null, undefined])).toBe(false);
    // A null among zeros is still a run nobody was charged for.
    expect(costUntracked([0, null])).toBe(true);
  });

  it("formats durations the way both totals read them", () => {
    expect(formatDuration(9)).toBe("9s");
    expect(formatDuration(250)).toBe("4m 10s");
    expect(formatDuration(3725)).toBe("1h 02m");
    expect(formatDuration(-5)).toBe("0s");
  });
});

/* ------------------------------------------------------------------ *
 * HAR-84 G-02 — `engine` was never a GTMode. Every engine session raised
 * `ValueError: 'engine' is not a valid GTMode` on its first turn, and the
 * UI was the thing offering it.
 * ------------------------------------------------------------------ */
describe("gt modes", () => {
  it("offers exactly the four modes the server accepts", () => {
    expect([...GT_MODES]).toEqual(["off", "advisory", "assistive", "enforced"]);
  });

  it("does not know the mode that never existed", () => {
    expect(isGtMode("engine")).toBe(false);
    expect(isGtMode("banana")).toBe(false);
    expect(isGtMode("")).toBe(false);
    for (const mode of GT_MODES) expect(isGtMode(mode)).toBe(true);
  });

  it("has one line of help for every mode, and no empty ones", () => {
    for (const mode of GT_MODES) {
      expect(GT_MODE_HELP[mode].length).toBeGreaterThan(0);
    }
    expect(GT_MODE_HELP.off).toBe("no GroundTruth");
    expect(GT_MODE_HELP.enforced).toMatch(/fail-closed/);
  });

  it("names the mode in the header badge, not just the index status", () => {
    expect(gtBadgeLabel("advisory", "ready")).toBe("GT: advisory");
    expect(gtBadgeLabel("assistive", "ready")).toBe("GT: assistive");
    expect(gtBadgeLabel("enforced", "pending")).toBe("GT: enforced · indexing…");
    expect(gtBadgeLabel("advisory", "unavailable")).toBe(
      "GT: advisory · unavailable",
    );
  });

  it("says only `off` when no ground truth was asked for", () => {
    expect(gtBadgeLabel("off", "off")).toBe("GT: off");
    expect(gtBadgeLabel("", "off")).toBe("GT: off");
    expect(gtBadgeLabel("off", "ready")).toBe("GT: off");
  });
});

/* ------------------------------------------------------------------ *
 * HAR-84 G-08 / G-04 / G-20 — the words for the three failures the UI
 * previously rendered as silence.
 * ------------------------------------------------------------------ */
describe("failure wording", () => {
  it("treats an interrupted turn as neither a cap nor a failure", () => {
    expect(CAP_REASONS.has("interrupted")).toBe(false);
    expect(turnOutcomeNote("interrupted")).toBe(
      "interrupted by a server restart",
    );
  });

  it("ends a card that has no reply to end it", () => {
    expect(turnOutcomeNote("error")).toBe("the turn failed");
    expect(turnOutcomeNote("stopped")).toBe("stopped");
  });

  it("says nothing where the reply speaks for the turn", () => {
    for (const reason of ["reply", "question", "step_limit", "time_limit", null]) {
      expect(turnOutcomeNote(reason)).toBeNull();
    }
    expect(turnOutcomeNote(undefined)).toBeNull();
  });

  it("surfaces the server's reason for a failed session", () => {
    expect(failedReason("BadRequestError: not a valid model ID")).toBe(
      "This session failed: BadRequestError: not a valid model ID",
    );
    expect(failedReason(null)).toBe("This session failed.");
    expect(failedReason("   ")).toBe("This session failed.");
    expect(failedReason(undefined)).toBe("This session failed.");
  });

  it("clips a reason the composer cannot hold, and says it clipped it", () => {
    const long = failedReason("x".repeat(400));
    expect(long.length).toBeLessThan(180);
    expect(long.endsWith("…")).toBe(true);
  });

  it("gives the locked composer the rows its reason needs, and no more", () => {
    expect(lockedRows("")).toBe(1);
    expect(lockedRows("This session is closed.")).toBe(1);
    expect(lockedRows(failedReason("x".repeat(400)))).toBe(COMPOSER_MAX_ROWS);
    expect(lockedRows("y".repeat(4000))).toBe(COMPOSER_MAX_ROWS);
  });

  it("explains the exit codes the sandbox reports with no output at all", () => {
    expect(exitNote(137)).toMatch(/memory limit/);
    expect(exitNote(124)).toMatch(/timed out/);
    expect(exitNote(128)).toMatch(/could not run/);
    expect(exitNote(143)).toBe("terminated");
    // An ordinary failing command explains itself.
    expect(exitNote(1)).toBeNull();
    expect(exitNote(0)).toBeNull();
    expect(exitNote(null)).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * The wire: the new frame, the new phase, and a resume point that can
 * never be malformed (HAR-84 G-17).
 * ------------------------------------------------------------------ */
describe("event contract", () => {
  it("subscribes to the server-written note as a first-class frame", () => {
    expect(EVENT_TYPES).toContain("system_note");
    expect(EVENT_TYPES).toContain("turn_finished");
  });

  it("treats a sandbox restart as a phase, not a session status", () => {
    expect(lifecycleToSessionStatus("sandbox_restarted")).toBeNull();
  });

  it("never puts a non-integer after_id on the stream URL", () => {
    expect(streamUrl("s1", 0)).toBe("/api/sessions/s1/events");
    expect(streamUrl("s1", 42)).toBe("/api/sessions/s1/events?after_id=42");
    expect(streamUrl("s1", 42.7)).toBe("/api/sessions/s1/events?after_id=42");
    expect(streamUrl("s1", -3)).toBe("/api/sessions/s1/events");
    expect(streamUrl("s1", Number.NaN)).toBe("/api/sessions/s1/events");
    expect(streamUrl("a/b", 1)).toBe("/api/sessions/a%2Fb/events?after_id=1");
  });
});
