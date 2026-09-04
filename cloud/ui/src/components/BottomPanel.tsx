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
  diffError: string | null;
  diffLoading: boolean;
  onRefreshDiff: () => void;
  receipts: readonly Receipt[];
  receiptsError: string | null;
  receiptsLoading: boolean;
  onRefreshReceipts: () => void;
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
  diffError,
  diffLoading,
  onRefreshDiff,
  receipts,
  receiptsError,
  receiptsLoading,
  onRefreshReceipts,
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
    <div className="panel">
      <div className="panel-tabs" role="tablist">
        {TABS.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={`panel-tab ${tab === id ? "is-active" : ""}`}
            onClick={() => {
              chosen.current = true;
              setTab(id);
            }}
          >
            {id}
            <span className="panel-tab-n">{count(id)}</span>
          </button>
        ))}
      </div>

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
    </div>
  );
}
