import type { Receipt, Session, Timestamp } from "./api";

/** Epoch seconds, whether the server sent seconds, millis, or an ISO string. */
export function toEpochSeconds(value: Timestamp | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    // Anything past ~year 33658 in seconds is really milliseconds.
    return value > 1e12 ? value / 1000 : value;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed / 1000;
}

export function formatClock(value: Timestamp | null | undefined): string {
  const seconds = toEpochSeconds(value);
  if (seconds === null) return "—";
  return new Date(seconds * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelative(value: Timestamp | null | undefined): string {
  const seconds = toEpochSeconds(value);
  if (seconds === null) return "—";
  const delta = Date.now() / 1000 - seconds;
  if (delta < 45) return "just now";
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`;
  return `${Math.round(delta / 86400)}d ago`;
}

/** "1m 20s" / "2h 05m" — the compact form used in turn summaries. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

/** "0:07" / "1:02:33" — the ticking form used in the running header. */
export function formatStopwatch(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${ss}`;
  return `${m}:${ss}`;
}

export function formatCost(cost: number | undefined | null): string {
  if (typeof cost !== "number" || !Number.isFinite(cost)) return "$0.000";
  return `$${cost.toFixed(3)}`;
}

/** "owner/name" from a clone URL, an SSH remote, or an already-short slug. */
export function repoShort(repo: string): string {
  if (!repo) return "";
  return repo
    .replace(/^git@[^:]+:/, "")
    .replace(/^https?:\/\/[^/]+\//, "")
    .replace(/\.git$/, "");
}

export function truncate(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= max ? flat : `${flat.slice(0, max - 1)}…`;
}

export function shortSha(sha: string | null | undefined): string {
  if (!sha) return "—";
  return sha.slice(0, 10);
}

/** "820 B" / "4.1 kB" — file sizes in the graph tooltip and the inspector. */
export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size < 0) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} kB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/* ------------------------------------------------------------------ *
 * Session-level labels
 * ------------------------------------------------------------------ */

/**
 * Why a session is no longer open, in the words the reader needs: not the
 * enum, and never a bare "closed" where the server told us more.
 * Null while the session is still open.
 */
export function sessionClosedLabel(
  status: string,
  reason?: string | null,
): string | null {
  if (status === "failed") return "failed";
  if (status !== "closed") return null;
  switch (reason) {
    case "expired":
      return "closed · expired";
    case "user":
      return "closed · by you";
    case "failed":
      return "closed · failed";
    default:
      return "closed";
  }
}

/** The longer form, for the banner that offers a way out. */
export function sessionClosedBlurb(
  status: string,
  reason?: string | null,
): string | null {
  if (status === "failed") return "This session failed.";
  if (status !== "closed") return null;
  switch (reason) {
    case "expired":
      return "This session expired and its workspace was discarded.";
    case "user":
      return "You closed this session; its workspace was discarded.";
    case "failed":
      return "This session failed and was closed.";
    default:
      return "This session is closed; its workspace was discarded.";
  }
}

/**
 * The GT badge in the header: which mode was asked for, and whether the
 * index behind it actually exists. The mode is the half a reader cannot
 * get anywhere else on this page — "GT: ready" never said *ready for what*
 * (HAR-84 G-02).
 */
export function gtBadgeLabel(mode: string, status: string): string {
  const named = mode && mode !== "off" ? mode : "";
  if (!named) return "GT: off";
  switch (status) {
    case "pending":
      return `GT: ${named} · indexing…`;
    case "unavailable":
      return `GT: ${named} · unavailable`;
    case "off":
      return `GT: ${named} · off`;
    default:
      return `GT: ${named}`;
  }
}

/**
 * How a turn that ended without an answer reads on its own card. A restart
 * is not a failure and not a stop — nothing went wrong and nothing
 * finished — and the card has to say so rather than sit on "Working"
 * forever (HAR-84 G-08). Null for every reason that arrives with a reply
 * to carry it, which is where the tail caps already live.
 */
export function turnOutcomeNote(
  finishReason: string | null | undefined,
): string | null {
  switch (finishReason) {
    case "interrupted":
      return "interrupted by a server restart";
    case "error":
      return "the turn failed";
    case "stopped":
      return "stopped";
    default:
      return null;
  }
}

/**
 * Why the composer is dead on a failed session. The server knows — a bad
 * model id, a sandbox that will not start, a quota — and repeating "This
 * session failed." at someone who can read the error is a choice
 * (HAR-84 G-04).
 */
export function failedReason(error: string | null | undefined): string {
  const text = (error ?? "").trim();
  if (!text) return "This session failed.";
  return `This session failed: ${truncate(text, COMPOSER_REASON_MAX)}`;
}

/** As much of a reason as the composer can hold without clipping it. */
export const COMPOSER_REASON_MAX = 132;

/** Rows the locked composer needs to show its reason whole. */
export const COMPOSER_MAX_ROWS = 3;
const COMPOSER_COLS = 46;

export function lockedRows(reason: string): number {
  const rows = Math.ceil(reason.length / COMPOSER_COLS);
  return Math.min(COMPOSER_MAX_ROWS, Math.max(1, rows));
}

/**
 * The text the sandbox puts in the observation when *this* client stopped
 * the command — `cloud/server/environment.py:INTERRUPT_MESSAGE`. It is the
 * only evidence that separates a stop from a memory kill.
 */
const INTERRUPT_MESSAGE = "interrupted by user stop";

/**
 * What a bare exit code meant, where the runtime says nothing else. 128 is
 * the docker exec that could not start at all (G-03). Null where the number
 * speaks for itself.
 *
 * rc 137 is SIGKILL and the sandbox sends it for two unrelated reasons: the
 * container's memory cap, and the kill behind `/stop` and the wall-clock
 * limit. Telling someone who pressed esc that their sandbox ran out of
 * memory is a lie the transcript cannot take back (HAR-84 P1-3), so the
 * note names the stop when the observation says so and otherwise says both
 * and claims neither.
 */
export function exitNote(
  returncode: number | null | undefined,
  output?: string | null,
): string | null {
  switch (returncode) {
    case 137:
      return (output ?? "").toLowerCase().includes(INTERRUPT_MESSAGE)
        ? "stopped"
        : "killed (memory limit or stop)";
    case 143:
      return "terminated";
    case 124:
      return "killed: the command timed out";
    case 128:
      return "the sandbox could not run the command";
    default:
      return null;
  }
}

/**
 * The one-line session total: "12 steps · 4m 10s · $0.000". Parts the
 * server has not reported are left out rather than guessed at.
 */
export function sessionTotals(
  session: Pick<Session, "steps" | "cost"> & {
    total_wall_seconds?: number | null;
  },
): string {
  const parts: string[] = [];
  if (Number.isFinite(session.steps) && session.steps > 0) {
    parts.push(`${session.steps} step${session.steps === 1 ? "" : "s"}`);
  }
  const wall = session.total_wall_seconds;
  if (typeof wall === "number" && Number.isFinite(wall) && wall > 0) {
    parts.push(formatDuration(wall));
  }
  if (typeof session.cost === "number" && Number.isFinite(session.cost)) {
    parts.push(formatCost(session.cost));
  }
  return parts.join(" · ");
}

/**
 * True when every cost we have is exactly zero. A provider that reports no
 * cost and a run that genuinely cost nothing are indistinguishable from
 * "$0.000", so the column has to say which it is.
 */
export function costUntracked(
  values: readonly (number | null | undefined)[],
): boolean {
  let seen = 0;
  for (const value of values) {
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    if (value !== 0) return false;
    seen += 1;
  }
  return seen > 0;
}

/**
 * How long a turn took: the server's own measure first, the clock second.
 * The column is `NOT NULL DEFAULT 0.0`, so a zero is a turn the server did
 * not time rather than a turn that took no time — fall back rather than
 * claim "0s" over a minute of work.
 */
export function receiptWall(receipt: Receipt): string {
  const wall = receipt.wall_seconds;
  if (typeof wall === "number" && Number.isFinite(wall) && wall > 0) {
    return formatDuration(wall);
  }
  const start = toEpochSeconds(receipt.started_at);
  const end = toEpochSeconds(receipt.finished_at);
  if (start === null || end === null) return "—";
  return formatDuration(end - start);
}

/**
 * `GT 3 actions / 2 exact` — how much GroundTruth a turn actually used.
 * The server counts both per turn and per session; a reader who cannot see
 * them cannot tell a turn where GT paid from one where it never ran
 * (HAR-84 P1-2). Null when GT ran nothing, which is the common case and
 * does not deserve a line.
 */
export function gtCountsLabel(
  actions: number | null | undefined,
  exact: number | null | undefined,
): string | null {
  if (typeof actions !== "number" || !Number.isFinite(actions) || actions <= 0) {
    return null;
  }
  const matches =
    typeof exact === "number" && Number.isFinite(exact) ? Math.max(0, exact) : 0;
  return `GT ${actions} action${actions === 1 ? "" : "s"} / ${matches} exact`;
}
