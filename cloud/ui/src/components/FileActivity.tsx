import type { TrailStep } from "../trail";
import CommandOutput from "./CommandOutput";

interface Props {
  path: string;
  steps: readonly TrailStep[];
  /** Steps past this are being held back by the scrubber. */
  cutoff: number;
  onScrubTo: (n: number) => void;
}

/** The steps of the selected turn that resolved to this file. */
export default function FileActivity({ path, steps, cutoff, onScrubTo }: Props) {
  const mine = steps.filter((step) => step.files.includes(path));

  if (mine.length === 0) {
    return (
      <p className="ins-empty">The agent has not been here this turn.</p>
    );
  }

  return (
    <div className="acts">
      {mine.map((step) => (
        <div
          key={step.key}
          role="button"
          tabIndex={0}
          className={`act ${step.n > cutoff ? "is-future" : ""} ${
            step.isError ? "is-error" : ""
          }`}
          onClick={() => onScrubTo(step.n)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onScrubTo(step.n);
            }
          }}
          title={`Replay the graph to step ${step.n}`}
        >
          <span className="act-n mono">{step.n}</span>
          <div className="act-main">
            {step.thought && <p className="act-thought">{step.thought}</p>}
            {step.command && (
              <div className="cmd">
                <span className="cmd-sign" aria-hidden="true">
                  $
                </span>
                <span className="cmd-text">{step.command}</span>
              </div>
            )}
            <CommandOutput
              output={step.output}
              returncode={step.returncode}
              isError={step.isError}
              clip={6}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
