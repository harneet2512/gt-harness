import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Session } from "../api";
import { formatRelative, repoShort, sessionClosedLabel, truncate } from "../format";
import { nestSessions } from "../workers";
import Box, { BoxRow } from "./Box";

interface Props {
  sessions: readonly Session[];
  activeId: string | null;
  onClose: () => void;
}

/**
 * `/resume` — the session list, full screen, driven from the keyboard.
 *
 * ↑↓ moves, ⏎ opens, esc closes. It replaces the permanent rail: a list you
 * ask for costs nothing when you are not asking for it, and a terminal does
 * not keep a sidebar open on the chance you might want one.
 */
export default function ResumePicker({ sessions, activeId, onClose }: Props) {
  const navigate = useNavigate();
  const rows = useMemo(() => nestSessions(sessions), [sessions]);
  const [pick, setPick] = useState(() => {
    const at = rows.findIndex((row) => row.session.id === activeId);
    return at >= 0 ? at : 0;
  });
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    boxRef.current?.focus();
  }, []);

  /**
   * The Enter that opened this picker is *still propagating* when React
   * flushes the state update that mounts it — a discrete input event is
   * flushed synchronously — so a listener attached during that flush hears
   * its own opening keystroke and immediately picks the first row. The
   * listener therefore goes on after the event has finished.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (rows.length === 0) return;
      if (e.key === "ArrowDown" || (e.key === "j" && e.ctrlKey)) {
        e.preventDefault();
        setPick((n) => (n + 1) % rows.length);
      } else if (e.key === "ArrowUp" || (e.key === "k" && e.ctrlKey)) {
        e.preventDefault();
        setPick((n) => (n - 1 + rows.length) % rows.length);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const chosen = rows[Math.min(pick, rows.length - 1)];
        onClose();
        navigate(`/sessions/${chosen.session.id}`);
      }
    };
    const arm = setTimeout(() => window.addEventListener("keydown", onKey), 0);
    return () => {
      clearTimeout(arm);
      window.removeEventListener("keydown", onKey);
    };
  }, [rows, pick, navigate, onClose]);

  return (
    <div className="resume" role="dialog" aria-label="Resume a session">
      <div className="resume-inner" ref={boxRef} tabIndex={-1}>
        <Box title=" resume " right={` ${rows.length} sessions `}>
          {rows.length === 0 && (
            <BoxRow>
              <span className="dim">no sessions yet</span>
            </BoxRow>
          )}
          {rows.map((row, i) => {
            const { session, depth } = row;
            const worker = depth === 1;
            const closed = sessionClosedLabel(
              String(session.status),
              session.closed_reason,
            );
            return (
              <BoxRow key={session.id} className={i === pick ? "is-pick" : ""}>
                <span
                  role="button"
                  tabIndex={-1}
                  className="resume-row"
                  onMouseEnter={() => setPick(i)}
                  onClick={() => {
                    onClose();
                    navigate(`/sessions/${session.id}`);
                  }}
                  onKeyDown={() => {
                    /* the window handler owns the keyboard */
                  }}
                >
                  <span className="resume-caret">{i === pick ? "❯" : " "}</span>
                  <span className={`resume-dot is-${session.status}`}>
                    {session.status === "running" ? "●" : "○"}
                  </span>
                  <span className="resume-name">
                    {worker ? "  └─ worker" : repoShort(session.repo)}
                  </span>
                  <span className="resume-what">
                    {worker
                      ? truncate(session.task || "no task", 70)
                      : (closed ??
                        (session.last_message
                          ? truncate(session.last_message, 70)
                          : "no messages yet"))}
                  </span>
                  <span className="resume-when dim">
                    {formatRelative(session.updated_at)}
                  </span>
                </span>
              </BoxRow>
            );
          })}
        </Box>
        <p className="hintline dim">
          ↑↓ move · ⏎ open · esc close
        </p>
      </div>
    </div>
  );
}
