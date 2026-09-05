import { useEffect, useState } from "react";
import type { Session } from "../api";
import { formatRelative } from "../format";
import { parseRepoRef, repoChipLabel, type RepoRef } from "../repoUrl";
import { usePopover } from "../usePopover";

interface Props {
  /** The repository the next message will run in, or null when none is known. */
  value: RepoRef | null;
  /** Recent sessions, for the "you have worked here before" list. */
  sessions: readonly Session[];
  onPick: (next: RepoRef) => void;
}

const NO_REPO = "no repo yet — paste a GitHub URL in your message or pick one";

/** Distinct repo+ref pairs, most recent first. */
function recentRepos(sessions: readonly Session[]): RepoRef[] {
  const out: RepoRef[] = [];
  const seen = new Set<string>();
  for (const session of sessions) {
    if (!session.repo) continue;
    const key = `${session.repo}#${session.ref}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ repo: session.repo, ref: session.ref || null });
  }
  return out;
}

/**
 * `working in owner/name @ ref` — one quiet line under the prompt, and the
 * only place the repository is ever asked for.
 */
export default function RepoChip({ value, sessions, onPick }: Props) {
  const pop = usePopover();
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pop.open) return;
    setUrl(value?.repo ?? "");
    setRef(value?.ref ?? "");
    setError(null);
  }, [pop.open, value]);

  function use(next: RepoRef) {
    onPick(next);
    pop.setOpen(false);
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = parseRepoRef(url.trim());
    if (!parsed) {
      setError("A GitHub URL, like https://github.com/owner/name.");
      return;
    }
    use({ repo: parsed.repo, ref: ref.trim() || parsed.ref });
  }

  const recent = recentRepos(sessions);

  return (
    <span className="chipline" ref={pop.ref}>
      <span className="cap cap-muted">working in</span>
      <button
        type="button"
        className={`repochip ${value ? "" : "is-empty"}`}
        aria-expanded={pop.open}
        aria-haspopup="dialog"
        title="Choose the repository this prompt runs in"
        onClick={pop.toggle}
      >
        {value ? repoChipLabel(value.repo, value.ref) : NO_REPO}
      </button>

      {pop.open && (
        <div className="pop pop-repo" role="dialog" aria-label="Repository">
          <form onSubmit={submit}>
            <div className="field">
              <label className="cap" htmlFor="chip-url">
                repository url
              </label>
              <input
                id="chip-url"
                value={url}
                autoFocus
                placeholder="https://github.com/owner/name"
                onChange={(e) => {
                  setUrl(e.target.value);
                  setError(null);
                }}
              />
            </div>
            <div className="field">
              <label className="cap" htmlFor="chip-ref">
                ref — branch, tag or sha
              </label>
              <input
                id="chip-ref"
                value={ref}
                placeholder="main"
                onChange={(e) => setRef(e.target.value)}
              />
            </div>
            {error && <div className="notice">{error}</div>}
            <button type="submit" className="btn btn-orange btn-block">
              Use this repository
            </button>
          </form>

          {recent.length > 0 && (
            <div className="pop-recent">
              <p className="cap cap-muted">recent</p>
              <ul>
                {recent.map((item) => (
                  <li key={`${item.repo}#${item.ref}`}>
                    <button
                      type="button"
                      className="pop-recent-item"
                      onClick={() => use(item)}
                    >
                      <span className="pop-recent-name">
                        {repoChipLabel(item.repo, item.ref)}
                      </span>
                      <span className="cap cap-muted">
                        {formatRelative(
                          sessions.find(
                            (s) => s.repo === item.repo && s.ref === item.ref,
                          )?.updated_at,
                        )}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </span>
  );
}
