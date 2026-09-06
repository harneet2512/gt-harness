/* ------------------------------------------------------------------ *
 * The two lines this transcript is made of.
 *
 *   ⏺ Bash(pytest -q)            a thing that happened
 *     ⎿  42 passed               what it said
 *
 * Everything in the thread — the agent's prose, a command, a GroundTruth
 * query, a worker, a receipt — is one of these. Keeping them here is what
 * makes the grammar a grammar rather than a coincidence.
 * ------------------------------------------------------------------ */

import type { ReactNode } from "react";

export const BULLET = "⏺";
export const CONT = "⎿";

type Tone = "" | "gt" | "dim";

/** `⏺ …` */
export function Line({
  tone = "",
  className = "",
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`tline ${tone ? `is-${tone}` : ""} ${className}`}>
      <span className="tline-bullet" aria-hidden="true">
        {BULLET}
      </span>
      <span className="tline-body">{children}</span>
    </div>
  );
}

/**
 * `⏺ Bash(pytest -q)` — a typed call: the tool's name, then what it was
 * asked. The name is the half you scan for, so it is the half that is bold.
 */
export function Call({
  tool,
  arg,
  tone = "",
  after,
}: {
  tool: string;
  arg: string;
  tone?: Tone;
  after?: ReactNode;
}) {
  return (
    <Line tone={tone}>
      <span className="tname">{tool}</span>
      <span className="targ">({arg})</span>
      {after}
    </Line>
  );
}

/** `  ⎿  …` */
export function Cont({
  tone = "",
  children,
}: {
  tone?: "" | "error" | "dim" | "gt" | "ok";
  children: ReactNode;
}) {
  return (
    <div className={`cont ${tone ? `is-${tone}` : ""}`}>
      <span className="cont-mark" aria-hidden="true">
        {CONT}
      </span>
      <span className="cont-body">{children}</span>
    </div>
  );
}

/** A continuation line with no mark: the second and later lines of output. */
export function ContMore({ children }: { children: ReactNode }) {
  return (
    <div className="cont">
      <span className="cont-mark" aria-hidden="true">
        {" "}
      </span>
      <span className="cont-body">{children}</span>
    </div>
  );
}
