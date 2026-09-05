import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  EVENT_TYPES,
  getSession,
  subscribeEvents,
  TERMINAL_STATUSES,
  type SessionEvent,
} from "./api";
import { createIngest, ingestFrame } from "./streamSync";

const RECONNECT_MS = 1000;
const RECONNECT_MAX_MS = 30_000;
const RECONNECT_MAX_ATTEMPTS = 10;

export type StreamState = "connecting" | "live" | "retrying" | "closed";

export const STREAM_LABEL: Record<StreamState, string> = {
  connecting: "connecting…",
  live: "live",
  retrying: "reconnecting…",
  closed: "disconnected",
};

/**
 * One EventSource per chat page. The stream stays open across turns; it is
 * only torn down when the session reaches `closed`/`failed`, when the page
 * unmounts, or after the reconnect budget is exhausted.
 *
 * Frames are de-duplicated by envelope id and a reconnect resumes with
 * `?after_id=<highest seen>` so history is not replayed twice.
 */
export function useSessionStream(
  sessionId: string | undefined,
  onEvent: (event: SessionEvent) => void,
): StreamState {
  const [stream, setStream] = useState<StreamState>("connecting");

  const handler = useRef(onEvent);
  handler.current = onEvent;

  /* De-duplication, the resume point and the terminal test all live in
     `streamSync.ts` so they can be tested without a browser. */
  const ingest = useRef(createIngest());
  const done = useRef(false);
  const attempts = useRef(0);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const teardown = (next: StreamState) => {
      esRef.current?.close();
      esRef.current = null;
      setStream(next);
    };

    const take = (raw: unknown) => {
      const { event, terminal } = ingestFrame(ingest.current, raw);
      if (!event) return;

      handler.current(event);

      if (terminal) {
        // The server ends the generator on closed/failed.
        done.current = true;
        teardown("closed");
      }
    };

    const scheduleRetry = () => {
      if (cancelled || done.current) return;
      if (attempts.current >= RECONNECT_MAX_ATTEMPTS) {
        done.current = true;
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
      if (cancelled || done.current) return;
      const es = subscribeEvents(sessionId, ingest.current.lastEventId);
      esRef.current = es;

      const onFrame = (e: Event) => take((e as MessageEvent).data);
      for (const type of EVENT_TYPES) es.addEventListener(type, onFrame);

      es.onopen = () => {
        attempts.current = 0;
        setStream("live");
      };

      es.onerror = (e: Event) => {
        // Server error frames are named `agent_error` precisely so they do not
        // land here; a payload on this handler would still mean a live socket.
        if (typeof (e as MessageEvent).data === "string") return;

        es.close();
        if (esRef.current === es) esRef.current = null;
        if (cancelled || done.current) {
          setStream("closed");
          return;
        }
        setStream("retrying");

        // Distinguish "the run ended" from "the socket dropped" before
        // spending a reconnect attempt.
        getSession(sessionId)
          .then((session) => {
            if (cancelled || done.current) return;
            if (TERMINAL_STATUSES.has(String(session.status))) {
              done.current = true;
              setStream("closed");
              return;
            }
            scheduleRetry();
          })
          .catch((err: unknown) => {
            if (cancelled || done.current) return;
            if (
              err instanceof ApiError &&
              (err.status === 404 || err.status === 401 || err.status === 403)
            ) {
              done.current = true;
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
      teardown("closed");
    };
  }, [sessionId]);

  return stream;
}
