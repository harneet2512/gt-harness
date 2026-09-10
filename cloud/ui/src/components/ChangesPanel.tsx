import { useState } from "react";

import type { DiffFile, SessionDiff } from "../api";
import { publishSession } from "../api";
import type { PublishResult } from "../api";
import { shortSha } from "../format";

interface Props {
  diff: SessionDiff | null;
  /** Replay caveat, when the diff is the approximation rather than truth. */
  note: string | null;
  error: string | null;
  loading: boolean;
  /** The session this diff belongs to; publish hides without it. */
  sessionId: string | null;
  onRefresh: () => void;
  onPickFile: (path: string) => void;
}

/** What the agent has changed so far. Each row opens the inspector. */
export default function ChangesPanel({
  diff,
  note,
  error,
  loading,
  sessionId,
  onRefresh,
  onPickFile,
}: Props) {
  const files = diff?.files ?? [];
  const [publishing, setPublishing] = useState(false);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [pr, setPr] = useState<PublishResult | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);

  const publish = async () => {
    if (!sessionId || !token.trim()) return;
    setBusy(true);
    setPublishError(null);
    try {
      const result = await publishSession(sessionId, {
        github_token: token.trim(),
      });
      setPr(result);
      setToken("");
      setPublishing(false);
    } catch (exc) {
      setPublishError(exc instanceof Error ? exc.message : "publish failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="panel-head">
        <span className="cap">
          {files.length} file{files.length === 1 ? "" : "s"} changed
          {diff?.base_sha ? ` · base ${shortSha(diff.base_sha)}` : ""}
        </span>
        <span className="spacer" />
        {files.length > 0 && sessionId && (
          <button
            type="button"
            className="btn-text"
            onClick={() => setPublishing((v) => !v)}
            disabled={busy}
          >
            open PR
          </button>
        )}
        <button
          type="button"
          className="btn-text"
          onClick={onRefresh}
          disabled={loading}
        >
          {loading ? "…" : "refresh"}
        </button>
      </div>

      {publishing && !pr && (
        <div className="publish-row">
          <input
            type="password"
            className="publish-token"
            placeholder="GitHub token — used once, never stored"
            value={token}
            autoComplete="off"
            onChange={(e) => setToken(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void publish();
            }}
          />
          <button
            type="button"
            className="btn-text"
            disabled={busy || !token.trim()}
            onClick={() => void publish()}
          >
            {busy ? "pushing…" : "push & open"}
          </button>
        </div>
      )}
      {publishError && <div className="notice">{publishError}</div>}
      {pr && (
        <div className="notice publish-done">
          Opened PR #{pr.pr_number} on <code>{pr.branch}</code> —{" "}
          <a href={pr.pr_url} target="_blank" rel="noreferrer">
            {pr.pr_url}
          </a>
        </div>
      )}

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
