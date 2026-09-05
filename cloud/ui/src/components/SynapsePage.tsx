import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { COMPOSER_LOCKED } from "../api";
import { failedReason } from "../format";
import { shouldAutoOpenGraph, turnFileCount } from "../launch";
import { isOverlayMode, useLayoutMode } from "../layoutMode";
import { loadGraphOpen, loadPrefs, savePrefs, saveGraphOpen, type Prefs } from "../prefs";
import { helpText, type ParsedSlash } from "../slash";
import { useDragSize } from "../useDragSize";
import { useGraphView } from "../useGraphView";
import { useSessionData } from "../useSessionData";
import { useSessions } from "../useSessions";
import Conversation, { type LocalNote } from "./Conversation";
import GraphPanel from "./GraphPanel";
import ResumeRail from "./ResumeRail";
import SessionHeader from "./SessionHeader";
import type { StatusMode } from "./StatusLine";

const GRAPH_DEFAULT = 560;
const GRAPH_MIN = 360;
const GRAPH_MAX = 900;

const SPAWN_SOON =
  "spawning worker agents is coming — the server side is being built";

/** SYNAPSE — a transcript, with the graph a keystroke away. */
export default function SynapsePage() {
  const { id } = useParams<{ id: string }>();
  const sessionId = id ?? null;
  const location = useLocation();
  const data = useSessionData(sessionId);
  const { session, chat, graph, diff } = data;
  const { sessions, error: listError } = useSessions();

  const view = useGraphView({
    sessionId,
    chat,
    tree: data.tree,
    graph,
    diff,
    currentTurnId: session?.current_turn_id ?? null,
    turnEpoch: data.turnEpoch,
  });

  /* ---- the first message, typed on the landing page ----
     The session was created a moment ago and the server answers 409 until
     the workspace is up, so the prompt waits here — on screen — and is
     posted the instant the session reaches `idle`. */
  const firstMessage =
    (location.state as { firstMessage?: string } | null)?.firstMessage ?? null;
  const [pendingFirst, setPendingFirst] = useState<string | null>(firstMessage);
  const sentFirst = useRef(false);

  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs());
  const [gearSignal, setGearSignal] = useState(0);
  const [focusSignal, setFocusSignal] = useState(0);
  const [notes, setNotes] = useState<readonly LocalNote[]>([]);
  const noteId = useRef(0);

  const layout = useLayoutMode();
  const overlay = isOverlayMode(layout);
  const width = useDragSize(GRAPH_DEFAULT, GRAPH_MIN, GRAPH_MAX, "x-left");

  /* ---- the graph panel ---- */
  const remembered = useMemo(
    () => (sessionId ? loadGraphOpen(sessionId) : null),
    [sessionId],
  );
  const [graphOpen, setGraphOpen] = useState(remembered ?? false);
  /* An explicit choice is never overridden by the auto-expand below. */
  const autoDone = useRef(remembered !== null);

  const setGraph = useCallback(
    (open: boolean) => {
      autoDone.current = true;
      setGraphOpen(open);
      if (sessionId) saveGraphOpen(sessionId, open);
    },
    [sessionId],
  );

  const liveSteps =
    view.stepsByTurn[session?.current_turn_id ?? ""] ?? view.steps;
  const touched = turnFileCount(liveSteps);

  useEffect(() => {
    if (autoDone.current || graphOpen) return;
    if (!shouldAutoOpenGraph(touched)) return;
    autoDone.current = true;
    setGraphOpen(true);
  }, [touched, graphOpen]);

  /* ---- selection ---- */
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [inspectedId, setInspectedId] = useState<string | null>(null);
  const [pinned, setPinned] = useState(false);

  const select = useCallback(
    (nextId: string | null) => {
      setSelectedId(nextId);
      if (nextId) setInspectedId(nextId);
      else if (!pinned) setInspectedId(null);
    },
    [pinned],
  );

  const { particleId } = view;
  const selectPath = useCallback(
    (path: string) => {
      const found = particleId(path);
      if (found) setGraph(true);
      select(found);
    },
    [select, particleId, setGraph],
  );

  const closeInspector = useCallback(() => {
    setPinned(false);
    setInspectedId(null);
    setSelectedId(null);
  }, []);

  /* ---- status ---- */
  const status = String(session?.status ?? (sessionId ? "creating" : "idle"));
  const lastAgent = useMemo(() => {
    for (let i = chat.messages.length - 1; i >= 0; i -= 1) {
      if (chat.messages[i].role === "agent") return chat.messages[i];
    }
    return null;
  }, [chat.messages]);

  const mode: StatusMode = data.isRunning
    ? "working"
    : status === "creating"
      ? "preparing"
      : status === "failed"
        ? "failed"
        : status === "closed"
          ? "closed"
          : lastAgent?.meta.finish_reason === "question"
            ? "waiting"
            : "idle";

  const currentTurn = session?.current_turn_id
    ? chat.turns[session.current_turn_id]
    : undefined;
  const elapsed =
    data.isRunning && currentTurn?.startedAt != null
      ? data.now - currentTurn.startedAt
      : null;

  /* ---- deliver the landing page's prompt ---- */
  const send = data.send;
  useEffect(() => {
    if (!pendingFirst || sentFirst.current) return;
    if (status !== "idle") return;
    sentFirst.current = true;
    const content = pendingFirst;
    setPendingFirst(null);
    void send(content);
  }, [pendingFirst, status, send]);

  /* ---- keyboard ---- */
  const stop = data.stop;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.ctrlKey || e.metaKey;
      if (!meta) return;
      if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        setFocusSignal((n) => n + 1);
        return;
      }
      if (e.key === "g" || e.key === "G") {
        e.preventDefault();
        setGraph(!graphOpen);
        return;
      }
      if (e.key === "Backspace" && e.shiftKey && data.isRunning) {
        e.preventDefault();
        stop();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [data.isRunning, stop, graphOpen, setGraph]);

  const note = useCallback((role: LocalNote["role"], text: string) => {
    setNotes((prev) => [...prev, { id: (noteId.current += 1), role, text }]);
  }, []);

  const onCommand = useCallback(
    ({ command, arg }: ParsedSlash) => {
      /* The command is echoed the way a shell echoes it: what you typed
         stays in the transcript, above whatever it did. */
      note("user", `/${command.name}${arg ? ` ${arg}` : ""}`);
      switch (command.name) {
        case "stop":
          if (data.isRunning) stop();
          else note("system", "Nothing is running.");
          break;
        case "close":
          data.close();
          break;
        case "graph":
          setGraph(!graphOpen);
          break;
        case "settings":
          setGearSignal((n) => n + 1);
          break;
        case "spawn":
          note("system", SPAWN_SOON);
          break;
        case "help":
          note("system", helpText());
          break;
      }
    },
    [data, stop, graphOpen, setGraph, note],
  );

  return (
    <div className={`shell ${overlay ? "is-narrow" : ""}`}>
      <ResumeRail
        activeId={sessionId}
        sessions={sessions}
        error={listError}
      />

      <main className="work">
        <SessionHeader
          session={session}
          mode={mode}
          phase={data.phase}
          elapsed={elapsed}
          liveSteps={view.calls}
          running={data.isRunning}
          stopping={data.isStopping}
          graphOpen={graphOpen}
          onToggleGraph={() => setGraph(!graphOpen)}
          onStop={stop}
          prefs={prefs}
          gearSignal={gearSignal}
          onPrefs={(next) => {
            setPrefs(next);
            savePrefs(next);
          }}
        />

        <div className="work-body">
          <div className="work-talk">
            <Conversation
              sessionId={sessionId}
              session={session}
              chat={chat}
              groups={view.groups}
              stepsByTurn={view.stepsByTurn}
              edited={view.editedPaths}
              selectedTurnId={view.selectedTurnId}
              currentTurnId={session?.current_turn_id ?? null}
              onSelectTurn={view.pickTurn}
              cutoff={view.cutoff}
              hereStep={view.hereStep}
              running={data.isRunning}
              stopping={data.isStopping}
              steeringQueued={data.steeringQueued}
              phase={data.phase}
              now={data.now}
              locked={COMPOSER_LOCKED.has(status)}
              lockedReason={lockedReason(status, data.phase, data.failureError)}
              sendError={data.loadError ?? data.sendError}
              gtError={data.gtError}
              failureError={data.failureError}
              pendingFirst={pendingFirst}
              notes={notes}
              focusSignal={focusSignal}
              onSend={data.send}
              onCommand={onCommand}
              onStop={stop}
              onContinue={() => void data.send("continue")}
              onPickFile={selectPath}
            />
          </div>

          {graphOpen && (
            <>
              {overlay ? (
                <div
                  className="scrim is-graph"
                  role="presentation"
                  onClick={() => setGraph(false)}
                />
              ) : (
                <div
                  className="grip is-x"
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="Resize the graph"
                  {...width.handlers}
                />
              )}
              <div
                className="work-graph"
                style={overlay ? undefined : { width: width.size }}
              >
                <GraphPanel
                  sessionId={sessionId}
                  session={session}
                  data={data}
                  view={view}
                  selectedId={selectedId}
                  inspectedId={inspectedId}
                  pinned={pinned}
                  onSelect={select}
                  onSelectPath={(path) => select(particleId(path))}
                  onTogglePin={() => setPinned(!pinned)}
                  onCloseInspector={closeInspector}
                  onCollapse={() => setGraph(false)}
                />
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

function lockedReason(
  status: string,
  phase: string | null,
  failureError: string | null,
): string {
  switch (status) {
    case "creating":
      switch (phase) {
        case "cloning":
          return "Cloning the repository…";
        case "sandbox_starting":
          return "Starting the sandbox…";
        case "sandbox_ready":
          return "Sandbox ready — indexing next…";
        case "indexing":
          return "Indexing the workspace…";
        default:
          return "Preparing the workspace…";
      }
    case "closed":
      return "This session is closed.";
    case "failed":
      return failedReason(failureError);
    default:
      return "";
  }
}
