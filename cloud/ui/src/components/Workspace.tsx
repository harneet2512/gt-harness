import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getSession, subscribeEvents, type SessionStatus } from "../api";
import type { EventEntry } from "./TerminalFeed";
import TerminalFeed from "./TerminalFeed";
import SteeringChat from "./SteeringChat";
import ResultView from "./ResultView";

const FINISHED = new Set(["completed", "failed", "stopped"]);

export default function Workspace() {
  const { id } = useParams<{ id: string }>();
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [events, setEvents] = useState<EventEntry[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const eventIdCounter = useRef(0);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!id) return;
    getSession(id).then(setSession);
    const poll = setInterval(() => {
      getSession(id).then(setSession);
    }, 3000);
    return () => clearInterval(poll);
  }, [id]);

  useEffect(() => {
    if (!id) return;
    const es = subscribeEvents(id);
    esRef.current = es;

    const handleEvent = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const entry: EventEntry = {
          id: ++eventIdCounter.current,
          type: e.type === "message" ? (data.type || "unknown") : e.type,
          data,
          timestamp: data.timestamp || Date.now() / 1000,
        };
        setEvents((prev) => [...prev, entry]);
      } catch {
        // skip malformed
      }
    };

    es.addEventListener("assistant", handleEvent);
    es.addEventListener("tool_call", handleEvent);
    es.addEventListener("tool_result", handleEvent);
    es.addEventListener("steering", handleEvent);
    es.addEventListener("lifecycle", handleEvent);
    es.addEventListener("error", handleEvent);
    es.onmessage = handleEvent;

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [id]);

  useEffect(() => {
    if (!session?.started_at || FINISHED.has(session.status)) return;
    const interval = setInterval(() => {
      setElapsed(Math.floor(Date.now() / 1000 - session.started_at!));
    }, 1000);
    return () => clearInterval(interval);
  }, [session?.started_at, session?.status]);

  if (!id) return null;

  const isRunning = session?.status === "running";
  const isFinished = session ? FINISHED.has(session.status) : false;

  function formatElapsed(s: number): string {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  function repoShort(url: string) {
    return url.replace("https://github.com/", "");
  }

  return (
    <div className="workspace">
      <div className="workspace-bar">
        <Link to="/" style={{ fontSize: 16, fontWeight: 700, color: "var(--accent)" }}>
          &larr;
        </Link>
        {session && (
          <>
            <span className={`status-badge status-${session.status}`}>
              {session.status}
            </span>
            <span>
              <span className="label">Repo</span>{" "}
              <span className="value">{repoShort(session.repo)}</span>
            </span>
            <span>
              <span className="label">Model</span>{" "}
              <span className="value">{session.model}</span>
            </span>
            <span className="stat">
              Steps: {session.steps} | Cost: ${session.cost.toFixed(3)}
              {isRunning && ` | ${formatElapsed(elapsed)}`}
            </span>
          </>
        )}
      </div>

      <TerminalFeed events={events} />

      {isRunning && <SteeringChat sessionId={id} isRunning />}

      {isFinished && <ResultView sessionId={id} />}
    </div>
  );
}
