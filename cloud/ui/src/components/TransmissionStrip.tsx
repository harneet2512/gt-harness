import { formatCost, formatDuration } from "../format";
import { callSteps, stepKind, type StepSteering, type TrailStep } from "../trail";

interface Props {
  /** Turn ordinal in the thread: Turn 1, Turn 2… */
  no: number;
  steps: readonly TrailStep[];
  /**
   * Steps, in the one sense this UI has: model calls. `turn_finished.n_calls`
   * once the turn has ended, the `assistant` frames seen so far while it
   * runs. See the rule at the top of `trail.ts` — the reply, the receipt and
   * this line all count the same thing.
   */
  calls: number;
  edited: ReadonlySet<string>;
  running: boolean;
  selected: boolean;
  /** Steps included at the current scrub position; later ticks grey out. */
  cutoff: number;
  cost: number | null;
  elapsed: number | null;
  /** Mid-turn messages with no matching `steering` frame. */
  extraSteering: readonly StepSteering[];
  onSelect: () => void;
}

/**
 * One line between what you said and what came back: how far the agent
 * walked, and a tick per step. Clicking it selects the turn everywhere.
 */
export default function TransmissionStrip({
  no,
  steps,
  calls,
  edited,
  running,
  selected,
  cutoff,
  cost,
  elapsed,
  extraSteering,
  onSelect,
}: Props) {
  const midTurn: StepSteering[] = [
    ...steps.flatMap((step) => step.steering),
    ...extraSteering,
  ];

  // One tick per model call, so the row can be counted against the label.
  const ticks = callSteps(steps);

  const parts = [
    `${calls} step${calls === 1 ? "" : "s"}`,
    elapsed !== null ? formatDuration(elapsed) : null,
    cost !== null ? formatCost(cost) : null,
  ].filter((part): part is string => part !== null);

  return (
    <button
      type="button"
      className={`strip ${selected ? "is-selected" : ""}`}
      aria-pressed={selected}
      onClick={onSelect}
    >
      <span className="strip-line">
        <span className="strip-no">Turn {no}</span>
        {parts.map((part) => (
          <span key={part} className="strip-part">
            <span className="strip-sep">·</span> {part}
          </span>
        ))}
        {running && <span className="caret">▌</span>}
        {midTurn.length > 0 && (
          <span className="strip-more">{midTurn.length} mid-turn</span>
        )}
      </span>

      {ticks.length > 0 && (
        <span className="ticks">
          {ticks.map((step) => (
            <span
              key={step.key}
              className={`tick is-${stepKind(step, edited)} ${
                step.n > cutoff ? "is-future" : ""
              }`}
            />
          ))}
        </span>
      )}

      {selected && midTurn.length > 0 && (
        <span className="strip-open">
          {midTurn.map((message) => (
            <span className="strip-steer" key={message.key}>
              <span className="cap cap-orange">sent mid-turn</span>
              <span className="strip-steer-text">{message.content}</span>
            </span>
          ))}
        </span>
      )}
    </button>
  );
}
