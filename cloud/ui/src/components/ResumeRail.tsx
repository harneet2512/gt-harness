import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Session } from "../api";
import { BUILD_SHA } from "../build";
import {
  formatRelative,
  repoShort,
  sessionClosedLabel,
  truncate,
} from "../format";

interface Props {
  activeId: string | null;
  sessions: readonly Session[];
  error: string | null;
}

function dotClass(status: string): string {
  if (status === "running" || status === "creating") return "is-hot";
  if (status === "failed") return "is-failed";
  if (status === "closed") return "is-closed";
  return "";
}

/**
 * `/resume`, as a rail. A thin strip until you ask for it, then the list of
 * everything you have running: repository, last message, status, when.
 */
export default function ResumeRail({ activeId, sessions, error }: Props) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  return (
    <nav className={`rail ${open ? "is-open" : ""}`} aria-label="Sessions">
      <div className="rail-head">
        <button
          type="button"
          className="btn-text rail-toggle"
          aria-expanded={open}
          aria-label={open ? "Hide sessions" : "Show sessions"}
          title={open ? "Hide sessions" : "Show sessions"}
          onClick={() => setOpen(!open)}
        >
          <span aria-hidden="true">{open ? "‹" : "≡"}</span>
        </button>
        {open && <span className="cap rail-title">sessions</span>}
        {open && (
          <button
            type="button"
            className="btn-text rail-new"
            onClick={() => navigate("/")}
          >
            New
          </button>
        )}
      </div>

      {!open && (
        <button
          type="button"
          className="btn-text rail-new-thin"
          aria-label="New session"
          title="New session"
          onClick={() => navigate("/")}
        >
          <span aria-hidden="true">+</span>
        </button>
      )}

      {open && (
        <div className="rail-body">
          {error && <div className="notice rail-notice">{error}</div>}
          {sessions.length === 0 && !error && (
            <p className="rail-empty">No sessions yet.</p>
          )}
          <ul className="rail-list">
            {sessions.map((session) => {
              const closed = sessionClosedLabel(
                String(session.status),
                session.closed_reason,
              );
              return (
                <li key={session.id}>
                  <button
                    type="button"
                    className={`rail-item ${
                      session.id === activeId ? "is-active" : ""
                    }`}
                    aria-current={session.id === activeId ? "page" : undefined}
                    onClick={() => {
                      setOpen(false);
                      navigate(`/sessions/${session.id}`);
                    }}
                  >
                    <span className="rail-top">
                      <span
                        className={`status-dot ${dotClass(String(session.status))}`}
                      />
                      <span className="rail-repo">{repoShort(session.repo)}</span>
                      <span className="spacer" />
                      <span className="cap cap-muted">
                        {formatRelative(session.updated_at)}
                      </span>
                    </span>
                    <span className="rail-ref mono">{session.ref}</span>
                    <span className="rail-last">
                      {closed ??
                        (session.last_message
                          ? truncate(session.last_message, 90)
                          : "no messages yet")}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <span className="build-stamp mono">build {BUILD_SHA}</span>
        </div>
      )}
    </nav>
  );
}
