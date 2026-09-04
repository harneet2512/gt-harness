import { formatStopwatch } from "../format";

export type StatusMode =
  | "working"
  | "waiting"
  | "idle"
  | "preparing"
  | "failed"
  | "closed";

interface Props {
  mode: StatusMode;
  /** The lifecycle phase behind `preparing`: cloning, indexing… */
  phase: string | null;
  /** Seconds into the running turn. */
  elapsed: number | null;
  steps: number;
}

const LABEL: Record<StatusMode, string> = {
  working: "Working",
  waiting: "Waiting for you",
  idle: "Idle",
  preparing: "Preparing…",
  failed: "Failed",
  closed: "Closed",
};

/** A dot and three words: what the agent is doing right now. */
export default function StatusLine({ mode, phase, elapsed, steps }: Props) {
  const label =
    mode === "preparing" ? phaseLabel(phase) : LABEL[mode];
  const hot = mode === "working" || mode === "waiting";

  return (
    <div className={`status is-${mode}`}>
      <span
        className={`status-dot ${hot ? "is-hot" : ""} ${
          mode === "working" || mode === "preparing" ? "is-live" : ""
        }`}
        aria-hidden="true"
      />
      <span className="status-label">{label}</span>
      {mode === "working" && (
        <span className="status-meta mono">
          {steps > 0 && `${steps} step${steps === 1 ? "" : "s"}`}
          {steps > 0 && elapsed !== null && " · "}
          {elapsed !== null && formatStopwatch(elapsed)}
          <span className="caret">▌</span>
        </span>
      )}
    </div>
  );
}

function phaseLabel(phase: string | null): string {
  switch (phase) {
    case "cloning":
      return "Cloning…";
    case "indexing":
      return "Indexing…";
    default:
      return "Preparing…";
  }
}
