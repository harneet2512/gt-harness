import type { SurveyStep } from "../survey";
import CommandOutput from "./CommandOutput";

interface Props {
  steps: readonly SurveyStep[];
  /** Steps past this are greyed: the scrubber is holding history. */
  cutoff: number;
  /** Step number the surveyor is standing on. */
  hereStep: number | null;
  edited: ReadonlySet<string>;
  focusPath: string | null;
  onPickFile: (path: string | null) => void;
  running: boolean;
}

/** Every step of the selected turn, in order: thought, command, output. */
export default function TrailPanel({
  steps,
  cutoff,
  hereStep,
  edited,
  focusPath,
  onPickFile,
  running,
}: Props) {
  const shown = focusPath
    ? steps.filter((step) => step.files.includes(focusPath))
    : steps;

  if (shown.length === 0) {
    return (
      <p className="inst-empty">
        {focusPath
          ? "The surveyor has not been here this turn."
          : running
            ? "Waiting for the first step…"
            : "No steps in this turn."}
      </p>
    );
  }

  return (
    <div className="trail">
      {shown.map((step) => (
        <div
          key={step.key}
          className={`trail-step ${step.n > cutoff ? "is-future" : ""} ${
            step.n === hereStep ? "is-here" : ""
          }`}
        >
          <div className="trail-n">
            {step.n}
            {step.n === hereStep && (
              <span className="trail-here" aria-label="current position" />
            )}
          </div>

          <div className="trail-main">
            {step.thought && <p className="trail-thought">{step.thought}</p>}

            {step.files.length > 0 && (
              <div className="trail-files">
                {step.files.map((path) => (
                  <button
                    type="button"
                    key={path}
                    className={`trail-file ${edited.has(path) ? "is-edit" : ""}`}
                    onClick={() => onPickFile(path === focusPath ? null : path)}
                  >
                    {path}
                  </button>
                ))}
              </div>
            )}

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
            />

            {step.errors.map((error, i) => (
              <p className="trail-error" key={i}>
                {error}
              </p>
            ))}

            {step.steering.map((message) => (
              <div className="trail-steer" key={message.key}>
                <span className="cap cap-orange">sent mid-turn</span>
                <p className="trail-steer-text">{message.content}</p>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
