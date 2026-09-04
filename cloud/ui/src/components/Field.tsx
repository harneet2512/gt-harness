import { useEffect, useRef, useState } from "react";
import type { Receipt, SessionDiff, SessionEvent, TreeFile } from "../api";
import type { Attention, SurveyStep, Waypoint } from "../survey";
import BearingsPanel from "./BearingsPanel";
import ChangesPanel from "./ChangesPanel";
import ReceiptsPanel from "./ReceiptsPanel";
import RepoMap from "./RepoMap";
import Scrubber from "./Scrubber";
import TrailPanel from "./TrailPanel";

const TABS = ["trail", "changes", "bearings", "receipts"] as const;
type TabId = (typeof TABS)[number];

const MIN_MAP = 25;
const MAX_MAP = 80;
const DEFAULT_MAP = 58;

export interface TurnOption {
  id: string;
  no: number;
}

interface Props {
  hidden: boolean;
  /* terrain */
  files: readonly TreeFile[];
  treeError: string | null;
  emptyText: string;
  attention: ReadonlyMap<string, Attention>;
  edited: ReadonlySet<string>;
  trail: readonly Waypoint[];
  position: string | null;
  hereStep: number | null;
  running: boolean;
  /* turn selection */
  turns: readonly TurnOption[];
  selectedTurnId: string | null;
  currentTurnId: string | null;
  onSelectTurn: (turnId: string) => void;
  /* replay */
  steps: readonly SurveyStep[];
  scrubPosition: number;
  live: boolean;
  onScrub: (position: number) => void;
  onLive: () => void;
  /* instruments */
  diff: SessionDiff | null;
  diffError: string | null;
  diffLoading: boolean;
  onRefreshDiff: () => void;
  receipts: readonly Receipt[];
  receiptsError: string | null;
  receiptsLoading: boolean;
  onRefreshReceipts: () => void;
  events: readonly SessionEvent[];
  gtStatus: string;
}

/** The right column: the map, the scrubber, and the instruments. */
export default function Field({
  hidden,
  files,
  treeError,
  emptyText,
  attention,
  edited,
  trail,
  position,
  hereStep,
  running,
  turns,
  selectedTurnId,
  currentTurnId,
  onSelectTurn,
  steps,
  scrubPosition,
  live,
  onScrub,
  onLive,
  diff,
  diffError,
  diffLoading,
  onRefreshDiff,
  receipts,
  receiptsError,
  receiptsLoading,
  onRefreshReceipts,
  events,
  gtStatus,
}: Props) {
  const [tab, setTab] = useState<TabId>("trail");
  const [search, setSearch] = useState("");
  const [focusPath, setFocusPath] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(
    () => new Set<string>(),
  );
  const [mapPct, setMapPct] = useState(DEFAULT_MAP);

  const pickedTab = useRef(false);
  const frame = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  // The trail is what matters while the surveyor is moving, unless the
  // reader has already said otherwise.
  useEffect(() => {
    if (running && !pickedTab.current) setTab("trail");
  }, [running]);

  const toggleDir = (path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const fit = () => {
    setSearch("");
    setFocusPath(null);
    setCollapsed(new Set());
  };

  const onDrag = (clientY: number) => {
    const box = frame.current?.getBoundingClientRect();
    if (!box || box.height < 80) return;
    const pct = ((clientY - box.top) / box.height) * 100;
    setMapPct(Math.min(MAX_MAP, Math.max(MIN_MAP, pct)));
  };

  return (
    <section className={`field ${hidden ? "is-hidden" : ""}`} ref={frame}>
      <div className="map-bar">
        <span className="cap">terrain</span>

        {turns.length > 0 && (
          <select
            aria-label="Turn shown on the map"
            value={selectedTurnId ?? ""}
            onChange={(e) => onSelectTurn(e.target.value)}
          >
            {turns.map((turn) => (
              <option key={turn.id} value={turn.id}>
                №{turn.no}
                {turn.id === currentTurnId ? " · live" : ""}
              </option>
            ))}
          </select>
        )}

        <span className="legend">
          <span className="legend-item">
            <span className="legend-swatch is-read" />
            <span className="cap">read</span>
          </span>
          <span className="legend-item">
            <span className="legend-swatch is-edit" />
            <span className="cap">edited</span>
          </span>
          <span className="legend-item">
            <span className="legend-swatch is-here" />
            <span className="cap">position</span>
          </span>
        </span>

        <span className="spacer" />

        <input
          type="search"
          value={search}
          placeholder="find a file"
          aria-label="Highlight files on the map"
          onChange={(e) => setSearch(e.target.value)}
        />
        <button type="button" className="btn-text" onClick={fit}>
          fit
        </button>
      </div>

      <div className="map-frame" style={{ height: `${mapPct}%` }}>
        <RepoMap
          files={files}
          emptyText={treeError ? "terrain unavailable" : emptyText}
          attention={attention}
          currentStep={scrubPosition}
          edited={edited}
          trail={trail}
          position={position}
          running={running}
          search={search}
          focusPath={focusPath}
          onPickFile={setFocusPath}
          collapsed={collapsed}
          onToggleDir={toggleDir}
        />
      </div>

      <div
        className="field-divider"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the map"
        onPointerDown={(e) => {
          dragging.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (dragging.current) onDrag(e.clientY);
        }}
        onPointerUp={(e) => {
          dragging.current = false;
          e.currentTarget.releasePointerCapture(e.pointerId);
        }}
      />

      <Scrubber
        steps={steps}
        edited={edited}
        position={scrubPosition}
        live={live}
        onScrub={onScrub}
        onLive={onLive}
      />

      <div className="instruments">
        <div className="inst-tabs" role="tablist">
          {TABS.map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              className={`inst-tab ${tab === id ? "is-active" : ""}`}
              onClick={() => {
                pickedTab.current = true;
                setTab(id);
              }}
            >
              {id}
              <span className="inst-tab-count">{countFor(id)}</span>
            </button>
          ))}

          {focusPath && (
            <span className="inst-filter">
              <span className="mono">{focusPath}</span>
              <button
                type="button"
                className="link"
                onClick={() => setFocusPath(null)}
              >
                clear
              </button>
            </span>
          )}
        </div>

        <div className="inst-body">
          {tab === "trail" && (
            <TrailPanel
              steps={steps}
              cutoff={scrubPosition}
              hereStep={hereStep}
              edited={edited}
              focusPath={focusPath}
              onPickFile={setFocusPath}
              running={running}
            />
          )}
          {tab === "changes" && (
            <ChangesPanel
              diff={diff}
              error={diffError}
              loading={diffLoading}
              onRefresh={onRefreshDiff}
              onPickFile={setFocusPath}
            />
          )}
          {tab === "bearings" && (
            <BearingsPanel
              steps={steps}
              cutoff={scrubPosition}
              events={events}
              gtStatus={gtStatus}
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
    </section>
  );

  function countFor(id: TabId): string {
    switch (id) {
      case "trail":
        return steps.length > 0 ? String(steps.length) : "";
      case "changes":
        return diff && diff.files.length > 0 ? String(diff.files.length) : "";
      case "receipts":
        return receipts.length > 0 ? String(receipts.length) : "";
      default:
        return "";
    }
  }
}
