import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listSessions, type Session } from "../api";
import { BUILD_SHA } from "../build";
import { formatRelative, repoShort, truncate } from "../format";
import NewSessionForm from "./NewSessionForm";

const REFRESH_MS = 5000;

interface Props {
  activeId: string | null;
  /** The open session, for the label before the list has loaded. */
  active: Session | null;
}

function dotClass(status: string): string {
  if (status === "running" || status === "creating") return "is-hot";
  if (status === "failed") return "is-failed";
  if (status === "closed") return "is-closed";
  return "";
}

/** Every session behind one control, at the top of the conversation. */
export default function SessionSwitcher({ activeId, active }: Props) {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setSessions(await listSessions());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
    const poll = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(poll);
  }, [load]);

  useEffect(() => {
    if (!open && !creating) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) {
        setOpen(false);
        setCreating(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      setCreating(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, creating]);

  const current = active ?? sessions.find((s) => s.id === activeId) ?? null;

  return (
    <div className="switch" ref={box}>
      <button
        type="button"
        className="switch-btn"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => {
          setOpen(!open);
          setCreating(false);
        }}
      >
        {current ? (
          <>
            <span className={`status-dot ${dotClass(String(current.status))}`} />
            <span className="switch-repo">{repoShort(current.repo)}</span>
            <span className="switch-sep">·</span>
            <span className="switch-ref mono">{current.ref}</span>
          </>
        ) : (
          <span className="switch-repo">Sessions</span>
        )}
        <span className="switch-chev" aria-hidden="true">
          ▾
        </span>
      </button>

      {open && !creating && (
        <ul className="switch-menu" role="listbox">
          {error && (
            <li>
              <div className="notice switch-notice">{error}</div>
            </li>
          )}
          {sessions.length === 0 && !error && (
            <li className="switch-empty">No sessions yet.</li>
          )}
          {sessions.map((s) => (
            <li key={s.id} role="option" aria-selected={s.id === activeId}>
              <button
                type="button"
                className={`switch-item ${s.id === activeId ? "is-active" : ""}`}
                onClick={() => {
                  setOpen(false);
                  navigate(`/sessions/${s.id}`);
                }}
              >
                <span className="switch-top">
                  <span className={`status-dot ${dotClass(String(s.status))}`} />
                  <span className="switch-repo">{repoShort(s.repo)}</span>
                  <span className="switch-ref mono">{s.ref}</span>
                  <span className="spacer" />
                  <span className="cap cap-muted">
                    {formatRelative(s.updated_at)}
                  </span>
                </span>
                <span className="switch-last">
                  {s.last_message
                    ? truncate(s.last_message, 96)
                    : "no messages yet"}
                </span>
              </button>
            </li>
          ))}
          <li className="switch-foot">
            <button
              type="button"
              className="btn btn-orange btn-block"
              onClick={() => setCreating(true)}
            >
              New session
            </button>
            <span className="build-stamp mono">build {BUILD_SHA}</span>
          </li>
        </ul>
      )}

      {creating && (
        <NewSessionForm
          onCancel={() => {
            setCreating(false);
            setOpen(false);
          }}
          onCreated={(session) => {
            setCreating(false);
            setOpen(false);
            void load();
            navigate(`/sessions/${session.id}`);
          }}
        />
      )}
    </div>
  );
}
