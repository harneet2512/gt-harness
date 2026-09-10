import { useState, useEffect, useRef } from "react";
import type { Receipt, SessionDiff } from "../api";
import type { TrailStep } from "../trail";
import ChangesPanel from "./ChangesPanel";
import ReceiptsPanel from "./ReceiptsPanel";
import TrailPanel from "./TrailPanel";
import type { OutputRow } from "../sessionOutput";
import type { AgentVisualState } from "../cityAgents";

const TABS = ["output", "trail", "changes", "receipts"] as const;
type TabId = (typeof TABS)[number];

interface Props {
  outputRows?: readonly OutputRow[];
  requestedTab?: {tab: "changes" | "receipts" | "trail"; nonce:number} | null;
  agents?: readonly AgentVisualState[];
  /** The session shown; the changes tab's publish button hides without it. */
  sessionId?: string | null;
  steps: readonly TrailStep[];
  cutoff: number;
  hereStep: number | null;
  edited: ReadonlySet<string>;
  running: boolean;
  onPickFile: (path: string) => void;
  diff: SessionDiff | null;
  /** Set while scrubbing: the diff shown is an approximation, and says so. */
  diffNote: string | null;
  diffError: string | null;
  diffLoading: boolean;
  onRefreshDiff: () => void;
  receipts: readonly Receipt[];
  receiptsError: string | null;
  receiptsLoading: boolean;
  onRefreshReceipts: () => void;
  /**
   * Collapsed to its tab strip. A narrow screen opens this way: the tabs
   * say what is down here without spending a third of the viewport on it,
   * and picking one opens the drawer.
   */
  collapsed?: boolean;
  onExpand?: (() => void) | null;
  onCollapse?: (() => void) | null;
}

/** The IDE drawer under the graph: steps, changes, receipts. */
export default function BottomPanel({
  requestedTab,
  outputRows = [],
  agents = [],
  sessionId = null,
  steps,
  cutoff,
  hereStep,
  edited,
  running,
  onPickFile,
  diff,
  diffNote,
  diffError,
  diffLoading,
  onRefreshDiff,
  receipts,
  receiptsError,
  receiptsLoading,
  onRefreshReceipts,
  collapsed = false,
  onExpand = null,
  onCollapse = null,
}: Props) {
  /* Changes, not the trail: every step of the turn is already inline in the
     transcript, and a drawer that repeats it is a drawer worth nothing. The
     trail is still here — with the scrubber, which the transcript has not —
     one tab away. */
  const [tab, setTab] = useState<TabId>("output");

  useEffect(()=>{if(requestedTab)setTab(requestedTab.tab);},[requestedTab]);
  const [agentFilter,setAgentFilter]=useState("all");
  const [visibleRows,setVisibleRows]=useState(400);
  const outputRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (tab !== "output") return;
    const node = outputRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [tab, outputRows.length, agentFilter]);
  const count = (id: TabId): string => {
    if (id === "output") return outputRows.length > 0 ? String(outputRows.length) : "";
    if (id === "trail") return steps.length > 0 ? String(steps.length) : "";
    if (id === "changes") {
      return diff && diff.files.length > 0 ? String(diff.files.length) : "";
    }
    return receipts.length > 0 ? String(receipts.length) : "";
  };

  return (
    <div className={`panel ${collapsed ? "is-collapsed" : ""}`}>
      <div className="panel-tabs" role="tablist">
        {TABS.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={!collapsed && tab === id}
            className={`panel-tab ${
              !collapsed && tab === id ? "is-active" : ""
            }`}
            onClick={() => {
              setTab(id);
              // Picking a tab on a collapsed strip is a request to read it.
              if (collapsed) onExpand?.();
            }}
          >
            {id === "output" ? "Agent output" : id}
            <span className="panel-tab-n">{count(id)}</span>
          </button>
        ))}
        {tab==="output" && <select className="output-agent-filter" aria-label="Filter output by agent" value={agentFilter} onChange={e=>setAgentFilter(e.target.value)}><option value="all">All agents</option>{agents.map(a=><option key={a.id} value={a.id}>{a.label}</option>)}</select>}
        {(onExpand || onCollapse) && (
          <>
            <span className="spacer" />
            <button
              type="button"
              className="btn-text panel-fold"
              aria-expanded={!collapsed}
              aria-label={collapsed ? "Open the panel" : "Collapse the panel"}
              title={collapsed ? "Open the panel" : "Collapse the panel"}
              onClick={() => (collapsed ? onExpand?.() : onCollapse?.())}
            >
              <span aria-hidden="true">{collapsed ? "▴" : "▾"}</span>
            </button>
          </>
        )}
      </div>

      {!collapsed && (
        <div className="panel-body">
          {tab === "output" && (
            <div className="city-output" ref={outputRef} aria-live="polite" aria-label="Live agent terminal output">
              {outputRows.filter(row=>agentFilter==="all"||row.agentId===agentFilter).slice(-visibleRows).map(row=>{
                const agent=agents.find(a=>a.id===row.agentId);
                return <div className={`output-event ${row.error?"is-error":""}`} key={row.id}><time>{new Date(row.timestamp*1000).toLocaleTimeString([], {hour12:false})}</time><span className="output-identity" style={{color:agent?.color??"#8cb8f4"}}>[{agent?.label??row.agentId}]</span><pre>{row.text}</pre></div>;
              })}
              {outputRows.length===0 && <p className="output-empty">Agent output will appear here as commands run.</p>}
              {outputRows.length>visibleRows && <button className="output-older" onClick={()=>setVisibleRows(n=>n+400)}>Show earlier output</button>}
            </div>
          )}
          {tab === "trail" && (
            <TrailPanel
              steps={steps}
              cutoff={cutoff}
              hereStep={hereStep}
              edited={edited}
              running={running}
              onPickFile={onPickFile}
            />
          )}
          {tab === "changes" && (
            <ChangesPanel
              diff={diff}
              note={diffNote}
              error={diffError}
              loading={diffLoading}
              sessionId={sessionId}
              onRefresh={onRefreshDiff}
              onPickFile={onPickFile}
            />
          )}
          {tab === "receipts" && (
            <ReceiptsPanel
              receipts={receipts}
              error={receiptsError}
              loading={receiptsLoading}
              onRefresh={onRefreshReceipts}
            />
          )}
        </div>
      )}
    </div>
  );
}
