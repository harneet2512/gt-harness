import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  agentIdOf,
  ApiError,
  applyAgent,
  closeSession,
  EMPTY_GRAPH,
  getDiff,
  getGraph,
  getMessages,
  getReceipts,
  getSession,
  getTree,
  lifecycleToSessionStatus,
  listAgents,
  registerExternalAgent,
  sendMessage,
  spawnAgents,
  stopSession,
  type Message,
  type Receipt,
  type ExternalAgentRegistration,
  type Session,
  type SessionDiff,
  type SessionEvent,
  type SessionGraph,
  type TreeFile,
} from "./api";
import { chatReducer, emptyChat, type ChatState } from "./chatState";
import { gtErrorOf, nextSession, shouldApply } from "./sessionSync";
import { WRITES } from "./trail";
import { useSessionStream } from "./useSessionStream";

/* A session that is coming up is a session someone is watching: the first
   message cannot be posted until it reaches `idle`, and the SSE frame can
   be missed. 1.5s is the cost of not making them wait for nothing. */
const CREATING_POLL_MS = 1500;
/** A write settles before the diff is worth asking for again. */
const DIFF_DEBOUNCE_MS = 800;
/**
 * How long a `turn_started` frame is given to carry its own prompt before
 * the thread is re-read from `/messages`. The frame does carry it now
 * (HAR-84 G-09) — this is the belt to that braces: a server that predates
 * the field, a frame that lost a race, a tab that reconnected mid-turn.
 */
const MESSAGES_REFETCH_MS = 700;

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
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
  /** Why the GT index is unavailable, as the lifecycle frame reported it. */
  gtError: string | null;
  /** Why the session failed, in the server's words. Null until it does. */
  failureError: string | null;
  loadError: string | null;
  sendError: string | null;
  isRunning: boolean;
  /** Stop was pressed and the turn has not reported back yet. */
  isStopping: boolean;
  /** A sent message is queued and the agent has not picked it up yet. */
  steeringQueued: boolean;
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

  /* ---- worker agents ---- */
  /**
   * Spawn one worker per task. Resolves to null when they were created, or
   * to the server's own `detail` when it refused — a 429 over a cap, a 409
   * on a worker trying to spawn workers.
   */
  spawn: (tasks: readonly string[]) => Promise<string | null>;
  /** 3-way merge a worker's diff into this workspace. Conflicts land on the card. */
  applyWorker: (workerId: string) => Promise<void>;
  reloadWorkers: () => void;
  /**
   * Register a Claude Code / Codex session against this one. Resolves to
   * the ingest URL and the token a local bridge authenticates with, or to
   * the server's own words for why it would not.
   */
  connectExternal: (kind: string) => Promise<ConnectResult>;
}

export type ConnectResult =
  | { registration: ExternalAgentRegistration; error: null }
  | { registration: null; error: string };

/**
 * Everything one session is: its record, its thread, and the four workspace
 * snapshots the graph and the panels read. Presentation lives elsewhere.
 */
export function useSessionData(sessionId: string | null): SessionData {
  const [session, setSession] = useState<Session | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  /* `undefined` until the stream says something about GT; the session
     row answers for it until then. See `gtErrorOf`. */
  const [liveGtError, setLiveGtError] = useState<string | null | undefined>(
    undefined,
  );
  /* What the `lifecycle failed` frame said. "This session failed." is not an
     explanation, and the server has one. */
  const [failureError, setFailureError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [turnEpoch, setTurnEpoch] = useState(0);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [stopping, setStopping] = useState(false);
  /* Messages accepted while a turn was running, not yet delivered. The
     composer's "delivered at the next step" hint is bound to this rather than
     to `isRunning`, so it disappears the moment the agent picks the message
     up instead of lingering for the rest of the turn. */
  const [queuedIds, setQueuedIds] = useState<readonly string[]>([]);

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

  /* Two refetches are routinely in flight at once — `stop()` fires one and the
     `turn_finished` frame fires another a moment later. Their responses can
     arrive out of order, and the older one carries a still-running row: without
     a guard it overwrites the settled state and the header stays "Working"
     forever. Responses are therefore applied in issue order only, and a
     snapshot that names a turn the stream has already ended is discarded. */
  const fetchSeq = useRef(0);
  const appliedSeq = useRef(0);
  const finishedTurns = useRef<Set<string>>(new Set());

  const refetchSession = useCallback(() => {
    if (!sessionId) return;
    const seq = (fetchSeq.current += 1);
    getSession(sessionId)
      .then((next) => {
        if (!shouldApply(appliedSeq.current, seq)) return;
        appliedSeq.current = seq;
        setSession((prev) => nextSession(prev, next, finishedTurns.current));
      })
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

  /* The record behind the worker cards. A reload has them back from here
     before a single frame arrives; the stream then fills in the live
     detail. Failure is silent: no workers is the normal case. */
  const loadWorkers = useCallback(async () => {
    if (!sessionId) return;
    try {
      dispatch({ type: "workers", rows: await listAgents(sessionId) });
    } catch {
      /* a server without /agents simply has none */
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
    void loadWorkers();

    return () => {
      cancelled = true;
    };
  }, [sessionId, refreshKey, loadDiff, loadReceipts, loadWorkers]);

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

  /* ---- the thread, when a frame may not have carried it ---- */
  const messagesTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleMessages = useCallback(() => {
    if (!sessionId) return;
    if (messagesTimer.current) clearTimeout(messagesTimer.current);
    messagesTimer.current = setTimeout(() => {
      messagesTimer.current = null;
      getMessages(sessionId)
        .then((messages) => dispatch({ type: "hydrate", messages }))
        .catch(() => {
          /* the stream is the primary path; this was the fallback */
        });
    }, MESSAGES_REFETCH_MS);
  }, [sessionId]);

  useEffect(
    () => () => {
      if (messagesTimer.current) clearTimeout(messagesTimer.current);
    },
    [],
  );

  /* ---- live stream ---- */
  const onEvent = useCallback(
    (event: SessionEvent) => {
      dispatch({ type: "event", event });

      /* A mirrored worker frame is not this session's work. It has already
         gone to that worker's card; letting it fall through would set the
         parent "running" on a turn that is not its own, bump the turn epoch
         and refetch the parent's diff for a write in another workspace. */
      if (agentIdOf(event)) return;

      switch (event.type) {
        case "lifecycle": {
          const raw = String(event.data.status);
          setPhase(raw);
          const mapped = lifecycleToSessionStatus(raw);
          if (mapped) {
            /* A session can end while you are watching it — the reaper
               collects an idle workspace and the frame is the only notice
               anyone gets. `reason` travels with it so the page can say
               *why* it went, not just that it did. */
            const reason = str(event.data.reason) || null;
            setSession((prev) =>
              prev
                ? {
                    ...prev,
                    status: mapped,
                    closed_reason:
                      mapped === "closed" || mapped === "failed"
                        ? (reason ?? prev.closed_reason ?? null)
                        : prev.closed_reason,
                    current_turn_id:
                      mapped === "running" ? prev.current_turn_id : null,
                  }
                : prev,
            );
          }
          /* GT never reaches the session row through the stream, only
             through a refetch that may be seconds away — and when the index
             fails the reader deserves to know before the first turn ends.
             The frame is therefore applied to the session directly. */
          if (raw === "gt_unavailable") {
            setLiveGtError(str(event.data.error) || null);
            setSession((prev) =>
              prev ? { ...prev, gt_status: "unavailable" } : prev,
            );
          }
          if (mapped === "failed") {
            setFailureError(str(event.data.error) || null);
          }
          if (raw === "gt_ready") {
            setLiveGtError(null);
            setSession((prev) =>
              prev ? { ...prev, gt_status: "ready" } : prev,
            );
          }
          if (raw === "stopped" || raw.startsWith("gt_")) refetchSession();
          if (raw === "indexing" || raw === "idle") {
            setRefreshKey((k) => k + 1);
          }
          break;
        }

        case "steering": {
          const delivered = String(event.data.message_id ?? "");
          if (delivered) {
            setQueuedIds((ids) => ids.filter((id) => id !== delivered));
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
          // Debounced fallback for a prompt the frame did not carry.
          scheduleMessages();
          break;
        }

        case "tool_result": {
          const command = String(event.data.command ?? "");
          if (command && WRITES.test(command)) scheduleDiff();
          break;
        }

        case "turn_finished": {
          const finishedId = String(event.data.turn_id ?? "");
          if (finishedId) finishedTurns.current.add(finishedId);
          setSession((prev) =>
            prev
              ? {
                  ...prev,
                  status: prev.status === "running" ? "idle" : prev.status,
                  current_turn_id: null,
                }
              : prev,
          );
          setStopping(false);
          // Anything still queued was folded into the turn that just ended,
          // or will start a new one; either way nothing is waiting now.
          setQueuedIds([]);
          // New files may exist now: the graph is refetched with everything.
          setRefreshKey((k) => k + 1);
          refetchSession();
          break;
        }

        /* The parent's own frames about its workers. The cards are already
           updated; what is left is the records they are backed by. */
        case "agent_spawned":
        case "agent_closed":
          void loadWorkers();
          break;

        case "agent_report":
          void loadWorkers();
          refetchSession();
          break;

        case "agent_applied":
          /* A worker's patch is in this workspace now: the diff, the graph
             and the tree all say something different than they did. */
          void loadWorkers();
          void loadDiff();
          setRefreshKey((k) => k + 1);
          break;

        default:
          break;
      }
    },
    [refetchSession, scheduleDiff, scheduleMessages, loadWorkers, loadDiff],
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
        if (result.delivery === "queued_for_running_turn") {
          setQueuedIds((ids) => [...ids, result.message.id]);
        }
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
    // The client knows it pressed Stop before any frame says so; the status
    // line reads "Stopping…" from here until `turn_finished` lands.
    setStopping(true);
    stopSession(sessionId)
      .catch((err: unknown) => {
        setStopping(false);
        setSendError(message(err));
      })
      .finally(refetchSession);
  }, [sessionId, refetchSession]);

  /* ---- worker agents ---- */

  const spawn = useCallback(
    async (tasks: readonly string[]): Promise<string | null> => {
      if (!sessionId) return "There is no session to spawn from.";
      try {
        const { workers } = await spawnAgents(sessionId, tasks);
        dispatch({ type: "workers", rows: workers });
        return null;
      } catch (err) {
        /* 429 over a cap, 409 for a worker-of-worker, 400 for a model the
           provider will not serve: the server's `detail` says which, and
           saying it verbatim beats any paraphrase this page could write. */
        return message(err);
      }
    },
    [sessionId],
  );

  const applyWorker = useCallback(
    async (workerId: string): Promise<void> => {
      if (!sessionId) return;
      dispatch({ type: "worker_applying", workerId });
      try {
        const result = await applyAgent(sessionId, workerId);
        dispatch({ type: "worker_applied", workerId, files: result.files });
        await loadDiff();
        setRefreshKey((k) => k + 1);
        void loadWorkers();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          dispatch({
            type: "worker_conflict",
            workerId,
            conflicts: err.conflicts,
            detail: err.message,
          });
          return;
        }
        dispatch({ type: "worker_apply_error", workerId, error: message(err) });
      }
    },
    [sessionId, loadDiff, loadWorkers],
  );

  /**
   * A local agent attaching itself to this session. The row it creates is
   * an agent like any other, so the list is reloaded the moment it exists
   * rather than waiting for its first frame.
   */
  const connectExternal = useCallback(
    async (kind: string): Promise<ConnectResult> => {
      if (!sessionId) {
        return { registration: null, error: "There is no session to connect to." };
      }
      try {
        /* `label` is required and may not be blank: it is what the card
           is called until the agent says something better. The kind is
           the honest default — we do not know yet what it will work on. */
        const registration = await registerExternalAgent(sessionId, {
          agent_kind: kind,
          label: kind,
        });
        void loadWorkers();
        return { registration, error: null };
      } catch (err) {
        /* A 404 is a server without the route, a 409 a session that cannot
           take one, a 429 a cap. The server's `detail` says which, and
           saying it verbatim beats any paraphrase this page could write. */
        return { registration: null, error: message(err) };
      }
    },
    [sessionId, loadWorkers],
  );

  /**
   * Close the session. The confirmation is **not** here: everything else in
   * this release asks in the transcript, and the one destructive action
   * opening an OS modal was both off-key and untestable from a headless
   * client (HAR-84 P2-9). `SynapsePage` asks; this does it.
   */
  const close = useCallback(() => {
    if (!sessionId) return;
    closeSession(sessionId)
      .catch((err: unknown) => setSendError(message(err)))
      .finally(refetchSession);
  }, [sessionId, refetchSession]);

  return {
    session,
    chat,
    phase,
    gtError: gtErrorOf(liveGtError, session?.gt_error),
    failureError,
    loadError,
    sendError,
    isRunning: Boolean(isRunning),
    isStopping: Boolean(isRunning) && stopping,
    steeringQueued: queuedIds.length > 0,
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
    spawn,
    applyWorker,
    reloadWorkers: () => void loadWorkers(),
    connectExternal,
  };
}
