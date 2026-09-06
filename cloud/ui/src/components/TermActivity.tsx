import { actionLine, evidenceLine, fromCommand } from "../gt";
import type { TrailStep } from "../trail";
import { Call, Cont, Line } from "./TermLine";
import TermOutput from "./TermOutput";

/** How much of a command the `Bash(...)` line shows before it clips. */
const COMMAND_MAX = 180;

/**
 * A command on one line. A heredoc is a single action and has to read as
 * one: the first line, then how many more there were.
 */
export function commandLine(command: string): string {
  const lines = command.split("\n");
  const head =
    lines[0].length > COMMAND_MAX
      ? `${lines[0].slice(0, COMMAND_MAX)}…`
      : lines[0];
  return lines.length > 1
    ? `${head} … +${lines.length - 1} more line${lines.length === 2 ? "" : "s"}`
    : head;
}

interface Props {
  steps: readonly TrailStep[];
  /** Steps past this are being held back by the scrubber. */
  cutoff: number;
  edited: ReadonlySet<string>;
  running: boolean;
  onPickFile: (path: string) => void;
}

/**
 * A turn, step by step, in the terminal's own grammar:
 *
 *     ⏺ I need to see how the option parser is wired.
 *     ⏺ Bash(rg -n "class Option" src/click)
 *       ⎿  src/click/core.py:2103:class Option(Parameter):
 *     ⏺ GroundTruth(exact_literal_search "class Option" in src/click)
 *       ⎿  2 matches · exact · complete
 *
 * There is no card, no panel and no step number: the order is the story.
 */
export default function TermActivity({
  steps,
  cutoff,
  edited,
  running,
  onPickFile,
}: Props) {
  if (steps.length === 0) {
    return running ? (
      <Line tone="dim">Waiting for the first step…</Line>
    ) : null;
  }

  return (
    <div className="tacts">
      {steps.map((step) => {
        /* A typed GroundTruth action is not a shell command and must not be
           drawn as one. The frame says so when the server sends it; the
           command's own JSON says so when it does not. */
        const gt = step.gt ?? fromCommand(step.command ?? "");
        const evidence = evidenceLine(gt?.evidence ?? null);

        return (
          <div
            key={step.key}
            className={step.n > cutoff ? "is-future" : undefined}
          >
            {step.thought && <p className="tprose">{step.thought}</p>}

            {gt ? (
              <>
                <Call tool="GroundTruth" arg={actionLine(gt)} tone="gt" />
                {evidence && <Cont tone="gt">{evidence}</Cont>}
                {!evidence && step.output && (
                  <TermOutput
                    output={step.output}
                    returncode={step.returncode}
                    isError={step.isError}
                  />
                )}
              </>
            ) : (
              step.command && (
                <>
                  <Call tool="Bash" arg={commandLine(step.command)} />
                  <TermOutput
                    output={step.output}
                    returncode={step.returncode}
                    isError={step.isError}
                  />
                </>
              )
            )}

            {step.files.length > 0 && (
              <Cont tone="dim">
                {step.files.map((path, i) => (
                  <span key={path}>
                    {i > 0 && " · "}
                    <button
                      type="button"
                      className={`cont-more ${edited.has(path) ? "is-edit" : ""}`}
                      onClick={() => onPickFile(path)}
                    >
                      {path}
                    </button>
                  </span>
                ))}
              </Cont>
            )}

            {step.errors.map((error, i) => (
              <Cont key={i} tone="error">
                Error: {error}
              </Cont>
            ))}

            {step.steering.map((message) => (
              <p className="termsaid is-mid" key={message.key}>
                <span className="termsaid-mark" aria-hidden="true">
                  &gt;
                </span>
                <span>(mid-turn) {message.content}</span>
              </p>
            ))}
          </div>
        );
      })}
    </div>
  );
}
