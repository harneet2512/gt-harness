import { stepKind, type SurveyStep } from "../survey";

interface Props {
  steps: readonly SurveyStep[];
  edited: ReadonlySet<string>;
  /** 1-based step being shown; equals steps.length while live. */
  position: number;
  live: boolean;
  onScrub: (position: number) => void;
  onLive: () => void;
}

/**
 * Walk back through the turn. One tick per step, coloured like the
 * transmission strip; releasing at the end snaps back to live.
 */
export default function Scrubber({
  steps,
  edited,
  position,
  live,
  onScrub,
  onLive,
}: Props) {
  const total = steps.length;

  if (total === 0) {
    return (
      <div className="scrubber">
        <span className="scrub-empty cap cap-muted">no steps to replay</span>
      </div>
    );
  }

  const fraction = total > 1 ? (position - 1) / (total - 1) : 1;

  return (
    <div className="scrubber">
      <div className="scrub-track">
        <span className="scrub-rule" aria-hidden="true" />
        <span className="scrub-ticks" aria-hidden="true">
          {steps.map((step) => (
            <span
              key={step.key}
              className={`tick is-${stepKind(step, edited)} ${
                step.n > position ? "is-future" : ""
              }`}
            />
          ))}
        </span>
        <span
          className="scrub-caret"
          style={{ left: `calc(${(fraction * 100).toFixed(2)}% - 1px)` }}
          aria-hidden="true"
        />
        <input
          className="scrub-input"
          type="range"
          min={1}
          max={total}
          step={1}
          value={position}
          aria-label="Replay position"
          aria-valuetext={`step ${position} of ${total}`}
          onChange={(e) => {
            const next = Number.parseInt(e.target.value, 10);
            if (next >= total) onLive();
            else onScrub(next);
          }}
        />
      </div>

      <span className="scrub-state">
        {live ? `step ${total} · live` : `step ${position} of ${total}`}
      </span>
      {!live && (
        <button type="button" className="btn-text is-hot" onClick={onLive}>
          live
        </button>
      )}
    </div>
  );
}
