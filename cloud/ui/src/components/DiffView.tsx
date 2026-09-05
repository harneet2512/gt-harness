import { useMemo } from "react";
import type { DiffFile, SessionDiff } from "../api";
import { lineKind, patchFor } from "../patch";

interface Props {
  path: string;
  diff: SessionDiff | null;
  file: DiffFile | undefined;
  /** Replay caveat, when the diff is the approximation rather than truth. */
  note: string | null;
  loading: boolean;
  error: string | null;
}

/** One file's patch, refetched while the agent is still writing to it. */
export default function DiffView({
  path,
  diff,
  file,
  note,
  loading,
  error,
}: Props) {
  const lines = useMemo(
    () => patchFor(path, diff?.patch ?? "", file?.patch),
    [path, diff?.patch, file?.patch],
  );

  if (error) return <div className="notice">{error}</div>;

  if (lines.length === 0) {
    return (
      <>
        {note && <p className="approx cap">{note}</p>}
        <p className="ins-empty">
          {loading
            ? "reading the workspace…"
            : note
              ? "This file had not been written by this step."
              : "No changes yet."}
        </p>
      </>
    );
  }

  return (
    <div className="diff">
      {note && <p className="approx cap">{note}</p>}
      {loading && <div className="diff-live cap cap-muted">refreshing…</div>}
      <pre>
        {lines.map((line, i) => (
          <div key={i} className={`dl is-${lineKind(line)}`}>
            {line || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}
