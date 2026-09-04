import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  EVENT_TYPES,
  getSession,
  parseEventFrame,
  subscribeEvents,
  TERMINAL_STATUSES,
  type SessionEvent,
  type SessionStatus,
} from "../api";
import TerminalFeed from "./TerminalFeed";
import SteeringChat from "./SteeringChat";
import ResultView from "./ResultView";

const POLL_MS = 3000;
const RECONNECT_MS = 1500;
const RECONNECT_MAX_MS = 30_000;
const RECONNECT_MAX_ATTEMPTS = 8;
const IDLE_CLOSE_MS = 3000;

type StreamState = "connecting" | "live" | "retrying" | "closed";

const STREAM_LABEL: Record<StreamState, string> = {
  connecting: "connecting...",
  live: "live",
  retrying: "reconnecting...",
  closed: "disconnected",
};

interface LiveStats {
  steps?: number;
  cost?: number;
}

export default function Workspace() {
  const { id } = useParams<{ id: string }>();
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [live, setLive] = useState<LiveStats>({});
  const [stream, setStream] = useState<StreamState>("connecting");
  const [now, setNow] = useState(() => Date.now() / 1000);

  // Highest envelope id rendered so far — replayed on reconnect via ?after_id.
  const lastEventId = useRef(0);
  const seenIds = useRef<Set<number>>(new Set());
  const synthId = useRef(0);
  const streamDone = useRef(false);
  const attempts = useRef(0);
  const esRef = useRef<EventSource | null>(null);

  const refreshSession = useCallback(() => {
    if (!id) return;
    getSession(id)
      .then(setSession)
      .catch(() => {
        /* transient: the poll or the next event will retry */
      });
  }, [id]);

  const isFinished = session ? TERMINAL_STATUSES.has(session.status) : false;
  const isRunning = session?.status === "running";

  // Poll session metadata until the session reaches a terminal status.
  useEffect(() => {
    if (!id) return;
    refreshSession();
    if (isFinished) return;
    const poll = setInterval(refreshSession, POLL_MS);
    return () => clearInterval(poll);
  }, [id, isFinished, refreshSession]);

  // SSE: one listener per event type (frames carry `event:`, so onmessage
  // would never fire), de-duplicated by envelope id, reconnecting with
  // ?after_id so a dropped connection does not replay what we already have.
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const closeStream = (next: StreamState = "closed") => {
      esRef.current?.close();
      esRef.current = null;
      setStream(next);
    };

    const ingest = (raw: unknown) => {
      const event = parseEventFrame(raw);
      if (!event) return;

      if (Number.isFinite(event.id)) {
        if (seenIds.current.has(event.id)) return;
        seenIds.current.add(event.id);
        if (event.id > lastEventId.current) lastEventId.current = event.id;
      } else {
        // Server omitted an id; keep it renderable with a synthetic key.
        event.id = -++synthId.current;
      }

      setEvents((prev) => [...prev, event]);

      if (event.type === "assistant" || event.type === "lifecycle") {
        const { n_calls, cost } = event.data;
        setLive((prev) => ({
          steps: typeof n_calls === "number" ? n_calls : prev.steps,
          cost: typeof cost === "number" ? cost : prev.cost,
        }));
      }

      if (
        event.type === "lifecycle" &&
        TERMINAL_STATUSES.has(String(event.data.status))
      ) {
        // Terminal lifecycle frame: the server ends the generator here.
        streamDone.current = true;
        closeStream();
        refreshSession();
      }
    };

    const scheduleRetry = () => {
      if (cancelled || streamDone.current) return;
      if (attempts.current >= RECONNECT_MAX_ATTEMPTS) {
        // Permanently broken (deleted session, expired cookie, server down).
        // Give up rather than hammer the endpoint forever.
        streamDone.current = true;
        setStream("closed");
        return;
      }
      const delay = Math.min(
        RECONNECT_MS * 2 ** attempts.current,
        RECONNECT_MAX_MS,
      );
      attempts.current += 1;
      setStream("retrying");
      retry = setTimeout(connect, delay);
    };

    const connect = () => {
      if (cancelled || streamDone.current) return;
      const es = subscribeEvents(id, lastEventId.current);
      esRef.current = es;

      const onFrame = (e: Event) => ingest((e as MessageEvent).data);
      for (const type of EVENT_TYPES) es.addEventListener(type, onFrame);
      es.onopen = () => {
        attempts.current = 0;
        setStream("live");
      };
      es.onerror = (e: Event) => {
        // A server frame with `event: error` is dispatched as an "error" event
        // on the EventSource itself, so this handler sees it too — but onFrame
        // (registered first) already rendered it and the socket is healthy.
        // Only a data-less event is an actual transport failure.
        if (typeof (e as MessageEvent).data === "string") return;

        es.close();
        if (esRef.current === es) esRef.current = null;
        if (cancelled || streamDone.current) {
          setStream("closed");
          return;
        }
        setStream("retrying");

        // The runner ends a finished run by closing the generator rather than
        // by emitting a terminal lifecycle frame, so ask the server whether the
        // stream ended because the run is over before reconnecting.
        getSession(id)
          .then((s) => {
            if (cancelled || streamDone.current) return;
            setSession(s);
            if (TERMINAL_STATUSES.has(s.status)) {
              streamDone.current = true;
              setStream("closed");
              return;
            }
            scheduleRetry();
          })
          .catch((err: unknown) => {
            if (cancelled || streamDone.current) return;
            // The session is gone or we are no longer authenticated: retrying
            // cannot help.
            if (
              err instanceof ApiError &&
              (err.status === 404 || err.status === 401 || err.status === 403)
            ) {
              streamDone.current = true;
              setStream("closed");
              return;
            }
            scheduleRetry();
          });
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retry) clearTimeout(retry);
      closeStream();
    };
  }, [id, refreshSession]);

  // A run that finished before (or during) this view's subscription leaves the
  // server generator parked on an empty queue: it replays history and then
  // never closes. Once the session is terminal and the replay has gone quiet,
  // drop the socket instead of holding it open forever.
  useEffect(() => {
    if (!isFinished || streamDone.current) return;
    const idle = setTimeout(() => {
      streamDone.current = true;
      esRef.current?.close();
      esRef.current = null;
      setStream("closed");
    }, IDLE_CLOSE_MS);
    return () => clearTimeout(idle);
  }, [isFinished, events.length]);

  // Wall clock for the elapsed counter.
  useEffect(() => {
    if (!isRunning) return;
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(tick);
  }, [isRunning]);

  const elapsed = useMemo(() => {
    if (!session?.started_at) return null;
    const end = session.finished_at ?? (isRunning ? now : null);
    if (end === null) return null;
    return Math.max(0, Math.floor(end - session.started_at));
  }, [session?.started_at, session?.finished_at, isRunning, now]);

  if (!id) return null;

  const steps = live.steps ?? session?.steps ?? 0;
  const cost = live.cost ?? session?.cost ?? 0;

  return (
    <div className="workspace">
      <div className="workspace-bar">
        <Link
          to="/"
          style={{ fontSize: 16, fontWeight: 700, color: "var(--accent)" }}
        >
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
            <span>
              <span className="label">GT</span>{" "}
              <span className="value">{session.gt_mode}</span>
            </span>
            <span className="stat">
              Steps: {steps} | Cost: ${cost.toFixed(3)}
              {elapsed !== null && ` | ${formatElapsed(elapsed)}`}
            </span>
            {!isFinished && (
              <span className="label">stream: {STREAM_LABEL[stream]}</span>
            )}
          </>
        )}
      </div>

      <TerminalFeed events={events} />

      {isRunning && <SteeringChat sessionId={id} isRunning />}

      {isFinished && <ResultView sessionId={id} />}
    </div>
  );
}

function formatElapsed(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

function repoShort(url: string): string {
  return url.replace("https://github.com/", "").replace(/\.git$/, "");
}
