import type { DiffFile, SessionDiff } from "../api";
import { shortSha } from "../format";

interface Props {
  diff: SessionDiff | null;
  /** Replay caveat, when the diff is the approximation rather than truth. */
  note: string | null;
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
  onPickFile: (path: string) => void;
}

/** What the agent has changed so far. Each row opens the inspector. */
export default function ChangesPanel({
  diff,
  note,
  error,
  loading,
  onRefresh,
  onPickFile,
}: Props) {
  const files = diff?.files ?? [];

  return (
    <>
      <div className="panel-head">
        <span className="cap">
          {files.length} file{files.length === 1 ? "" : "s"} changed
          {diff?.base_sha ? ` · base ${shortSha(diff.base_sha)}` : ""}
        </span>
        <span className="spacer" />
        <button
          type="button"
          className="btn-text"
          onClick={onRefresh}
          disabled={loading}
        >
          {loading ? "…" : "refresh"}
        </button>
      </div>

      {error && <div className="notice">{error}</div>}

      {note && <p className="approx cap">{note}</p>}

      {!error && files.length === 0 && (
        <p className="panel-empty">
          {note
            ? "Nothing had been written by this step."
            : "Nothing has changed yet."}
        </p>
      )}

      {files.length > 0 && (
        <ul className="files">
          {files.map((file) => (
            <li key={file.path}>
              <button
                type="button"
                className="file-row"
                onClick={() => onPickFile(file.path)}
              >
                <span className={`file-mark is-${tone(file)}`} aria-hidden="true" />
                <span className="file-path mono" title={file.path}>
                  {file.path}
                </span>
                <span className="file-status cap">{String(file.status)}</span>
                <span className="file-counts mono">
                  <span className="add">+{file.additions}</span>
                  <span className="del">−{file.deletions}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function tone(file: DiffFile): string {
  if (file.status === "deleted") return "bad";
  if (file.status === "added") return "new";
  return "edit";
}
