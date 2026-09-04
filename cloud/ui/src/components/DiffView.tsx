import { useMemo } from "react";
import type { DiffFile, SessionDiff } from "../api";
import { lineKind, patchFor } from "../patch";

interface Props {
  path: string;
  diff: SessionDiff | null;
  file: DiffFile | undefined;
  loading: boolean;
  error: string | null;
}

/** One file's patch, refetched while the agent is still writing to it. */
export default function DiffView({ path, diff, file, loading, error }: Props) {
  const lines = useMemo(
    () => patchFor(path, diff?.patch ?? "", file?.patch),
    [path, diff?.patch, file?.patch],
  );

  if (error) return <div className="notice">{error}</div>;

  if (lines.length === 0) {
    return (
      <p className="ins-empty">
        {loading ? "reading the workspace…" : "No changes yet."}
      </p>
    );
  }

  return (
    <div className="diff">
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
