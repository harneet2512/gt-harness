import { useState } from "react";

export const COLLAPSED_LINES = 8;

interface Props {
  output: string;
  returncode: number | null;
  isError: boolean;
  /** How much is shown before the "N more lines" affordance. */
  clip?: number;
}

/** Command output, clipped until asked for more. */
export default function CommandOutput({
  output,
  returncode,
  isError,
  clip = COLLAPSED_LINES,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  const lines = output.length > 0 ? output.replace(/\n+$/, "").split("\n") : [];
  const overflows = lines.length > clip;
  const clipped = !expanded && overflows;
  const shown = clipped ? lines.slice(0, clip) : lines;
  const failed = returncode !== null && returncode !== 0;

  if (lines.length === 0 && !failed) return null;

  return (
    <div className={`out ${isError ? "is-error" : ""}`}>
      {lines.length === 0 ? (
        <pre className="muted">(no output)</pre>
      ) : (
        <pre>{shown.join("\n")}</pre>
      )}
      {(failed || overflows) && (
        <div className="out-foot">
          {failed && <span className="out-rc">exit {returncode}</span>}
          {overflows && (
            <button
              type="button"
              className="link"
              onClick={(e) => {
                e.stopPropagation();
                setExpanded(!expanded);
              }}
            >
              {clipped
                ? `${lines.length - clip} more lines`
                : "show less"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
