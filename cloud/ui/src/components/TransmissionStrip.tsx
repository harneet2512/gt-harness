import { formatCost, formatDuration } from "../format";
import { stepKind, type StepSteering, type SurveyStep } from "../survey";

interface Props {
  /** Turn ordinal in the log: № 1, № 2… */
  no: number;
  steps: readonly SurveyStep[];
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
 * One line between what you said and what came back: how far the surveyor
 * walked, and a tick per step. Clicking it selects the turn everywhere.
 */
export default function TransmissionStrip({
  no,
  steps,
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

  const parts = [
    `${steps.length} step${steps.length === 1 ? "" : "s"}`,
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
        <span className="strip-no">№{no}</span>
        {parts.map((part) => (
          <span key={part}>
            <span className="strip-sep">·</span> {part}
          </span>
        ))}
        {running && <span className="caret">▌</span>}
        {midTurn.length > 0 && (
          <span className="strip-more">{midTurn.length} mid-turn</span>
        )}
      </span>

      {steps.length > 0 && (
        <span className="ticks">
          {steps.map((step) => (
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
