import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  ApiError,
  closeSession,
  EMPTY_GRAPH,
  getDiff,
  getGraph,
  getMessages,
  getReceipts,
  getSession,
  getTree,
  lifecycleToSessionStatus,
  sendMessage,
  stopSession,
  type Message,
  type Receipt,
  type Session,
  type SessionDiff,
  type SessionEvent,
  type SessionGraph,
  type TreeFile,
} from "./api";
import { chatReducer, emptyChat, type ChatState } from "./chatState";
import { WRITES } from "./trail";
import { useSessionStream } from "./useSessionStream";

const CREATING_POLL_MS = 4000;
/** A write settles before the diff is worth asking for again. */
const DIFF_DEBOUNCE_MS = 800;

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** A graph with no relations, so a server without `/graph` still draws. */
function fromTree(files: readonly TreeFile[]): SessionGraph {
  return {
    base_sha: "",
    gt: false,
    nodes: files.map((file) => {
      const cut = file.path.lastIndexOf(".");
      const slash = file.path.indexOf("/");
      return {
        id: file.path,
        path: file.path,
        size: file.size,
        lang: cut > 0 ? file.path.slice(cut + 1) : "",
        dir: slash > 0 ? file.path.slice(0, slash) : "",
      };
    }),
    edges: [],
  };
}

export interface SessionData {
  session: Session | null;
  chat: ChatState;
  phase: string | null;
  loadError: string | null;
  sendError: string | null;
  isRunning: boolean;
  /** Bumped on every `turn_started`, so views can follow the live turn. */
  turnEpoch: number;
  now: number;

  tree: readonly TreeFile[];
  graph: SessionGraph;
  diff: SessionDiff | null;
  diffError: string | null;
  diffLoading: boolean;
  receipts: readonly Receipt[];
  receiptsError: string | null;
  receiptsLoading: boolean;

  send: (content: string) => Promise<boolean>;
  stop: () => void;
  close: () => void;
  reloadDiff: () => void;
  reloadReceipts: () => void;
}

/**
 * Everything one session is: its record, its thread, and the four workspace
 * snapshots the graph and the panels read. Presentation lives elsewhere.
 */
export function useSessionData(sessionId: string | null): SessionData {
  const [session, setSession] = useState<Session | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [turnEpoch, setTurnEpoch] = useState(0);
  const [now, setNow] = useState(() => Date.now() / 1000);

  const [chat, dispatch] = useReducer(chatReducer, emptyChat);
  const tempCounter = useRef(0);

  const [tree, setTree] = useState<readonly TreeFile[]>([]);
  const [graph, setGraph] = useState<SessionGraph>(EMPTY_GRAPH);
  const [diff, setDiff] = useState<SessionDiff | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [receipts, setReceipts] = useState<readonly Receipt[]>([]);
  const [receiptsError, setReceiptsError] = useState<string | null>(null);
  const [receiptsLoading, setReceiptsLoading] = useState(false);

  const refetchSession = useCallback(() => {
    if (!sessionId) return;
    getSession(sessionId)
      .then(setSession)
      .catch(() => {
        /* transient — the stream or the next action will refresh it */
      });
  }, [sessionId]);

  /* ---- initial load ---- */
  useEffect(() => {
    if (!sessionId) {
      setSession(null);
      return;
    }
    let cancelled = false;

    Promise.all([getSession(sessionId), getMessages(sessionId)])
      .then(([nextSession, messages]) => {
        if (cancelled) return;
        setSession(nextSession);
        dispatch({ type: "hydrate", messages });
        setLoadError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(message(err));
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  /* ---- snapshots ---- */
  const loadDiff = useCallback(async () => {
    if (!sessionId) return;
    setDiffLoading(true);
    try {
      setDiff(await getDiff(sessionId));
      setDiffError(null);
    } catch (err) {
      setDiffError(message(err));
    } finally {
      setDiffLoading(false);
    }
  }, [sessionId]);

  const loadReceipts = useCallback(async () => {
    if (!sessionId) return;
    setReceiptsLoading(true);
    try {
      setReceipts(await getReceipts(sessionId));
      setReceiptsError(null);
    } catch (err) {
      setReceiptsError(message(err));
    } finally {
      setReceiptsLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;

    void (async () => {
      let files: readonly TreeFile[] = [];
      try {
        files = (await getTree(sessionId)).files;
        if (!cancelled) setTree(files);
      } catch {
        /* the graph may still answer */
      }
      try {
        const next = await getGraph(sessionId);
        if (!cancelled) setGraph(next);
      } catch {
        // A server without /graph still has particles — just no filaments.
        if (!cancelled) setGraph(files.length > 0 ? fromTree(files) : EMPTY_GRAPH);
      }
    })();

    void loadDiff();
    void loadReceipts();

    return () => {
      cancelled = true;
    };
  }, [sessionId, refreshKey, loadDiff, loadReceipts]);

  /* ---- the diff, while the agent is still writing ---- */
  const diffTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleDiff = useCallback(() => {
    if (diffTimer.current) clearTimeout(diffTimer.current);
    diffTimer.current = setTimeout(() => {
      diffTimer.current = null;
      void loadDiff();
    }, DIFF_DEBOUNCE_MS);
  }, [loadDiff]);

  useEffect(
    () => () => {
      if (diffTimer.current) clearTimeout(diffTimer.current);
    },
    [],
  );

  /* ---- live stream ---- */
  const onEvent = useCallback(
    (event: SessionEvent) => {
      dispatch({ type: "event", event });

      switch (event.type) {
        case "lifecycle": {
          const raw = String(event.data.status);
          setPhase(raw);
          const mapped = lifecycleToSessionStatus(raw);
          if (mapped) {
            setSession((prev) =>
              prev
                ? {
                    ...prev,
                    status: mapped,
                    current_turn_id:
                      mapped === "running" ? prev.current_turn_id : null,
                  }
                : prev,
            );
          }
          if (raw === "stopped" || raw.startsWith("gt_")) refetchSession();
          if (raw === "indexing" || raw === "idle") {
            setRefreshKey((k) => k + 1);
          }
          break;
        }

        case "turn_started": {
          const turnId = String(event.data.turn_id ?? "");
          setSession((prev) =>
            prev
              ? { ...prev, status: "running", current_turn_id: turnId }
              : prev,
          );
          setTurnEpoch((n) => n + 1);
          break;
        }

        case "tool_result": {
          const command = String(event.data.command ?? "");
          if (command && WRITES.test(command)) scheduleDiff();
          break;
        }

        case "turn_finished": {
          setSession((prev) =>
            prev
              ? {
                  ...prev,
                  status: prev.status === "running" ? "idle" : prev.status,
                  current_turn_id: null,
                }
              : prev,
          );
          // New files may exist now: the graph is refetched with everything.
          setRefreshKey((k) => k + 1);
          refetchSession();
          break;
        }

        default:
          break;
      }
    },
    [refetchSession, scheduleDiff],
  );

  useSessionStream(sessionId ?? undefined, onEvent);

  /* ---- polling and the stopwatch ---- */
  const isCreating = session?.status === "creating";
  useEffect(() => {
    if (!isCreating) return;
    const poll = setInterval(refetchSession, CREATING_POLL_MS);
    return () => clearInterval(poll);
  }, [isCreating, refetchSession]);

  const isRunning = session?.status === "running";
  useEffect(() => {
    if (!isRunning) return;
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(tick);
  }, [isRunning]);

  /* ---- actions ---- */
  const send = useCallback(
    async (content: string): Promise<boolean> => {
      if (!sessionId) return false;
      const tempId = `local-${++tempCounter.current}`;
      const optimistic: Message = {
        id: tempId,
        session_id: sessionId,
        turn_id: null,
        role: "user",
        content,
        created_at: Date.now() / 1000,
        meta: { pending: true },
      };
      dispatch({ type: "optimistic", message: optimistic });
      setSendError(null);

      try {
        const result = await sendMessage(sessionId, content);
        dispatch({ type: "settle", tempId, message: result.message });
        if (result.delivery === "turn_started") {
          setTurnEpoch((n) => n + 1);
          setSession((prev) =>
            prev
              ? {
                  ...prev,
                  status: "running",
                  current_turn_id:
                    result.message.turn_id ?? prev.current_turn_id,
                }
              : prev,
          );
        }
        return true;
      } catch (err) {
        dispatch({ type: "drop", id: tempId });
        if (err instanceof ApiError && err.status === 409) {
          setSendError(
            `This session is ${session?.status ?? "not ready"} and cannot accept messages.`,
          );
          refetchSession();
        } else {
          setSendError(message(err));
        }
        return false;
      }
    },
    [sessionId, session?.status, refetchSession],
  );

  const stop = useCallback(() => {
    if (!sessionId) return;
    stopSession(sessionId)
      .catch((err: unknown) => setSendError(message(err)))
      .finally(refetchSession);
  }, [sessionId, refetchSession]);

  const close = useCallback(() => {
    if (!sessionId) return;
    if (!window.confirm("Close this session? The workspace is discarded.")) {
      return;
    }
    closeSession(sessionId)
      .catch((err: unknown) => setSendError(message(err)))
      .finally(refetchSession);
  }, [sessionId, refetchSession]);

  return {
    session,
    chat,
    phase,
    loadError,
    sendError,
    isRunning: Boolean(isRunning),
    turnEpoch,
    now,
    tree,
    graph,
    diff,
    diffError,
    diffLoading,
    receipts,
    receiptsError,
    receiptsLoading,
    send,
    stop,
    close,
    reloadDiff: () => void loadDiff(),
    reloadReceipts: () => void loadReceipts(),
  };
}
