import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { COMPOSER_LOCKED } from "../api";
import { relationsFor } from "../graph";
import { useDragSize } from "../useDragSize";
import { useGraphView } from "../useGraphView";
import { useSessionData } from "../useSessionData";
import BottomPanel from "./BottomPanel";
import Conversation from "./Conversation";
import GraphCanvas from "./GraphCanvas";
import GraphToolbar, { type TurnOption } from "./GraphToolbar";
import Inspector from "./Inspector";
import Scrubber from "./Scrubber";
import type { StatusMode } from "./StatusLine";

const LEFT_DEFAULT = 400;
const LEFT_MIN = 340;
const LEFT_MAX = 640;
const PANEL_DEFAULT = 220;
const PANEL_MIN = 120;
const PANEL_MAX = 560;

/** SYNAPSE — the conversation, the particle graph, and what it is changing. */
export default function SynapsePage() {
  const { id } = useParams<{ id: string }>();
  const sessionId = id ?? null;
  const data = useSessionData(sessionId);
  const { session, chat, graph, diff } = data;

  const view = useGraphView({
    chat,
    tree: data.tree,
    graph,
    diff,
    currentTurnId: session?.current_turn_id ?? null,
    turnEpoch: data.turnEpoch,
  });

  /* ---- layout ---- */
  const left = useDragSize(LEFT_DEFAULT, LEFT_MIN, LEFT_MAX, "x");
  const panel = useDragSize(PANEL_DEFAULT, PANEL_MIN, PANEL_MAX, "y");
  const [panelOpen, setPanelOpen] = useState(true);
  const [labels, setLabels] = useState(false);
  const [fitToken, setFitToken] = useState(0);
  const [zoomK, setZoomK] = useState(1);
  const [search, setSearch] = useState("");

  /* ---- search ---- */
  const matches = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return null;
    const out = new Set<string>();
    for (const particle of view.field.particles) {
      if (particle.path.toLowerCase().includes(query)) out.add(particle.id);
    }
    return out;
  }, [search, view.field]);

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
    (path: string) => select(particleId(path)),
    [select, particleId],
  );

  const inspected = inspectedId
    ? (view.field.byId.get(inspectedId) ?? null)
    : null;
  const inspectedPath = inspected?.path ?? "";

  const inspectedCotouch = useMemo(() => {
    if (!inspectedPath) return [];
    const out: string[] = [];
    for (const key of view.cotouch) {
      const [a, b] = key.split(" ");
      if (a === inspectedPath) out.push(b);
      else if (b === inspectedPath) out.push(a);
    }
    return out;
  }, [view.cotouch, inspectedPath]);

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

  /* ---- Ctrl/Cmd+Shift+Backspace stops the turn ---- */
  const stop = data.stop;
  useEffect(() => {
    if (!data.isRunning) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Backspace" || !e.shiftKey) return;
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [data.isRunning, stop]);

  const turnOptions: TurnOption[] = useMemo(
    () => view.turnIds.map((turnId, i) => ({ id: turnId, no: i + 1 })),
    [view.turnIds],
  );

  return (
    <div className="synapse">
      <div className="synapse-left" style={{ width: left.size }}>
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
          mode={mode}
          phase={data.phase}
          elapsed={elapsed}
          liveSteps={view.steps.length}
          now={data.now}
          locked={COMPOSER_LOCKED.has(status)}
          lockedReason={lockedReason(status, data.phase)}
          sendError={data.loadError ?? data.sendError}
          onSend={data.send}
          onStop={data.stop}
          onContinue={() => void data.send("continue")}
        />
      </div>

      <div
        className="grip is-x"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the conversation"
        {...left.handlers}
      />

      <div className="synapse-main">
        <GraphToolbar
          turns={turnOptions}
          selectedTurnId={view.selectedTurnId}
          currentTurnId={session?.current_turn_id ?? null}
          onSelectTurn={view.pickTurn}
          search={search}
          onSearch={setSearch}
          onSearchEnter={() => {
            const first = matches ? [...matches][0] : undefined;
            if (first) select(first);
          }}
          matchCount={matches ? matches.size : null}
          zoom={zoomK}
          onFit={() => setFitToken((n) => n + 1)}
          labels={labels}
          onToggleLabels={() => setLabels(!labels)}
          panelOpen={panelOpen}
          onTogglePanel={() => setPanelOpen(!panelOpen)}
          gt={graph.gt}
          folded={view.field.folded}
        />

        <div className="synapse-stage">
          <GraphCanvas
            field={view.field}
            neighbours={view.neighbours}
            attention={view.attentionById}
            currentStep={view.cutoff}
            edited={view.editedById}
            positionId={view.positionId}
            running={data.isRunning}
            selectedId={selectedId}
            onSelect={select}
            matches={matches}
            labels={labels}
            trailIds={view.trailIds}
            trailToken={`${view.selectedTurnId ?? ""}|${
              view.live ? "live" : "scrub"
            }`}
            animate={
              view.live &&
              data.isRunning &&
              view.selectedTurnId === session?.current_turn_id
            }
            fitToken={fitToken}
            onZoom={setZoomK}
            emptyText={emptyText(sessionId, status, view.field.particles.length)}
          />
        </div>

        {panelOpen && (
          <div className="synapse-bottom" style={{ height: panel.size }}>
            <div
              className="grip is-y"
              role="separator"
              aria-orientation="horizontal"
              aria-label="Resize the panel"
              {...panel.handlers}
            />
            <Scrubber
              steps={view.steps}
              edited={view.editedPaths}
              position={view.cutoff}
              live={view.live}
              onScrub={view.setScrub}
              onLive={() => view.setScrub(null)}
            />
            <BottomPanel
              steps={view.steps}
              cutoff={view.cutoff}
              hereStep={view.hereStep}
              edited={view.editedPaths}
              running={data.isRunning}
              onPickFile={selectPath}
              diff={diff}
              diffError={data.diffError}
              diffLoading={data.diffLoading}
              onRefreshDiff={data.reloadDiff}
              receipts={data.receipts}
              receiptsError={data.receiptsError}
              receiptsLoading={data.receiptsLoading}
              onRefreshReceipts={data.reloadReceipts}
            />
          </div>
        )}
      </div>

      <Inspector
        particle={inspected}
        open={inspected !== null}
        pinned={pinned}
        onTogglePin={() => setPinned(!pinned)}
        onClose={() => {
          setPinned(false);
          setInspectedId(null);
          setSelectedId(null);
        }}
        diff={diff}
        diffFile={view.editedFiles.get(inspectedPath)}
        diffLoading={data.diffLoading}
        diffError={data.diffError}
        relations={relationsFor(view.relations, inspectedPath)}
        cotouch={inspectedCotouch}
        reads={view.attentionById.get(inspectedId ?? "")?.reads ?? 0}
        steps={view.steps}
        cutoff={view.cutoff}
        onScrubTo={(n) => view.setScrub(n >= view.steps.length ? null : n)}
        onPick={selectPath}
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
      return "This session is closed.";
    case "failed":
      return "This session failed.";
    default:
      return "";
  }
}

function emptyText(
  sessionId: string | null,
  status: string,
  particles: number,
): string | null {
  if (particles > 0) return null;
  if (!sessionId) return "pick a session";
  if (status === "creating") return "indexing…";
  return "no files indexed";
}
