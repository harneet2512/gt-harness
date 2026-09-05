import type { Session } from "../api";
import { gtBadgeLabel, sessionTotals } from "../format";
import type { Prefs } from "../prefs";
import { repoChipLabel } from "../repoUrl";
import SettingsGear from "./SettingsGear";
import StatusLine, { type StatusMode } from "./StatusLine";

interface Props {
  session: Session | null;
  mode: StatusMode;
  phase: string | null;
  elapsed: number | null;
  liveSteps: number;
  running: boolean;
  stopping: boolean;
  graphOpen: boolean;
  onToggleGraph: () => void;
  onStop: () => void;
  prefs: Prefs;
  onPrefs: (next: Prefs) => void;
  gearSignal: number;
}

/**
 * One line: where you are, what it is doing, and the three controls that
 * are not the conversation.
 */
export default function SessionHeader({
  session,
  mode,
  phase,
  elapsed,
  liveSteps,
  running,
  stopping,
  graphOpen,
  onToggleGraph,
  onStop,
  prefs,
  onPrefs,
  gearSignal,
}: Props) {
  /* While a turn runs the status line is already counting steps and seconds;
     the session total beside it reads as a contradiction. */
  const totals = session && !running ? sessionTotals(session) : "";
  const gtMode = String(session?.gt_mode ?? "off");
  const gtStatus = String(session?.gt_status ?? "off");

  return (
    <header className="head">
      <span className="head-repo" title={session?.repo}>
        {session ? repoChipLabel(session.repo, session.ref) : "…"}
      </span>

      <span className="head-sep" aria-hidden="true">
        ·
      </span>
      <StatusLine
        stopping={stopping}
        mode={mode}
        phase={phase}
        elapsed={elapsed}
        steps={liveSteps}
      />

      {totals !== "" && (
        <>
          <span className="head-sep" aria-hidden="true">
            ·
          </span>
          <span className="mono muted head-totals">{totals}</span>
        </>
      )}

      <span className="spacer" />

      {session && (
        <span
          className={`gt-badge is-${gtStatus}`}
          title={`ground truth: mode ${gtMode || "off"}, index ${gtStatus}`}
        >
          {gtBadgeLabel(gtMode, gtStatus)}
        </span>
      )}

      <button
        type="button"
        className={`btn-text ${graphOpen ? "is-on" : ""}`}
        aria-pressed={graphOpen}
        title="Ctrl/Cmd + G"
        onClick={onToggleGraph}
      >
        graph
      </button>

      <SettingsGear
        prefs={prefs}
        openSignal={gearSignal}
        onChange={onPrefs}
        note="Kept on this browser. This session keeps the settings it started with."
      />

      {running && (
        <button
          type="button"
          className="btn-text"
          disabled={stopping}
          title={
            stopping
              ? "Stopping at the end of the model call in flight"
              : "Ctrl/Cmd + Shift + Backspace"
          }
          onClick={onStop}
        >
          {stopping ? "stopping…" : "stop"}
        </button>
      )}
    </header>
  );
}
