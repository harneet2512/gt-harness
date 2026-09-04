import { formatStopwatch } from "../format";

export type RadioMode =
  | "working"
  | "surveying"
  | "waiting"
  | "standing-by"
  | "off-air";

interface Props {
  mode: RadioMode;
  /** Lifecycle phase, so "surveying" can say what it is doing. */
  phase: string | null;
  /** Seconds into the running turn, when there is one. */
  elapsed: number | null;
}

const TEXT: Record<RadioMode, string> = {
  working: "on air · working",
  surveying: "surveying…",
  waiting: "waiting for you",
  "standing-by": "standing by",
  "off-air": "off air",
};

/** Three arcs and a dot: the surveyor's radio. */
export default function RadioIndicator({ mode, phase, elapsed }: Props) {
  const live = mode === "working" || mode === "surveying";
  const label =
    mode === "surveying" && (phase === "cloning" || phase === "indexing")
      ? `surveying · ${phase}`
      : TEXT[mode];

  const state =
    mode === "working" || mode === "surveying"
      ? "is-live"
      : mode === "waiting"
        ? "is-waiting"
        : mode === "off-air"
          ? "is-off"
          : "";

  return (
    <span
      className={`radio ${state} ${live ? "on-air" : ""}`}
      role="status"
      aria-label={label}
    >
      <svg
        className="radio-glyph"
        width="15"
        height="15"
        viewBox="0 0 15 15"
        aria-hidden="true"
      >
        <circle cx="3" cy="12" r="1.6" fill="currentColor" />
        <path
          className="arc-1"
          d="M3 9 A 3 3 0 0 1 6 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
        />
        <path
          className="arc-2"
          d="M3 6 A 6 6 0 0 1 9 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
        />
        <path
          className="arc-3"
          d="M3 3 A 9 9 0 0 1 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
        />
      </svg>
      <span className="radio-text">{label}</span>
      {elapsed !== null && (
        <span className="radio-elapsed">{formatStopwatch(elapsed)}</span>
      )}
    </span>
  );
}
