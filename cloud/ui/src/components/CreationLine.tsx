import { CREATION_STEPS, creationStage } from "../launch";

interface Props {
  /** `owner/name`, for the first step, which is the only one worth naming. */
  repo: string;
  /** The lifecycle phase, verbatim. */
  phase: string | null;
}

/**
 * `cloning owner/repo… → sandbox → indexing`, one compact system line that
 * advances as the lifecycle frames arrive. It stands where the agent's
 * first step will be, so the wait happens in the transcript rather than
 * behind a spinner.
 */
export default function CreationLine({ repo, phase }: Props) {
  const stage = creationStage(phase);

  return (
    <p className="creation" aria-live="polite">
      {CREATION_STEPS.map((step, i) => (
        <span key={step} className="creation-step">
          {i > 0 && (
            <span className="creation-arrow" aria-hidden="true">
              →
            </span>
          )}
          <span
            className={`creation-name ${
              i < stage ? "is-done" : i === stage ? "is-now" : "is-next"
            }`}
          >
            {i === 0 && repo ? `cloning ${repo}` : step}
            {i === stage ? "…" : ""}
          </span>
        </span>
      ))}
    </p>
  );
}
