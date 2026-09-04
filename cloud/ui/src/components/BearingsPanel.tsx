import type { SessionEvent } from "../api";
import { gtEvents, text } from "../eventViews";
import { formatClock } from "../format";
import type { SurveyStep } from "../survey";

interface Props {
  steps: readonly SurveyStep[];
  /** Steps past this are greyed while the scrubber holds history. */
  cutoff: number;
  events: readonly SessionEvent[];
  gtStatus: string;
}

/**
 * The reasoning trail: what the surveyor said it was doing, in order,
 * plus the GroundTruth milestones when GT is actually wired up.
 */
export default function BearingsPanel({
  steps,
  cutoff,
  events,
  gtStatus,
}: Props) {
  const thoughts = steps.filter((step) => step.thought);
  const gt = gtStatus === "ready" ? gtEvents(events) : [];

  return (
    <>
      <div className="inst-block">
        {thoughts.length === 0 ? (
          <p className="inst-empty">
            The surveyor has not explained a step in this turn yet.
          </p>
        ) : (
          <ol className="bearings">
            {thoughts.map((step, i) => (
              <li
                key={step.key}
                className={`bearing ${
                  i === thoughts.length - 1 ? "is-latest" : ""
                } ${step.n > cutoff ? "is-future" : ""}`}
              >
                <span className="bearing-n">{step.n}</span>
                <span className="bearing-text">{step.thought}</span>
              </li>
            ))}
          </ol>
        )}
      </div>

      {gt.length > 0 && (
        <div className="inst-block">
          <div className="inst-head">
            <span className="cap">ground truth</span>
          </div>
          <ul className="gt-list">
            {gt.map((event) => {
              const status = text(event, "status");
              const ok = status === "gt_ready";
              return (
                <li key={event.id}>
                  <span className={ok ? "gt-ok" : "gt-bad"}>
                    {status.replace(/^gt_/, "")}
                  </span>
                  <span className="muted">{formatClock(event.timestamp)}</span>
                  {text(event, "error") && <span>{text(event, "error")}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </>
  );
}
