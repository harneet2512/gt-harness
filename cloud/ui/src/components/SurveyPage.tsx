import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  closeSession,
  COMPOSER_LOCKED,
  getDiff,
  getMessages,
  getReceipts,
  getSession,
  getTree,
  lifecycleToSessionStatus,
  sendMessage,
  stopSession,
  TERMINAL_STATUSES,
  type Message,
  type Receipt,
  type Session,
  type SessionDiff,
  type SessionEvent,
  type TreeFile,
} from "../api";
import { buildGroups, chatReducer, emptyChat } from "../chatState";
import {
  buildSteps,
  EMPTY_INDEX,
  indexFiles,
  surveyView,
  type SurveyStep,
} from "../survey";
import { useMedia } from "../useSize";
import { useSessionStream } from "../useSessionStream";
import Field, { type TurnOption } from "./Field";
import RadioLog from "./RadioLog";
import type { RadioMode } from "./RadioIndicator";

const CREATING_POLL_MS = 4000;
const NARROW = "(max-width: 1100px)";

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** The whole instrument: radio log on the left, terrain on the right. */
export default function SurveyPage() {
  const { id } = useParams<{ id: string }>();
  const sessionId = id ?? null;

  const [session, setSession] = useState<Session | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [pickedTurnId, setPickedTurnId] = useState<string | null>(null);
  const [scrub, setScrub] = useState<number | null>(null);
  const [fieldOpen, setFieldOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);

  const [chat, dispatch] = useReducer(chatReducer, emptyChat);
  const tempCounter = useRef(0);

  const narrow = useMedia(NARROW);

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
          if (
            raw === "stopped" ||
            raw === "gt_ready" ||
            raw === "gt_unavailable"
          ) {
            refetchSession();
          }
          if (raw === "indexing" || raw === "idle") {
            // The workspace has files now; go and draw them.
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
          // Follow the live turn unless the reader is inspecting an older one.
          setPickedTurnId(null);
          setScrub(null);
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
          setRefreshKey((k) => k + 1);
          refetchSession();
          break;
        }

        default:
          break;
      }
    },
    [refetchSession],
  );

  useSessionStream(sessionId ?? undefined, onEvent);

  /* ---- workspace snapshots: terrain, diff, receipts ---- */
  const [tree, setTree] = useState<readonly TreeFile[]>([]);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [diff, setDiff] = useState<SessionDiff | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [receipts, setReceipts] = useState<readonly Receipt[]>([]);
  const [receiptsError, setReceiptsError] = useState<string | null>(null);
  const [receiptsLoading, setReceiptsLoading] = useState(false);

  const loadTree = useCallback(async () => {
    if (!sessionId) return;
    try {
      const next = await getTree(sessionId);
      setTree(next.files);
      setTreeError(null);
    } catch (err) {
      setTreeError(message(err));
    }
  }, [sessionId]);

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
    void loadTree();
    void loadDiff();
    void loadReceipts();
  }, [loadTree, loadDiff, loadReceipts, refreshKey]);

  /* ---- while the workspace is being provisioned, poll ---- */
  const isCreating = session?.status === "creating";
  useEffect(() => {
    if (!isCreating) return;
    const poll = setInterval(refetchSession, CREATING_POLL_MS);
    return () => clearInterval(poll);
  }, [isCreating, refetchSession]);

  /* ---- the stopwatch ---- */
  const isRunning = session?.status === "running";
  useEffect(() => {
    if (!isRunning) return;
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(tick);
  }, [isRunning]);

  /* ---- derived survey ---- */
  const groups = useMemo(() => buildGroups(chat), [chat]);

  const turnIds = useMemo(
    () =>
      groups
        .filter((group) => group.kind === "turn")
        .map((group) => (group as { turnId: string }).turnId),
    [groups],
  );

  const fileIndex = useMemo(
    () => (tree.length > 0 ? indexFiles(tree) : EMPTY_INDEX),
    [tree],
  );

  const stepsByTurn = useMemo(() => {
    const out: Record<string, SurveyStep[]> = {};
    for (const turnId of turnIds) {
      out[turnId] = buildSteps(chat.turns[turnId], fileIndex);
    }
    return out;
  }, [turnIds, chat.turns, fileIndex]);

  const lastTurnId = turnIds.length > 0 ? turnIds[turnIds.length - 1] : null;
  const selectedTurnId =
    pickedTurnId ?? session?.current_turn_id ?? lastTurnId ?? null;

  const steps = useMemo(
    () => (selectedTurnId ? (stepsByTurn[selectedTurnId] ?? []) : []),
    [selectedTurnId, stepsByTurn],
  );

  const edited = useMemo(
    () => new Set((diff?.files ?? []).map((file) => file.path)),
    [diff],
  );

  const live = scrub === null;
  const scrubPosition = live
    ? steps.length
    : Math.min(Math.max(scrub, 1), Math.max(steps.length, 1));

  const view = useMemo(
    () => surveyView(steps, scrubPosition),
    [steps, scrubPosition],
  );
  const hereStep =
    view.trail.length > 0 ? view.trail[view.trail.length - 1].n : null;

  const turnOptions: TurnOption[] = useMemo(
    () => turnIds.map((turnId, i) => ({ id: turnId, no: i + 1 })),
    [turnIds],
  );

  const currentTurn = session?.current_turn_id
    ? chat.turns[session.current_turn_id]
    : undefined;
  const elapsed =
    isRunning && currentTurn?.startedAt != null
      ? now - currentTurn.startedAt
      : null;

  /** The newest agent reply, which decides "waiting for you". */
  const lastAgentMessage = useMemo(() => {
    for (let i = chat.messages.length - 1; i >= 0; i -= 1) {
      if (chat.messages[i].role === "agent") return chat.messages[i];
    }
    return null;
  }, [chat.messages]);

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
          setPickedTurnId(null);
          setScrub(null);
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
            `The expedition is ${session?.status ?? "not ready"} and cannot accept messages.`,
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

  const onStop = useCallback(async () => {
    if (!sessionId) return;
    try {
      await stopSession(sessionId);
    } catch (err) {
      setSendError(message(err));
    } finally {
      refetchSession();
    }
  }, [sessionId, refetchSession]);

  const onClose = useCallback(async () => {
    if (!sessionId) return;
    if (!window.confirm("Close this expedition? The workspace is discarded.")) {
      return;
    }
    try {
      await closeSession(sessionId);
    } catch (err) {
      setSendError(message(err));
    } finally {
      refetchSession();
    }
  }, [sessionId, refetchSession]);

  /* ---- Ctrl/Cmd+Shift+Backspace cuts the transmission ---- */
  useEffect(() => {
    if (!isRunning) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Backspace" || !e.shiftKey) return;
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      void onStop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isRunning, onStop]);

  /* ---- presentation ---- */
  const status = String(session?.status ?? (sessionId ? "creating" : "idle"));
  const terminal = TERMINAL_STATUSES.has(status);
  const waitingForUser =
    !isRunning && lastAgentMessage?.meta.finish_reason === "question";

  const radioMode: RadioMode = !sessionId
    ? "standing-by"
    : isRunning
      ? "working"
      : status === "creating"
        ? "surveying"
        : terminal
          ? "off-air"
          : waitingForUser
            ? "waiting"
            : "standing-by";

  const fieldHidden = narrow && !fieldOpen;

  return (
    <div className="survey">
      <RadioLog
        sessionId={sessionId}
        session={session}
        chat={chat}
        groups={groups}
        stepsByTurn={stepsByTurn}
        edited={edited}
        selectedTurnId={selectedTurnId}
        currentTurnId={session?.current_turn_id ?? null}
        onSelectTurn={(turnId) => {
          setPickedTurnId(turnId);
          setScrub(null);
        }}
        cutoff={scrubPosition}
        running={Boolean(isRunning)}
        terminal={terminal}
        radioMode={radioMode}
        phase={phase}
        elapsed={elapsed}
        now={now}
        locked={COMPOSER_LOCKED.has(status)}
        lockedReason={lockedReason(status, phase)}
        sendError={loadError ?? sendError}
        onSend={send}
        onStop={() => void onStop()}
        onClose={() => void onClose()}
        onContinue={() => void send("continue")}
        shrunk={narrow && fieldOpen}
        fieldToggle={
          narrow ? (
            <button
              type="button"
              className="btn field-toggle"
              onClick={() => setFieldOpen(!fieldOpen)}
            >
              {fieldOpen ? "hide field" : "field"}
            </button>
          ) : null
        }
      />

      <Field
        hidden={fieldHidden}
        files={tree}
        treeError={treeError}
        emptyText={
          !sessionId
            ? "Pick an expedition or start a new one"
            : status === "creating"
              ? "surveying…"
              : "no tracked files"
        }
        attention={view.attention}
        edited={edited}
        trail={view.trail}
        position={view.position}
        hereStep={hereStep}
        running={Boolean(isRunning)}
        turns={turnOptions}
        selectedTurnId={selectedTurnId}
        currentTurnId={session?.current_turn_id ?? null}
        onSelectTurn={(turnId) => {
          setPickedTurnId(turnId);
          setScrub(null);
        }}
        steps={steps}
        scrubPosition={scrubPosition}
        live={live}
        onScrub={setScrub}
        onLive={() => setScrub(null)}
        diff={diff}
        diffError={diffError}
        diffLoading={diffLoading}
        onRefreshDiff={() => void loadDiff()}
        receipts={receipts}
        receiptsError={receiptsError}
        receiptsLoading={receiptsLoading}
        onRefreshReceipts={() => void loadReceipts()}
        events={chat.events}
        gtStatus={String(session?.gt_status ?? "off")}
      />
    </div>
  );
}

function lockedReason(status: string, phase: string | null): string {
  switch (status) {
    case "creating":
      return phase === "cloning"
        ? "Cloning the repository…"
        : phase === "indexing"
          ? "Indexing the workspace…"
          : "Preparing the workspace…";
    case "closed":
      return "This expedition is closed.";
    case "failed":
      return "This expedition failed.";
    default:
      return "";
  }
}
