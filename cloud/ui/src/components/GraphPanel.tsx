import { useEffect, useMemo, useState } from "react";
import type { AgentVisualState } from "../cityAgents";
import { Icon, type WorkspaceView } from "./WorkspaceChrome";
import LiveAgentInspector from "./LiveAgentInspector";
import { sessionOutput } from "../sessionOutput";
import { repoShort } from "../format";

import { agentMatches, focusAgent } from "../agentField";
import type { Session } from "../api";
import { relationsFor } from "../graph";
import type { GraphMode } from "../prefs";
import { useDragSize } from "../useDragSize";
import type { GraphView } from "../useGraphView";
import type { SessionData } from "../useSessionData";

import BottomPanel from "./BottomPanel";
import GraphStage from "./GraphStage";

import Inspector from "./Inspector";
import Scrubber from "./Scrubber";

const PANEL_DEFAULT = 230;
const PANEL_MIN = 120;
const PANEL_MAX = 520;

interface Props {
  agents: readonly AgentVisualState[];
  selectedAgent: string | null;
  onSelectedAgent: (id: string | null) => void;
  workspaceView: WorkspaceView;
  onWorkspaceView: (v: WorkspaceView) => void;
  outputRequest: {tab:"changes"|"receipts"|"trail";nonce:number}|null;
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
  /** The agent the map is narrowed to, or null for everything at once. */
  isolated: string | null;
  onIsolate: (agentId: string | null) => void;
  /** Flat or in depth, remembered in `prefs` alongside the others. */
  mode: GraphMode;
  onMode: (mode: GraphMode) => void;
  onCollapse: () => void;
  onInspect?: () => void;
}

/**
 * The graph, and everything that reads off it: the inspector beside the
 * canvas, the trail/changes/receipts drawer under it. A panel now, not a
 * column — the conversation is the page.
 */
export default function GraphPanel({
  agents, selectedAgent, onSelectedAgent: setSelectedAgent, workspaceView, onWorkspaceView, outputRequest,
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
  isolated,
  onIsolate: _onIsolate,
  mode,
  onMode,
  onCollapse: _onCollapse,
  onInspect,
}: Props) {
  const panel = useDragSize(PANEL_DEFAULT, PANEL_MIN, PANEL_MAX, "y");
  const [panelOpen, setPanelOpen] = useState(true);
  const labels = false;
  const [fitToken, setFitToken] = useState(0);
  const [, setZoomK] = useState(1);
  const [search, setSearch] = useState("");
  const [followedAgent, setFollowedAgent] = useState<string | null>(null);
  useEffect(()=>{if(outputRequest)setPanelOpen(true);},[outputRequest]);
  const agent = agents.find((a) => a.id === selectedAgent);
  const selectFile = (id: string | null) => {
    setSelectedAgent(null);
    onSelect(id);
    if (id) onInspect?.();
  };
  const selectAgent = (id: string) => {
    onCloseInspector();
    setSelectedAgent(id);
    onInspect?.();
  };
  useEffect(() => {
    if (inspectedId) setSelectedAgent(null);
  }, [inspectedId]);
  useEffect(() => {
    if (followedAgent && !agents.some((a) => a.id === followedAgent))
      setFollowedAgent(null);
  }, [agents, followedAgent]);
  /* Pointing at a legend chip focuses that agent; clicking pins it. A
     hover is a look, so it never survives the pointer leaving, and it
     never overrides something the reader deliberately isolated. */
  const hovered: string | null = null;
  const focused = focusAgent(isolated, hovered);

  const matches = useMemo(() => {
    const agent = agentMatches(view.workerTrails, isolated, hovered);
    if (agent) return agent;
    const query = search.trim().toLowerCase();
    if (!query) return null;
    const out = new Set<string>();
    for (const particle of view.field.particles) {
      if (particle.path.toLowerCase().includes(query)) out.add(particle.id);
    }
    return out;
  }, [search, view.field, isolated, hovered, view.workerTrails]);

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

  const outputRows=useMemo(()=>sessionOutput(data.chat.events,view.selectedTurnId,view.steps[view.cutoff]?.eventId ? view.steps[view.cutoff].eventId-1 : Infinity,view.live),[data.chat.events,view.selectedTurnId,view.steps,view.cutoff,view.live]);
  const status = String(session?.status ?? (sessionId ? "creating" : "idle"));

  return (
    <aside className={`gpanel workspace-${workspaceView.toLowerCase()}`} aria-label="Live repository workspace">
      <header className="repository-heading">
        <div><h1>{session ? repoShort(session.repo) : "Repository workspace"}</h1><p>{session?.last_message || "Your code, your agents, one live workspace."}</p>
          <div className="repository-facts"><span><Icon name="file" size={14}/>{data.graph.nodes.length.toLocaleString()} files</span><span><Icon name="layers" size={14}/>{new Set(data.graph.nodes.map(n=>n.dir)).size} modules</span><span><Icon name="graph" size={14}/>{data.graph.edges.length.toLocaleString()} relations</span><span><Icon name="branch" size={14}/>{session?.ref}</span></div>
        </div>
        <nav className="workspace-segments" aria-label="Repository presentation">{(["Structure","Flow","Agents"] as const).map(v=><button key={v} aria-pressed={workspaceView===v} onClick={()=>onWorkspaceView(v)}>{v}</button>)}</nav>
      </header>
      <div className="workspace-tools">
        <label className="workspace-file-search"><Icon name="search" size={14}/><input aria-label="Find files by path" placeholder="Find a file…" value={search} onChange={e=>setSearch(e.target.value)} onKeyDown={e=>{if(e.key==="Enter" && matches?.size)selectFile([...matches][0]);}}/></label>
        {view.turnIds.length>0 && <select aria-label="Replay turn" value={view.selectedTurnId??""} onChange={e=>view.pickTurn(e.target.value)}>{view.turnIds.map((id,i)=><option key={id} value={id}>Turn {i+1}{id===session?.current_turn_id?" · current":""}</option>)}</select>}
        <span className="workspace-data-state">{view.live?"Live workspace":"Replay"}{data.graph.truncated?" · graph truncated":""}</span>
        <button aria-label="Fit repository" onClick={()=>setFitToken(n=>n+1)}><Icon name="target" size={15}/></button>
        <button aria-pressed={mode==="2d"} onClick={()=>onMode(mode==="3d"?"2d":"3d")}>{mode==="3d"?"2D view":"3D view"}</button>
        <button aria-expanded={panelOpen} onClick={()=>setPanelOpen(v=>!v)}><Icon name="log" size={15}/><span>Output</span></button>
      </div>
      <div className="gpanel-row">
        <div className="gpanel-stage">
          {search.trim() && <div className="workspace-search-results" role="list" aria-label="Matching files">{[...(matches??[])].slice(0,50).map(id=><button role="listitem" key={id} onClick={()=>{selectFile(id);setSearch("");}}><Icon name="file" size={14}/>{view.field.byId.get(id)?.path}</button>)}{matches?.size===0 && <p>No matching files.</p>}{(matches?.size??0)>50 && <p>Showing 50 of {matches?.size} matches. Refine your search.</p>}</div>}
          {workspaceView === "Flow" && <div className="workspace-scene-note">Select a file to explore its direct relationships.</div>}
          {workspaceView === "Agents" && <div className="workspace-agent-cards">{agents.map(a=><button key={a.id} onClick={()=>selectAgent(a.id)}><i style={{background:a.color}}/>{a.label}<small>{a.activity}</small></button>)}</div>}

          <GraphStage
            mode={mode}
            sessionId={sessionId}
            field={view.field}
            neighbours={view.neighbours}
            attention={view.attentionById}
            pulses={view.pulses}
            currentStep={view.cutoff}
            edited={view.editedById}
            positionId={view.positionId}
            running={data.isRunning}
            selectedId={selectedId}
            onSelect={selectFile}
            agents={agents}
            onSelectAgent={selectAgent}
            followAgent={followedAgent}
            matches={matches}
            labels={labels}
            trailIds={view.trailIds}
            showAllRelations={workspaceView === "Flow"}
            workerTrails={view.workerTrails}
            presence={view.agentPresence}
            focusAgent={focused}
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
            emptyText={emptyText(
              sessionId,
              status,
              view.field.particles.length,
            )}
          />
        </div>

        {!inspected && <LiveAgentInspector agent={agent??null} agents={agents} view={view} data={data} followedAgent={followedAgent}
          onSelectAgent={selectAgent} onClose={()=>setSelectedAgent(null)} onFollow={id=>setFollowedAgent(followedAgent===id?null:id)}
          onPick={path=>{setSelectedAgent(null);onSelectPath(path);}} />}
        <Inspector
          agents={agents.filter(a=>a.fileId===inspectedId)}
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
            aria-valuemin={PANEL_MIN}
            aria-valuemax={PANEL_MAX}
            aria-valuenow={panel.size}
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
            outputRows={outputRows}
            sessionId={sessionId}
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
            agents={agents}
            requestedTab={outputRequest}
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
