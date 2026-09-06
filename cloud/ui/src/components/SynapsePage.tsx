import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { COMPOSER_LOCKED, isWorker } from "../api";
import { failedReason, repoShort } from "../format";
import { shouldAutoOpenGraph, turnFileCount } from "../launch";
import { isOverlayMode, useLayoutMode } from "../layoutMode";
import { refreshPalette } from "../palette";
import { loadGraphOpen, loadPrefs, savePrefs, saveGraphOpen, type Prefs } from "../prefs";
import { helpText, parseSpawn, type ParsedSlash } from "../slash";
import { applyTheme, loadTheme, saveTheme, themeFromArg, type Theme } from "../theme";
import { useDragSize } from "../useDragSize";
import { useGraphView } from "../useGraphView";
import { useSessionData } from "../useSessionData";
import { useSessions } from "../useSessions";
import { workerList } from "../workers";
import Conversation, { type LocalNote } from "./Conversation";
import GraphPanel from "./GraphPanel";
import ResumePicker from "./ResumePicker";

const GRAPH_DEFAULT = 620;
const GRAPH_MIN = 360;
const GRAPH_MAX = 1000;

/** The split, as a column of box-drawing characters. */
const SPLIT_BAR = Array.from({ length: 400 }, () => "│").join("\n");

/** GT Cloud Agent — a terminal, with the graph a keystroke away. */
export default function SynapsePage() {
  const { id } = useParams<{ id: string }>();
  const sessionId = id ?? null;
  const location = useLocation();
  const data = useSessionData(sessionId);
  const { session, chat, graph, diff } = data;
  const { sessions } = useSessions();

  const view = useGraphView({
    sessionId,
    chat,
    tree: data.tree,
    graph,
    diff,
    currentTurnId: session?.current_turn_id ?? null,
    turnEpoch: data.turnEpoch,
  });

  /* ---- the first message, typed on the landing page ---- */
  const launched =
    (location.state as
      | { firstMessage?: string; alreadySent?: boolean; spawnTasks?: string[] }
      | null) ?? null;
  const firstMessage = launched?.firstMessage ?? null;
  const [pendingFirst, setPendingFirst] = useState<string | null>(firstMessage);
  /* `SessionCreate.first_message` means the server already has the prompt and
     starts the turn itself, so nothing is posted from here. The copy above is
     on screen only so the wait belongs to the prompt rather than to a blank
     page; the fallback path — a server that would not take the field — is the
     one that still sends it at `idle`. */
  const sentFirst = useRef(launched?.alreadySent === true);
  /* `/spawn` typed on the landing page: the server refuses a spawn while a
     session is `creating`, so the tasks wait here. */
  const [pendingSpawn, setPendingSpawn] = useState<readonly string[] | null>(
    launched?.spawnTasks ?? null,
  );

  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs());
  const [theme, setTheme] = useState<Theme>(() => loadTheme());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [focusSignal, setFocusSignal] = useState(0);
  const [notes, setNotes] = useState<readonly LocalNote[]>([]);
  const noteId = useRef(0);

  const layout = useLayoutMode();
  const overlay = isOverlayMode(layout);
  const width = useDragSize(GRAPH_DEFAULT, GRAPH_MIN, GRAPH_MAX, "x-left");

  /* ---- the graph pane ---- */
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

  const workers = useMemo(() => workerList(chat.workers), [chat.workers]);

  useEffect(() => {
    if (autoDone.current || graphOpen) return;
    if (!shouldAutoOpenGraph(touched)) return;
    autoDone.current = true;
    setGraphOpen(true);
  }, [touched, graphOpen]);

  /* A worker is a second trail on the same map, and the map is the only
     place two of them can be watched at once: the pane opens itself the
     moment the first one exists. An explicit choice still wins. */
  useEffect(() => {
    if (autoDone.current || graphOpen || workers.length === 0) return;
    setGraphOpen(true);
  }, [workers.length, graphOpen]);

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

  /* ---- deliver the landing page's prompt ---- */
  const send = data.send;
  useEffect(() => {
    if (!pendingFirst) return;
    if (sentFirst.current) {
      // The real message is in the thread now; never show the prompt twice.
      if (chat.messages.some((m) => m.content === pendingFirst)) {
        setPendingFirst(null);
      }
      return;
    }
    if (status !== "idle") return;
    sentFirst.current = true;
    const content = pendingFirst;
    setPendingFirst(null);
    void send(content);
  }, [pendingFirst, status, send, chat.messages]);

  const note = useCallback((role: LocalNote["role"], text: string) => {
    setNotes((prev) => [...prev, { id: (noteId.current += 1), role, text }]);
  }, []);

  useEffect(() => {
    if (!pendingSpawn || status !== "idle") return;
    const tasks = pendingSpawn;
    setPendingSpawn(null);
    note("user", tasks.map((task) => `/spawn ${task}`).join("\n"));
    void data.spawn(tasks).then((error) => {
      if (error) note("system", error);
    });
  }, [pendingSpawn, status, data, note]);

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
      if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        setResumeOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stop, graphOpen, setGraph]);

  const onCommand = useCallback(
    ({ command, arg, raw }: ParsedSlash) => {
      /* The command is echoed the way a shell echoes it: what you typed
         stays in the transcript, above whatever it did. A multi-line
         `/spawn` is echoed whole — each line is a worker. */
      note(
        "user",
        command.name === "spawn" ? raw : `/${command.name}${arg ? ` ${arg}` : ""}`,
      );
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
          setSettingsOpen(true);
          break;
        case "resume":
          setResumeOpen(true);
          break;
        case "theme": {
          const next = themeFromArg(arg, theme);
          setTheme(next);
          applyTheme(next);
          saveTheme(next);
          refreshPalette();
          note("system", `theme: ${next}`);
          break;
        }
        case "spawn": {
          const draft = parseSpawn(raw);
          if (draft.error) {
            note("system", draft.error);
            break;
          }
          void data.spawn(draft.tasks).then((error) => {
            if (error) note("system", error);
          });
          break;
        }
        case "help":
          note("system", helpText());
          break;
      }
    },
    [data, stop, graphOpen, setGraph, note, theme],
  );

  const worker = session ? isWorker(session) : false;

  return (
    <div className={`shell ${overlay ? "is-narrow" : ""}`}>
      <main className="work">
        {worker && session?.parent_id && (
          <header className="head">
            <Link className="head-back" to={`/sessions/${session.parent_id}`}>
              ← back to parent
            </Link>
            <span className="dim">
              {"  "}worker of {repoShort(session.repo)}
            </span>
          </header>
        )}

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
              workers={workers}
              canApply={status === "idle"}
              onApplyWorker={(workerId) => void data.applyWorker(workerId)}
              settingsOpen={settingsOpen}
              prefs={prefs}
              onPrefs={(next) => {
                setPrefs(next);
                savePrefs(next);
              }}
              onCloseSettings={() => setSettingsOpen(false)}
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
              {overlay ? null : (
                <div
                  className="split"
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="Resize the graph"
                  {...width.handlers}
                >
                  {SPLIT_BAR}
                </div>
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

      {resumeOpen && (
        <ResumePicker
          sessions={sessions}
          activeId={sessionId}
          onClose={() => setResumeOpen(false)}
        />
      )}
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
          return "cloning the repository…";
        case "sandbox_starting":
          return "starting the sandbox…";
        case "sandbox_ready":
          return "sandbox ready — indexing next…";
        case "indexing":
          return "indexing the workspace…";
        default:
          return "preparing the workspace…";
      }
    case "closed":
      return "this session is closed";
    case "failed":
      return failedReason(failureError);
    default:
      return "";
  }
}
