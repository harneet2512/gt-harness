import { useEffect, useMemo, useState } from "react";
import type { Session } from "../api";
import { relationsFor } from "../graph";
import { useDragSize } from "../useDragSize";
import type { GraphView } from "../useGraphView";
import type { SessionData } from "../useSessionData";
import { Rule } from "./Box";
import BottomPanel from "./BottomPanel";
import GraphCanvas from "./GraphCanvas";
import GraphToolbar, { type TurnOption } from "./GraphToolbar";
import Inspector from "./Inspector";
import Scrubber from "./Scrubber";

const PANEL_DEFAULT = 200;
const PANEL_MIN = 120;
const PANEL_MAX = 520;

interface Props {
  sessionId: string | null;
  session: Session | null;
  data: SessionData;
  view: GraphView;
  selectedId: string | null;
  inspectedId: string | null;
  pinned: boolean;
  onSelect: (id: string | null) => void;
  onSelectPath: (path: string) => void;
  onTogglePin: () => void;
  onCloseInspector: () => void;
  onCollapse: () => void;
}

/**
 * The graph, and everything that reads off it: the inspector beside the
 * canvas, the trail/changes/receipts drawer under it. A panel now, not a
 * column — the conversation is the page.
 */
export default function GraphPanel({
  sessionId,
  session,
  data,
  view,
  selectedId,
  inspectedId,
  pinned,
  onSelect,
  onSelectPath,
  onTogglePin,
  onCloseInspector,
  onCollapse,
}: Props) {
  const panel = useDragSize(PANEL_DEFAULT, PANEL_MIN, PANEL_MAX, "y");
  const [panelOpen, setPanelOpen] = useState(true);
  const [labels, setLabels] = useState(false);
  const [fitToken, setFitToken] = useState(0);
  const [zoomK, setZoomK] = useState(1);
  const [search, setSearch] = useState("");
  const [isolated, setIsolated] = useState<string | null>(null);

  /* A worker that no longer exists cannot be the thing the map is narrowed
     to, or the canvas would dim every particle and show nothing. */
  const workerIds = view.workerTrails.map((worker) => worker.id).join("|");
  useEffect(() => {
    if (isolated && !workerIds.split("|").includes(isolated)) setIsolated(null);
  }, [workerIds, isolated]);

  const matches = useMemo(() => {
    if (isolated) {
      const worker = view.workerTrails.find((w) => w.id === isolated);
      if (worker) return worker.ids;
    }
    const query = search.trim().toLowerCase();
    if (!query) return null;
    const out = new Set<string>();
    for (const particle of view.field.particles) {
      if (particle.path.toLowerCase().includes(query)) out.add(particle.id);
    }
    return out;
  }, [search, view.field, isolated, view.workerTrails]);

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

  const turnOptions: TurnOption[] = useMemo(
    () => view.turnIds.map((turnId, i) => ({ id: turnId, no: i + 1 })),
    [view.turnIds],
  );

  const status = String(session?.status ?? (sessionId ? "creating" : "idle"));

  return (
    <aside className="gpanel" aria-label="Graph">
      {/* A pane title, the way tmux writes one. */}
      <div className="panetitle">
        <Rule />
        <span>
          {" graph · "}
          {view.field.particles.length} files
          {data.graph.gt ? " · GT ready" : ""}
          {view.workerTrails.length > 0
            ? ` · ${view.workerTrails.length} worker${
                view.workerTrails.length === 1 ? "" : "s"
              }`
            : ""}
          {" "}
        </span>
        <Rule />
      </div>

      <GraphToolbar
        turns={turnOptions}
        selectedTurnId={view.selectedTurnId}
        currentTurnId={session?.current_turn_id ?? null}
        onSelectTurn={view.pickTurn}
        search={search}
        onSearch={setSearch}
        onSearchEnter={() => {
          const first = matches ? [...matches][0] : undefined;
          if (first) onSelect(first);
        }}
        matchCount={matches ? matches.size : null}
        zoom={zoomK}
        onFit={() => setFitToken((n) => n + 1)}
        labels={labels}
        onToggleLabels={() => setLabels(!labels)}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen(!panelOpen)}
        gt={data.graph.gt}
        folded={view.field.folded}
        workers={view.workerTrails}
        isolated={isolated}
        onIsolate={setIsolated}
        onCollapse={onCollapse}
      />

      <div className="gpanel-row">
        <div className="gpanel-stage">
          <GraphCanvas
            sessionId={sessionId}
            field={view.field}
            neighbours={view.neighbours}
            attention={view.attentionById}
            currentStep={view.cutoff}
            edited={view.editedById}
            positionId={view.positionId}
            running={data.isRunning}
            selectedId={selectedId}
            onSelect={onSelect}
            matches={matches}
            labels={labels}
            trailIds={view.trailIds}
            workerTrails={view.workerTrails}
            animateWorkers={view.live}
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

        <Inspector
          particle={inspected}
          open={inspected !== null}
          pinned={pinned}
          onTogglePin={onTogglePin}
          onClose={onCloseInspector}
          diff={view.diffAtCutoff}
          diffFile={view.editedAtCutoff.get(inspectedPath)}
          diffNote={view.diffNote}
          diffLoading={data.diffLoading}
          diffError={data.diffError}
          relations={relationsFor(view.relations, inspectedPath)}
          cotouch={inspectedCotouch}
          reads={view.attentionById.get(inspectedId ?? "")?.reads ?? 0}
          steps={view.steps}
          cutoff={view.cutoff}
          onScrubTo={(n) => view.setScrub(n >= view.steps.length ? null : n)}
          onPick={onSelectPath}
        />
      </div>

      {panelOpen && (
        <div className="gpanel-bottom" style={{ height: panel.size }}>
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
            calls={view.calls}
            hereCall={view.hereCall}
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
            onPickFile={onSelectPath}
            diff={view.diffAtCutoff}
            diffNote={view.diffNote}
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
    </aside>
  );
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
