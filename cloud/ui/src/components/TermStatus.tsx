import { useEffect, useState } from "react";
import { formatStopwatch } from "../format";
import { BULLET } from "./TermLine";

/* ------------------------------------------------------------------ *
 * `✻ Working… (12s · 3 steps · esc to interrupt)`
 *
 * One line between the transcript and the input. It is the only animated
 * thing on the page: a five-glyph spinner and a verb that follows what the
 * agent is actually doing, so a long step still reads as motion.
 * ------------------------------------------------------------------ */

export const SPINNER = ["✻", "✽", "✶", "✳", "✢"] as const;
const SPIN_MS = 180;

export type Verb = "Thinking" | "Reading" | "Editing" | "Running" | "Checking";

const READS = /^(cat|less|head|tail|ls|find|grep|rg|wc|file|stat|git\s+(log|show|status|diff))\b|sed\s+-n/;
const CHECKS = /\b(pytest|tox|nox|unittest|npm\s+(test|run\s+test)|yarn\s+test|make\s+(test|check)|ruff|mypy|flake8|eslint|tsc)\b/;
const EDITS =
  /(^|[\s;&|(])(tee|patch|mv|cp|rm|mkdir|touch|truncate|install)\s|>>?[^&]|sed\s+-[a-z]*i|perl\s+-[a-z]*i|git\s+(apply|checkout|restore|revert|mv|rm)|apply_patch|python3?\s+-\s*<<|python3?\s+-c\b/;

/**
 * What the agent is doing, from the last thing it did. A step with no
 * command yet is a model call in flight, which is thinking; the rest is
 * read from the command, in the order a reader would guess it.
 */
export function verbFor(command: string | null | undefined): Verb {
  const text = (command ?? "").trim();
  if (!text) return "Thinking";
  if (CHECKS.test(text)) return "Checking";
  if (EDITS.test(text)) return "Editing";
  if (READS.test(text)) return "Reading";
  return "Running";
}

/**
 * While the workspace is still coming up, the phase is the verb.
 *
 * No frame yet means the session has just been created, and the first thing
 * a session does is clone — so that is what it says, rather than waiting
 * for a frame that on a small repo arrives after the clone is already done
 * (HAR-84 P2-14).
 */
export function phaseLine(phase: string | null, repo: string): string {
  switch (phase) {
    case null:
    case "":
    case "creating":
    case "cloning":
      return `Cloning ${repo || "the repository"}…`;
    case "sandbox_starting":
      return "Starting sandbox…";
    case "sandbox_ready":
      return "Sandbox ready…";
    case "indexing":
      return "Indexing (GroundTruth)…";
    case "gt_ready":
      return "GroundTruth ready…";
    default:
      return "Preparing workspace…";
  }
}

interface Props {
  /** True while a turn of this session's own is running. */
  running: boolean;
  /** True while the workspace is still being created. */
  preparing: boolean;
  phase: string | null;
  repo: string;
  stopping: boolean;
  elapsed: number | null;
  steps: number;
  verb: Verb;
  /** Workers running right now — a second line, never a second spinner. */
  agents: number;
}

export default function TermStatus({
  running,
  preparing,
  phase,
  repo,
  stopping,
  elapsed,
  steps,
  verb,
  agents,
}: Props) {
  const [frame, setFrame] = useState(0);
  const live = running || preparing || agents > 0;

  useEffect(() => {
    if (!live) return;
    const tick = setInterval(
      () => setFrame((n) => (n + 1) % SPINNER.length),
      SPIN_MS,
    );
    return () => clearInterval(tick);
  }, [live]);

  if (!live) return null;

  const parts: string[] = [];
  if (elapsed !== null) parts.push(formatStopwatch(elapsed));
  if (steps > 0) parts.push(`${steps} step${steps === 1 ? "" : "s"}`);
  if (running) parts.push("esc to interrupt");

  const label = preparing
    ? phaseLine(phase, repo)
    : stopping
      ? "Stopping…"
      : `${verb}…`;

  return (
    <div className="termstatus" aria-live="polite">
      <div>
        <span className="termstatus-spin" aria-hidden="true">
          {SPINNER[frame]}
        </span>{" "}
        <span className="termstatus-verb">{label}</span>
        {parts.length > 0 && ` (${parts.join(" · ")})`}
      </div>
      {agents > 0 && (
        <div className="termstatus-agents">
          <span className="tline-bullet" aria-hidden="true">
            {BULLET}
          </span>{" "}
          {agents} agent{agents === 1 ? "" : "s"} working
        </div>
      )}
    </div>
  );
}
