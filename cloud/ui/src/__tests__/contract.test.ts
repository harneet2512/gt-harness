import { describe, expect, it } from "vitest";
import {
  CAP_REASONS,
  capLabel,
  lifecycleToSessionStatus,
  WALL_SECONDS_MAX,
  WALL_SECONDS_MIN,
} from "../api";
import {
  costUntracked,
  formatDuration,
  receiptWall,
  sessionClosedBlurb,
  sessionClosedLabel,
  sessionTotals,
} from "../format";
import {
  defaultDrawerOpen,
  defaultPanelCollapsed,
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

  it("hides the drawer on a narrow session page and shows it with no graph", () => {
    expect(defaultDrawerOpen("narrow", true)).toBe(false);
    expect(defaultDrawerOpen("narrow", false)).toBe(true);
    // Stacked puts the conversation back in flow: it is always there.
    expect(defaultDrawerOpen("stacked", true)).toBe(true);
    expect(defaultDrawerOpen("wide", true)).toBe(true);
  });

  it("starts the bottom panel collapsed on anything but the full layout", () => {
    expect(defaultPanelCollapsed("wide")).toBe(false);
    expect(defaultPanelCollapsed("narrow")).toBe(true);
    expect(defaultPanelCollapsed("stacked")).toBe(true);
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
