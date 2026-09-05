import { useEffect, useRef, useState } from "react";
import type { Receipt, SessionDiff } from "../api";
import type { TrailStep } from "../trail";
import ChangesPanel from "./ChangesPanel";
import ReceiptsPanel from "./ReceiptsPanel";
import TrailPanel from "./TrailPanel";

const TABS = ["trail", "changes", "receipts"] as const;
type TabId = (typeof TABS)[number];

interface Props {
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
  const [tab, setTab] = useState<TabId>("trail");
  const chosen = useRef(false);

  // While the agent is walking, the steps are what matter — unless the
  // reader has already said otherwise.
  useEffect(() => {
    if (running && !chosen.current) setTab("trail");
  }, [running]);

  const count = (id: TabId): string => {
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
              chosen.current = true;
              setTab(id);
              // Picking a tab on a collapsed strip is a request to read it.
              if (collapsed) onExpand?.();
            }}
          >
            {id}
            <span className="panel-tab-n">{count(id)}</span>
          </button>
        ))}
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
