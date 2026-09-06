/* ------------------------------------------------------------------ *
 * Box drawing.
 *
 * Frames are characters, not CSS borders: ╭─╮ │ ╰─╯. The horizontal runs
 * are a long string of ─ clipped by the element's own width, which is how
 * a terminal draws a box that has to fit a column it does not control.
 * ------------------------------------------------------------------ */

import type { ReactNode } from "react";

/** Long enough for any pane this app draws, at any font size. */
const DASHES = "─".repeat(400);

interface FrameProps {
  /** Sits in the top rule: `╭─ graph · 166 files ─────╮`. */
  title?: ReactNode;
  /** Sits at the right end of the top rule. */
  right?: ReactNode;
  className?: string;
  children: ReactNode;
}

/** The rule itself, clipped: `────────────`. */
export function Rule({ className = "" }: { className?: string }) {
  return (
    <span className={`boxrule ${className}`} aria-hidden="true">
      {DASHES}
    </span>
  );
}

/** `╭─ title ─────────────╮` */
export function BoxTop({
  title,
  right,
}: {
  title?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="boxline boxline-top" aria-hidden="true">
      <span className="boxglyph">╭</span>
      <span className="boxglyph">─</span>
      {title !== undefined && <span className="boxtitle">{title}</span>}
      <Rule />
      {right !== undefined && <span className="boxtitle">{right}</span>}
      <span className="boxglyph">─</span>
      <span className="boxglyph">╮</span>
    </div>
  );
}

/** `╰─────────────────────╯` */
export function BoxBottom() {
  return (
    <div className="boxline boxline-bottom" aria-hidden="true">
      <span className="boxglyph">╰</span>
      <Rule />
      <span className="boxglyph">╯</span>
    </div>
  );
}

/** `│ …content… │` — one row of a box. */
export function BoxRow({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`boxrow ${className}`}>
      <span className="boxglyph" aria-hidden="true">
        │
      </span>
      <span className="boxbody">{children}</span>
      <span className="boxglyph" aria-hidden="true">
        │
      </span>
    </div>
  );
}

/** A whole framed block: top rule, rows, bottom rule. */
export default function Box({ title, right, className = "", children }: FrameProps) {
  return (
    <div className={`box ${className}`}>
      <BoxTop title={title} right={right} />
      {children}
      <BoxBottom />
    </div>
  );
}
